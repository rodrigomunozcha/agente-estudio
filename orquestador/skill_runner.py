"""
Invoca la skill transcripciones-a-conocimiento via Claude Agent SDK, con el
ramo y el alcance ya resueltos por el orquestador, para que no se detenga a
preguntar nada de eso. aplicar_skill hace de una pasada todo el trabajo que
"requiere criterio" (limpieza, destilado, preguntas y respuestas modelo,
titulo, los 5 conceptos mas repetidos, y guardado en el vault de Obsidian).

No es la unica llamada al SDK de la clase, aunque si la unica que escribe las
notas desde cero. Una clase con hallazgos usa tres: esta, el revisor (sesion
aparte a proposito, ver revisor.py) y la correccion de mas abajo, que retoma
la sesion de esta con `resume` para no volver a pagar la lectura de la
transcripcion.

Antes la escritura misma eran 3 invocaciones separadas (titulo, skill,
conceptos repetidos). Cada invocacion del SDK paga un overhead fijo de
~16.000 tokens de system prompt (medido en vivo) antes de leer nada, ademas
de releer la transcripcion completa cada vez. Fusionarlas en una sola ahorra
ese costo dos veces por clase. Antes de agregar una cuarta llamada, mide que
ahorra.

La skill vive en .claude/skills/transcripciones-a-conocimiento dentro de
este proyecto (copiada del plugin original y adaptada para invocacion
automatizada, ver el SKILL.md). El SDK la descubre porque cwd apunta a la
raiz del proyecto y setting_sources incluye "project" (no hace falta "user":
verificado en vivo que la skill se sigue encontrando igual).

Ojo con lo que arrastra ese "project", medido con get_context_usage() del
propio SDK sobre estas mismas opciones:

  - Carga el CLAUDE.md de la raiz del proyecto: 1.208 tokens en cada llamada
    que lo lleve, o sea esta y la correccion. Son reglas de como trabajar
    sobre el repositorio, no de como destilar una clase. Por eso el detalle
    fino del pipeline vive en orquestador/CLAUDE.md, que se comprobo que NO
    se carga, ni siquiera despues de leer la transcripcion, que esta en esa
    misma carpeta.
  - NO evita la memoria automatica de la cuenta: el MEMORY.md del usuario
    (1.021 tokens) entra igual, con setting_sources=["project"] y tambien con
    [], que es lo que usan revisor.py y hoja_html.py. O sea que la corrida SI
    depende de un archivo global que puede cambiar sin aviso, incluido el
    revisor, que se diseño para llegar sin contexto previo. No hay opcion del
    SDK para apagarlo (exclude_dynamic_sections solo lo reinyecta en el primer
    mensaje, no lo quita). Tenerlo presente antes de guardar en esa memoria
    algo que un revisor pudiera confundir con respaldo de la clase.

Al terminar, la skill reporta todo en una linea "RESULTADO_ORQUESTADOR: {...}"
(instruccion en el SKILL.md), que esta etapa parsea y guarda.
"""
import dataclasses
import json
from pathlib import Path

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)

from .config import dir_pendientes
from .notificaciones import notificar_aviso
from .uso import registrar_uso

PROJECT_ROOT = Path(__file__).parent.parent
CLI_PATH = PROJECT_ROOT / "node_modules" / ".bin" / "claude"

MARCADOR_RESULTADO = "RESULTADO_ORQUESTADOR:"

# Nombre de la skill, tal como aparece en el campo `name` de su SKILL.md.
SKILL = "transcripciones-a-conocimiento"

# Tope de turnos como seguro: si algo se sale de lo esperado (ej. la skill
# queda dando vueltas releyendo archivos), esto corta la corrida en vez de
# consumir cuota sin control. Una clase normal usa muchos menos.
#
# Estuvo en 40 y se quedo corto en una clase real: la skill alcanzo a escribir
# las tres notas y se quedo sin turnos justo antes de emitir la linea
# RESULTADO_ORQUESTADOR, asi que la corrida se dio por fallida con el trabajo
# hecho. Eso pasaba cuando la skill exploraba el vault entero; desde que la
# carpeta llega resuelta (ver carpetas.py) el uso real es de 9 a 16 turnos,
# medido sobre siete clases. Con 30 hay casi el doble de margen y, si algo se
# descontrola, corta mucho antes de gastar de mas.
MAX_TURNS = 30

# Tope de la tercera pasada, la que corrige lo que encontro el revisor. Va
# aparte y con nombre para que los topes del pipeline se vean todos juntos
# (los otros estan en revisor.py y hoja_html.py). Coincide con el de
# arriba por ahora, pero mide cosas distintas: aca son ediciones puntuales
# sobre notas que ya existen, no el trabajo completo.
MAX_TURNS_CORRECCION = 30

# Pedir de nuevo la linea de resultado cuando vino con el JSON roto: es repetir
# un texto, sin herramientas, asi que un turno basta.
MAX_TURNS_RESULTADO = 1


def construir_gate_de_rutas(*raices_permitidas: str | Path):
    """
    Hook PreToolUse que acota al agente a las carpetas donde legitimamente
    tiene algo que hacer. Cada llamador pasa las suyas: las etapas que escriben
    notas pasan el proyecto (la transcripcion a leer y los archivos de la
    propia skill) y el vault de Obsidian (donde escriben).

    Las raices llegan por parametro y no se deducen aqui dentro a proposito.
    Antes esta funcion recibia el vault y agregaba PROJECT_ROOT por su cuenta,
    con dos consecuencias: leyendo la llamada no se podia saber a que quedaba
    autorizado el agente, y regenerar.py (retirado el 14-09-2026), que solo
    necesitaba el proyecto, terminaba pasandolo en el parametro del vault, asi
    que el mensaje de denegacion nombraba un "vault de Obsidian" que era la
    carpeta del proyecto.

    Existe porque la corrida es automatizada y usa permission_mode
    "bypassPermissions": nadie va a ver ni contestar una peticion de permiso,
    asi que sin esto Write/Edit podrian escribir en cualquier parte del disco
    (verificado en vivo que si pueden). La transcripcion viene de audio de sala
    de clases pasado por un transcriptor automatico, o sea texto no confiable
    que termina dentro del prompt: si alguna vez trajera algo que el modelo
    interprete como instruccion, esto limita el alcance del dano.

    Se usa un hook y no can_use_tool porque el propio SDK avisa que
    can_use_tool queda anulado por "bypassPermissions" y por las entradas de
    allowed_tools que habilitan una herramienta completa (verificado en vivo:
    el callback nunca se llega a invocar). El hook si se dispara, y decide
    solo, sin preguntarle nada a nadie: la automatizacion queda intacta.

    Lo usa tambien la etapa de revision (ver revisor.py), que corre con las
    mismas dos raices.
    """
    if not raices_permitidas:
        # Un gate sin raices denegaria todo, y la corrida fallaria recien en el
        # primer Read, lejos de aqui y con un mensaje que no explica nada.
        raise ValueError(
            "construir_gate_de_rutas necesita al menos una raiz permitida."
        )

    raices = [Path(r).expanduser().resolve() for r in raices_permitidas]
    raices_legibles = ", ".join(str(r) for r in raices)

    async def gate(input_data, tool_use_id, context):
        tool_input = input_data.get("tool_input") or {}
        # Write/Edit/Read usan "file_path"; Glob y Grep usan "path".
        ruta = tool_input.get("file_path") or tool_input.get("path") or ""
        if not ruta:
            return {}
        destino = Path(ruta).expanduser().resolve()
        if any(destino == raiz or destino.is_relative_to(raiz) for raiz in raices):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Ruta fuera de alcance: {destino}. Esta corrida solo puede "
                    f"leer y escribir dentro de: {raices_legibles}."
                ),
            }
        }

    return gate


def _construir_prompt(
    ruta_texto: str, ramo: str, carpeta_ramo: str, nota_extra: str = ""
) -> str:
    # La carpeta llega ya resuelta y creada por el orquestador (ver
    # carpetas.py). Darsela exacta, y decirle explicitamente que no busque, es
    # lo que evita que liste el vault entero: ese listado entraba al contexto y
    # se releia en cada turno posterior.
    return (
        f"Aplica la skill {SKILL} sobre la transcripcion "
        f"que esta en el archivo: {ruta_texto}\n\n"
        "Contexto ya resuelto por el sistema de orquestacion, no lo preguntes:\n"
        f"- Ramo: {ramo}\n"
        "- Alcance: una clase (esta unica transcripcion)\n"
        f"- Carpeta donde van las notas: {carpeta_ramo}\n\n"
        "Esa carpeta ya existe y es la correcta. Escribe las notas ahi directamente. "
        "No explores el vault de Obsidian ni busques la carpeta del ramo, ya esta "
        "resuelta. Si necesitas ver que otras notas del ramo hay para enlazarlas, "
        "mira solo dentro de esa carpeta."
        + (f"\n\n{nota_extra}" if nota_extra else "")
    )


def _parsear_resultado(texto: str) -> dict | None:
    """
    Solo parsea. Los campos se normalizan en normalizar_resultado, aparte y a
    proposito: ver ahi por que no puede hacerse en este punto.
    """
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith(MARCADOR_RESULTADO):
            try:
                return json.loads(linea[len(MARCADOR_RESULTADO):].strip())
            except json.JSONDecodeError:
                return None
    return None


async def consultar(prompt: str, options: ClaudeAgentOptions):
    """
    query() del SDK, sin la excepcion que levanta despues de un error ya entregado.

    Desde alguna version del SDK, cuando el CLI termina con un ResultMessage
    is_error, query() lo entrega y enseguida levanta "Claude Code returned an
    error result: success". Esa excepcion se escapaba del bucle antes de que
    corriera describir_error_sdk, asi que el estudiante recibio ese texto, que no
    dice nada, en vez de "la sesion de Claude Code caduco" (clase del
    01-10-2026). Cada bucle ya anota el error del ResultMessage: aqui solo se
    calla la excepcion que lo repite. Si no hubo ResultMessage con error, la
    excepcion sigue de largo, porque entonces es la unica pista.
    """
    # Los conectores de claude.ai van apagados siempre. Con la sesion normal de
    # Claude Code, el CLI los carga solo, sin pasar por setting_sources ni
    # mcp_servers: el 01-10-2026 el bot tenia Google Calendar y Claude Docs listos
    # para usar, y Gmail y Drive esperando autorizacion, y se lo conto al
    # estudiante. Eso es su correo y sus eventos personales, justo lo que el
    # agente no debe poder leer (ver evaluaciones.py). Medido en vivo: sin esta
    # variable aparecen los cuatro, con ella ninguno.
    env = {**options.env, "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}

    # Se probo usar el token de "claude setup-token" (un año) y no sirve aqui. Del
    # 02 al 06-10-2026 se paso como CLAUDE_CODE_OAUTH_TOKEN, y medido en
    # segundo plano el 06-10: una llamada por el SDK responde igual con un token
    # inventado o con el real alterado en una letra. O sea, el CLI usa la sesion
    # del Llavero y no mira el token. Peor, con el token puesto la sesion no se
    # renovo sola: la clase de Mercados del 06-10 fallo con "401 OAuth access
    # token has expired. Re-authenticate", sin intento de renovar, y la sesion
    # volvio apenas corrio una llamada sin el token (el Llavero se actualizo en
    # ese momento). La conexion es la sesion normal, que se renueva sola.
    #
    # Si Claude rechaza la credencial, se intenta una segunda vez: un proceso
    # nuevo relee el Llavero, y eso cubre el caso de otro proceso (el bot y una
    # clase a la vez) que acaba de renovar la sesion. Lo que llega se retiene
    # hasta la primera respuesta del modelo, porque el rechazo llega antes, asi
    # que el intento fallido no deja rastro en quien llama.
    for intento in (1, 2):
        es_el_ultimo = intento == 2
        retenidos, confirmado = [], es_el_ultimo
        rechazada = hubo_error = False
        try:
            async for mensaje in query(prompt=prompt, options=dataclasses.replace(options, env=env)):
                if isinstance(mensaje, ResultMessage) and mensaje.is_error:
                    hubo_error = True
                if rechazada:
                    continue
                if not confirmado:
                    if isinstance(mensaje, AssistantMessage) and getattr(mensaje, "error", None) is None:
                        confirmado = True
                    elif isinstance(mensaje, ResultMessage) and _es_credencial_rechazada(mensaje):
                        rechazada = True
                        continue
                    else:
                        retenidos.append(mensaje)
                        continue
                    for retenido in retenidos:
                        yield retenido
                    retenidos = []
                yield mensaje
        except Exception:
            if not (hubo_error or rechazada):
                raise
        if rechazada:
            await anyio.sleep(SEGUNDOS_ANTES_DE_REINTENTAR)
            continue
        for retenido in retenidos:
            yield retenido
        return


SEGUNDOS_ANTES_DE_REINTENTAR = 5


def _es_credencial_rechazada(mensaje) -> bool:
    return (getattr(mensaje, "api_error_status", None) == 401
            or _es_sesion_caducada(getattr(mensaje, "result", None) or ""))


def describir_error_sdk(mensaje) -> str:
    """
    Arma un texto util a partir de un ResultMessage que llego con is_error.

    `subtype` por si solo no sirve y ademas engaña: cuando lo que falla es una
    llamada HTTP a la API (429, 500, 529), el subtype llega igual como
    "success" y el codigo real viaja en api_error_status. El SDK lo documenta
    en ResultMessage. El mensaje que se veia era "La skill termino con error:
    success", que no dice absolutamente nada de lo que paso.
    """
    # `result` es donde el SDK deja la explicacion en palabras, y era lo unico
    # que traia el fallo mas util de todos: "Failed to authenticate: OAuth
    # session expired and could not be refreshed", o sea que la sesion de
    # Claude Code habia caducado. Sin esta linea el estudiante leia
    # "subtype=success" y no tenia forma de saber que le tocaba volver a
    # iniciar sesion. Va primero porque es lo que de verdad se entiende.
    trozos = []
    texto = (getattr(mensaje, "result", None) or "").strip()
    if texto:
        trozos.append(texto[:300])
        if _es_sesion_caducada(texto):
            trozos.append(
                "La conexión con Claude caducó. En la Terminal, en la carpeta del proyecto, "
                "corre 'node_modules/.bin/claude auth login' para iniciar sesión"
                ". Si era una clase, su transcripción ya quedó guardada, así que después "
                "basta con volver a procesarla y retoma desde ahí"
            )

    trozos.append(f"subtype={mensaje.subtype}")
    estado = getattr(mensaje, "api_error_status", None)
    if estado:
        trozos.append(f"HTTP {estado}")
    razon = getattr(mensaje, "terminal_reason", None)
    if razon:
        trozos.append(f"termino por {razon}")
    errores = getattr(mensaje, "errors", None)
    if errores:
        trozos.append("; ".join(str(e) for e in errores))
    return " | ".join(trozos)


def _es_sesion_caducada(texto: str) -> bool:
    minusculas = texto.lower()
    return "authenticate" in minusculas or "oauth" in minusculas or "session expired" in minusculas


def normalizar_resultado(resultado: dict, titulo_respaldo: str) -> dict:
    """
    Garantiza los dos campos que el resto del pipeline lee con acceso directo:
    "titulo" (string no vacio) y "conceptos_repetidos" (lista).

    Esta aca y no dentro de _parsear_resultado porque hay tres formas de llegar
    a un resultado y solo dos pasan por el parser: la skill recien corrida, la
    correccion posterior a la revision (que reemplaza el resultado entero por
    uno recien parseado, ver corregir_con_revision), y el <slug>_skill.json de
    una corrida anterior, que se reusa tal cual desde el disco. Normalizar al
    parsear dejaba ese tercer camino sin cubrir, que es justo el de los
    reintentos.

    Por que "titulo" no puede faltar: era el unico campo que finalizar_clase
    leia con acceso directo y sin respaldo. Si el modelo emitia la linea
    RESULTADO_ORQUESTADOR sin el, la clase se perdia entera por un KeyError,
    con las notas ya escritas en el vault. Y como el _skill.json quedaba
    guardado, cada reintento leia ese mismo archivo y fallaba en el mismo
    punto: la clase quedaba imposible de terminar hasta borrar el archivo a
    mano. El titulo solo nombra la hoja de repaso, no puede costar la clase.
    """
    titulo = resultado.get("titulo")
    if not isinstance(titulo, str) or not titulo.strip():
        # Sin ramo tampoco se queda en blanco: nombre_base pega el titulo
        # despues de un " - ", y uno vacio deja el archivo terminado en guion.
        resultado["titulo"] = titulo_respaldo.strip() or "Clase sin titulo"
    if not isinstance(resultado.get("conceptos_repetidos"), list):
        # La hoja se arma igual sin esa seccion (ver hoja_html.py).
        resultado["conceptos_repetidos"] = []
    return resultado


def _guardar_resultado_skill(slug: str, resultado: dict | None) -> Path:
    dir_pendientes().mkdir(parents=True, exist_ok=True)
    ruta = dir_pendientes() / f"{slug}_skill.json"
    ruta.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
    return ruta


async def aplicar_skill(
    ruta_texto: str, ramo: str, vault_dir: str, slug: str, carpeta_ramo: str,
    nota_extra: str = "",
) -> dict | None:
    """
    Corre la skill y devuelve lo que reporto, mas el campo "session_id" que
    agrega el orquestador (no viene del modelo): con el, la etapa de revision
    puede pedirle a esta misma sesion que corrija sin volver a leer la
    transcripcion completa. Ver corregir_con_revision.

    `nota_extra` se pega al final del prompt. Lleva las evaluaciones conocidas
    del ramo (ver evaluaciones.resumen_para_skill), para que la skill asocie
    cada anuncio a S1, S2 o Examen.

    `carpeta_ramo` es la carpeta exacta donde van las notas, ya resuelta y
    creada (ver carpetas.py). `vault_dir` se sigue necesitando aparte, para el
    gate de rutas: acota la escritura a todo el vault, no solo a esa carpeta,
    porque la skill puede tener razones legitimas para leer notas de otros
    ramos al enlazar.
    """
    prompt = _construir_prompt(ruta_texto, ramo, carpeta_ramo, nota_extra)
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        # La skill se habilita por nombre en la opcion `skills`, no metiendo
        # "Skill" en allowed_tools (esa forma quedo deprecada). Asi el permiso
        # queda acotado a esta skill en vez de habilitar cualquiera que el CLI
        # llegue a descubrir.
        skills=[SKILL],
        # El SDK rellena setting_sources solo si no viene puesto (verificado en
        # el codigo del transporte: usa ["user", "project"] por defecto). Aqui
        # va explicito para que siga siendo solo "project": asi la corrida no
        # depende de configuracion global de la cuenta, que podria cambiar sin
        # aviso.
        setting_sources=["project"],
        # Glob y Grep son de solo lectura y hacen falta de verdad: la skill los
        # usa para ubicar la carpeta del ramo dentro del vault antes de escribir
        # (verificado en vivo). Bash no aparece aca, pero eso por si solo NO lo
        # bloquea: con permission_mode "bypassPermissions" el modelo igual puede
        # usar herramientas que no esten en esta lista (comprobado en vivo, la
        # skill escribia las notas con Bash). Lo que si lo bloquea de verdad es
        # disallowed_tools, abajo.
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        # Sin Bash, el gate de rutas de abajo tiene sentido: si el modelo pudiera
        # ejecutar comandos, podria escribir donde quisiera sin pasar por el.
        # Verificado en vivo que la skill funciona igual sin Bash (usa Write).
        disallowed_tools=["Bash"],
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher="Write|Edit|Read|Glob|Grep",
                    hooks=[construir_gate_de_rutas(PROJECT_ROOT, vault_dir)],
                )
            ]
        },
        cli_path=str(CLI_PATH),
        max_turns=MAX_TURNS,
    )

    partes = []
    session_id = None
    error_sdk = None
    async for mensaje in consultar(prompt=prompt, options=options):
        if isinstance(mensaje, AssistantMessage):
            for bloque in mensaje.content:
                if isinstance(bloque, TextBlock):
                    partes.append(bloque.text)
        elif isinstance(mensaje, ResultMessage):
            registrar_uso("destilado", slug, mensaje)
            session_id = mensaje.session_id
            if mensaje.is_error:
                # Se anota y se sigue, no se levanta aqui. Antes esta linea
                # hacia raise dentro del bucle y con eso se perdia `partes`,
                # que es donde viene el RESULTADO_ORQUESTADOR: si el fallo era
                # un 429 o un 529 al final de una corrida ya terminada, se
                # tiraba el trabajo completo. Visto en vivo dos veces: en
                # TALLER DE ANÁLISIS NUMÉRICO III (15-08-2026, 13 turnos
                # perdidos y la clase reprocesada entera despues) y en el
                # ensayo del 17-08, donde las tres notas estaban escritas y
                # completas en el vault cuando salto el error. Es el mismo
                # criterio que ya usaban revisar() y corregir_con_revision().
                error_sdk = describir_error_sdk(mensaje)

    texto = "\n".join(partes)
    resultado = _parsear_resultado(texto)
    if resultado is None and MARCADOR_RESULTADO in texto and session_id:
        # La linea vino pero no es JSON valido. Paso en la clase del
        # 15-10-2026: las tres notas escritas y a la linea le faltaba una llave
        # de cierre, asi que la clase se dio por fallida y no hubo hoja.
        # Repetirla en la misma sesion es un turno sobre el cache, rehacer la
        # clase eran 12 turnos.
        resultado = await _pedir_resultado_de_nuevo(session_id, slug)
    if resultado is None:
        raise ValueError(
            "La skill no reporto RESULTADO_ORQUESTADOR, no se puede ubicar "
            "las notas ni los conceptos repetidos."
            + (f" El SDK reporto: {error_sdk}." if error_sdk else "")
        )

    if error_sdk:
        # La clase se salva, pero el error no se traga en silencio: que la API
        # este fallando es algo que conviene saber antes de mandar cinco clases
        # seguidas. Queda ademas en logs/uso.jsonl como error_api.
        notificar_aviso(
            "La API fallo, pero la clase se salvo",
            f"{ramo}: hubo un problema de conexion ({error_sdk}) despues de que "
            "la skill terminara su trabajo. Las notas quedaron completas y la "
            "clase sigue su curso. No tienes que hacer nada.",
        )

    resultado["session_id"] = session_id
    _guardar_resultado_skill(slug, resultado)
    return resultado


async def _pedir_resultado_de_nuevo(session_id: str, slug: str) -> dict | None:
    """
    Retoma la sesion de la skill solo para que repita la linea de resultado.
    Sin ninguna herramienta: las notas ya estan escritas y aqui no se tocan.
    """
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        setting_sources=[],
        resume=session_id,
        allowed_tools=[],
        disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit", "Read", "Glob", "Grep",
                          "WebFetch", "WebSearch", "Task"],
        cli_path=str(CLI_PATH),
        max_turns=MAX_TURNS_RESULTADO,
    )
    partes = []
    try:
        async for mensaje in consultar(
            prompt="La linea RESULTADO_ORQUESTADOR que emitiste no es JSON valido: "
                   "le falta o le sobra una llave o un corchete. Emitela de nuevo, "
                   "completa, en una sola linea y con el mismo contenido. No toques "
                   "las notas.",
            options=options,
        ):
            if isinstance(mensaje, AssistantMessage):
                partes.extend(b.text for b in mensaje.content if isinstance(b, TextBlock))
            elif isinstance(mensaje, ResultMessage):
                registrar_uso("resultado", slug, mensaje)
    except Exception:
        # Un error tardio no descarta lo que ya llego: se intenta leer igual. Si
        # no hay linea, la clase falla con el mismo error que antes.
        pass
    return _parsear_resultado("\n".join(partes))


def _construir_prompt_correccion(hallazgos: list[dict]) -> str:
    lineas = []
    for i, h in enumerate(hallazgos, start=1):
        lineas.append(
            f"{i}. [{h.get('gravedad', 'alta')}] {h.get('donde', 'sin ubicacion')}\n"
            f"   Problema: {h.get('problema', '')}\n"
            f"   Correccion pedida: {h.get('correccion', '')}"
        )
    detalle = "\n".join(lineas)

    return (
        "Un revisor independiente comparo las notas que escribiste contra la "
        "transcripcion cruda y encontro esto:\n\n"
        f"{detalle}\n\n"
        "Corrige estos puntos en las notas que ya escribiste, editandolas en el "
        "mismo lugar. Instrucciones:\n"
        "- Si un hallazgo es correcto, arreglalo. Cuando la transcripcion no "
        "respalda algo, la salida correcta casi nunca es reescribirlo mejor: es "
        "quitarlo o marcarlo (reconstruccion a verificar, dudoso: audio, hueco).\n"
        "- Si despues de corregir queda algo de lo que el estudiante deba "
        "desconfiar, dejalo dicho con un callout `> [!verificar]` **pegado al "
        "parrafo afectado**, no al principio de la nota. Una advertencia lejos "
        "del contenido del que habla no se lee, o se lee sin poder usarla. Di "
        "que tiene de raro en concreto, no una advertencia generica.\n"
        "- Si un hallazgo esta equivocado y la transcripcion te da la razon, no "
        "cambies nada por ese punto y explica en una linea por que.\n"
        "- No reescribas secciones enteras ni cambies el estilo. Toca solo lo "
        "señalado.\n"
        "- No inventes contenido nuevo para tapar lo que quites.\n\n"
        "Cuando termines, vuelve a emitir la linea RESULTADO_ORQUESTADOR con el "
        "mismo formato de antes (titulo, fuente, aprendizaje, conceptos_repetidos, "
        "llamados, evaluaciones), actualizada si alguna correccion la cambio."
    )


async def corregir_con_revision(
    resultado_skill: dict, hallazgos: list[dict], vault_dir: str, slug: str
) -> dict:
    """
    Tercera pasada: le devuelve los hallazgos del revisor a la misma sesion que
    escribio las notas, para que las corrija.

    Se retoma la sesion original (`resume`) en vez de abrir una nueva por dos
    razones. La primera es de costo: esa sesion ya leyo la transcripcion entera
    y tiene las notas en contexto, asi que corregir no vuelve a pagar esa
    lectura. La segunda es de calidad: sabe de donde salio cada parrafo, asi que
    puede distinguir un hallazgo correcto de uno donde el revisor se equivoco.

    Si la correccion falla por cualquier motivo, se devuelve el resultado
    original sin tocar. Las notas ya escritas siguen ahi y la clase se archiva
    igual: perder la correccion es un problema mucho menor que perder la clase.
    """
    if not hallazgos:
        return resultado_skill

    session_id = resultado_skill.get("session_id")
    if not session_id:
        return resultado_skill

    error_sdk = None

    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        skills=[SKILL],
        setting_sources=["project"],
        resume=session_id,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        disallowed_tools=["Bash"],
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher="Write|Edit|Read|Glob|Grep",
                    hooks=[construir_gate_de_rutas(PROJECT_ROOT, vault_dir)],
                )
            ]
        },
        cli_path=str(CLI_PATH),
        # Si se pasa de esto, algo se salio de lo esperado y es mejor cortar.
        max_turns=MAX_TURNS_CORRECCION,
    )

    partes = []
    async for mensaje in consultar(
        prompt=_construir_prompt_correccion(hallazgos), options=options
    ):
        if isinstance(mensaje, AssistantMessage):
            for bloque in mensaje.content:
                if isinstance(bloque, TextBlock):
                    partes.append(bloque.text)
        elif isinstance(mensaje, ResultMessage):
            registrar_uso("correccion", slug, mensaje)
            if mensaje.is_error:
                # Anotar y seguir, no salir del bucle. Antes esto hacia return
                # aqui mismo y perdia `partes`: si la correccion ya habia
                # editado las notas y el fallo era un 429 al final, se
                # descartaba el resultado corregido y el _skill.json quedaba
                # con el titulo y las rutas de antes, sin la marca de
                # corregido, mientras las notas del vault si estaban
                # corregidas. Mismo motivo que en aplicar_skill.
                error_sdk = describir_error_sdk(mensaje)

    corregido = _parsear_resultado("\n".join(partes))
    if corregido is None:
        # Las correcciones pueden haberse aplicado igual (el modelo edita antes
        # de reportar). Se conservan las rutas y el titulo que ya se tenian.
        if error_sdk:
            # El titulo antes que el slug: es lo unico legible que hay aca
            # dentro, y el slug ("2026-08-13_a1b2c3d4") no le dice nada a nadie.
            notificar_aviso(
                "La correccion no se pudo confirmar",
                f"{resultado_skill.get('titulo') or slug}: hubo un problema de "
                f"conexion ({error_sdk}). Las notas del vault pueden haber "
                "quedado corregidas igual, pero el documento se arma con la "
                "version anterior a la correccion.",
            )
        return resultado_skill

    corregido["session_id"] = session_id
    corregido["corregido_tras_revision"] = True
    # La correccion reemplaza el resultado entero. Si el modelo no repite los
    # anuncios de evaluacion, se conservan los de la primera pasada: Python los
    # vuelve a comprobar contra la transcripcion antes de aplicarlos.
    corregido.setdefault("evaluaciones", resultado_skill.get("evaluaciones"))
    corregido.setdefault("llamados", resultado_skill.get("llamados"))
    _guardar_resultado_skill(slug, corregido)
    return corregido


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 5:
        print("Uso: python3 -m orquestador.skill_runner <ruta_texto> <ramo> <vault_dir> <slug>")
        raise SystemExit(1)

    from .carpetas import resolver_carpeta_ramo
    from .config import cargar_config, guardar_config

    _config = cargar_config()
    _carpeta, _cambio = resolver_carpeta_ramo(sys.argv[2], sys.argv[3], _config)
    if _cambio:
        guardar_config(_config)

    resultado = anyio.run(
        aplicar_skill, sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], str(_carpeta)
    )
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
