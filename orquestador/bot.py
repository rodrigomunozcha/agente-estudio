"""
El bot de Telegram: la secretaria y el profe de cada ramo, en el iPhone.

Por que existe. El Mac queda en casa, y el estudiante queria hablar con el
agente desde el telefono: avisarle cosas ("falte a la clase del lunes"),
preguntarle dudas cortas de cualquier ramo, mandarle fotos de la pizarra o un
PDF, y recibir lo que la secretaria tiene que recordarle. Se eligio un bot
propio y no Remote Control porque la secretaria escribe sin que se le pregunte,
y eso Remote Control no lo cubre (verificado en su documentacion el 29-09-2026).

Lo que mas le importaba: saber que algo esta pasando. Su bot de finanzas
tardaba sin decir nada. Este contesta "Recibido" al instante, muestra
"escribiendo..." mientras trabaja y va diciendo que esta leyendo.

Como responde. Cada pregunta es una llamada al modelo con el plan Pro, con
solo tres herramientas de lectura (Read, Glob, Grep) y un gate de rutas: las
notas de los ramos, las hojas, el archivo de evaluaciones, lo que mando el
estudiante y los PDF de OneDrive de la lista blanca. Nada mas, porque en esas
carpetas hay documentos personales (ver onedrive.py). No puede escribir: un
cambio de fechas lo propone con una linea ACCION, y Python lo anota solo si el
estudiante confirma con un boton.

Memoria. La conversacion sigue mientras no pasen 3 horas sin escribir, asi una
pregunta de las 23:55 y otra de las 00:03 siguen juntas. /nuevaconversacion la
borra cuando quiera.

Solo atiende al chat emparejado (config.json, telegram.chat_id). Cualquier otro
mensaje se ignora sin contestar.

Uso:
    python3 -m orquestador.bot                    el bot (lo lanza launchd)
    python3 -m orquestador.bot --emparejar        lista quien le escribio
    python3 -m orquestador.bot --emparejar <id>   deja ese chat como el unico
"""
import json
import re
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from . import evaluaciones, onedrive, reuniones, secretaria, telegram
from .config import cargar_config, guardar_config
from .hoja_evaluacion import NOMBRES, ramo_legible
from .hoja_html import HERRAMIENTAS_PROHIBIDAS
from .ramo_por_nombre import RECONOCIDO
from .ramo_por_nombre import resolver as resolver_ramo
from .skill_runner import CLI_PATH, PROJECT_ROOT, construir_gate_de_rutas, consultar, describir_error_sdk
from .uso import registrar_uso

RUTA_INSTRUCCIONES = Path(__file__).parent / "plantilla_bot" / "instrucciones.md"
RUTA_ESTADO = Path(__file__).parent / "bot_estado.json"

MEMORIA = timedelta(hours=3)
# Sonnet 5.5 por pedido del estudiante (29-09-2026). Medido ese dia con el precio
# que usa el propio CLI: cuesta cerca de un 10% mas por pregunta que Sonnet 5,
# que es el modelo por defecto del plan y el que sigue usando el pipeline.
MODELO = "claude-sonnet-5-5"
MAX_TURNS = 15  # seguro contra una respuesta que se desvie, no un objetivo
TIMEOUT_RESPUESTA_SEGUNDOS = 6 * 60
MAX_BYTES_ARCHIVO = 20 * 1024 * 1024  # lo que Telegram deja bajar a un bot
PROHIBIDAS = [h for h in HERRAMIENTAS_PROHIBIDAS if h not in ("Read", "Glob", "Grep")]

COMANDOS = [
    ("nuevaconversacion", "Borra la memoria y empieza de cero"),
    ("hoy", "Lo que pidió el profesor para hoy y las evaluaciones de los próximos 7 días"),
    ("evaluaciones", "Fechas, tipo y clases que entran en cada evaluación"),
    ("pendientes", "Lo que tienes por hacer, de hoy en adelante, de clases y reuniones"),
    ("resumenhtmlparasolemne", "Te mando la hoja de la próxima evaluación del ramo que elijas"),
    ("ayuda", "Qué puedo hacer y cómo hablarme"),
]

AYUDA = (
    "👋 Soy tu agente de estudio\n\n"
    "💬 Pregúntame dudas de cualquier ramo. Respondo con tus apuntes y te digo de dónde sale "
    "cada cosa.\n\n"
    "📝 Avísame cosas, como \"falté a la clase del lunes de Marketing\" o \"la S2 de Mercados se "
    "movió al 24\". Te pido confirmación antes de anotar.\n\n"
    "📎 Mándame fotos o PDF. Los guardo en la carpeta Bot de IA del ramo, en OneDrive. Si "
    "escribes el ramo junto al archivo, no te pregunto.\n\n"
    "☀️ A las 9:00 te escribo si el profesor pidió algo para ese día o si viene una evaluación.\n\n"
    "🧠 Recuerdo la conversación mientras no pasen 3 horas sin escribir. Con /nuevaconversacion "
    "la borro antes.\n\n"
    "👇 Toca Menú para ver los comandos."
)

_ACCION = re.compile(r"^ACCION:\s*(\{.*\})\s*$", re.M)


# --------------------------------------------------------------------------
# Estado: la conversacion vigente y lo que espera un boton
# --------------------------------------------------------------------------

def _cargar_estado() -> dict:
    try:
        return json.loads(RUTA_ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_estado(estado: dict) -> None:
    RUTA_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def ramos_con_carpeta(config: dict) -> list[str]:
    """Los ramos que tienen carpeta en OneDrive, en el orden de la semana."""
    nombres = [info["nombre"] for info in (config.get("ramos") or {}).values()]
    nombres += [r for r in (config.get("ramos_adicionales") or {}) if r not in nombres]
    return [r for r in nombres if onedrive.carpeta_del_ramo(r) is not None]


# --------------------------------------------------------------------------
# La conversacion con el modelo
# --------------------------------------------------------------------------

def _raices_de_lectura(config: dict) -> list[Path]:
    """Lo unico que el modelo puede leer. Las carpetas de OneDrive no entran
    enteras: solo "Bot de IA" (lo que mando el estudiante) y los PDF de la
    lista blanca de onedrive.elegir_archivos."""
    raices = [Path(p) for p in (config.get("carpetas_ramo") or {}).values()]
    raices += [Path(config["rutas"]["output"]), evaluaciones.RUTA,
               Path(config["rutas"]["vault_obsidian"]) / reuniones.CARPETA_BOVEDA]
    for ramo in ramos_con_carpeta(config):
        carpeta = onedrive.carpeta_del_ramo(ramo)
        raices.append(carpeta / "Bot de IA")
        # El programa del ramo (ponderaciones, controles, eximicion) entra por la
        # lista blanca de onedrive.elegir_archivos, solo si esta declarado a mano.
        programa = (config.get("programas") or {}).get(ramo)
        for evaluacion in ("S1", "S2", "Examen"):
            for tipo in ("prueba", "trabajo"):
                raices += [r for _, r in onedrive.elegir_archivos(carpeta, evaluacion, tipo, programa)]
    return list(dict.fromkeys(r for r in raices if r.exists()))


def _system_prompt(config: dict, datos: dict, raices: list[Path]) -> str:
    """Solo lo que no cambia de un mensaje a otro. Hasta el 14-10-2026 llevaba
    tambien la hora, las evaluaciones y los pendientes: como la hora cambiaba
    cada minuto, el prompt nunca se repetia y el cache del modelo no servia.
    Cada mensaje volvia a escribir unos 27.000 tokens en cache, incluso el segundo
    de una misma conversacion, y leerlos cuesta doce veces menos que escribirlos.
    Lo que cambia va ahora en _contexto_del_dia, junto a cada pregunta."""
    return (
        RUTA_INSTRUCCIONES.read_text(encoding="utf-8")
        + "\n\n## Horario de clases\n\n" + secretaria.horario(config)
        + "\n\nNombres exactos de los ramos, para la línea ACCION: "
        + ", ".join(datos.get("ramos", {}))
        + "\n\n## Rutas que puedes leer\n\n" + "\n".join(f"- {r}" for r in raices)
    )


def _contexto_del_dia(config: dict, datos: dict) -> str:
    """La fecha, las evaluaciones y los pendientes al momento de la pregunta. Va
    antes del texto del estudiante, en cada mensaje (ver _system_prompt)."""
    ahora = datetime.now()
    return (
        f"## Hoy\n\n{secretaria._fecha(ahora.date().isoformat())} de {ahora.year}, {ahora:%H:%M}."
        + "\n\n## Evaluaciones\n\n" + secretaria.tabla_evaluaciones(datos, config, ahora.date())
        + "\n\n## Pendientes anotados (controles, tareas y pedidos)\n\n"
        + secretaria.lista_pendientes(datos, ahora.date())
        + "\n\n## Lo que escribe el estudiante\n\n"
    )


def _describir(bloque: ToolUseBlock) -> str:
    entrada = bloque.input or {}
    if bloque.name == "Read":
        return f"📖 Leyendo {Path(entrada.get('file_path', '')).name}..."
    if bloque.name == "Grep":
        return f"🔎 Buscando \"{entrada.get('pattern', '')}\" en tus notas..."
    return "🗂️ Revisando qué material hay..."


async def _preguntar(texto: str, sesion: str | None, system_prompt: str, raices: list[Path],
                     al_usar) -> tuple[str | None, str | None, str | None]:
    """(respuesta, sesion, error). Mismo patron que el pipeline: ni raise ni
    return dentro del bucle del SDK, para no perder una respuesta que ya llego."""
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        setting_sources=[],
        system_prompt=system_prompt,
        allowed_tools=["Read", "Glob", "Grep"],
        disallowed_tools=PROHIBIDAS,
        permission_mode="bypassPermissions",
        hooks={"PreToolUse": [HookMatcher(matcher="Read|Glob|Grep",
                                          hooks=[construir_gate_de_rutas(*raices)])]},
        cli_path=str(CLI_PATH),
        model=MODELO,
        max_turns=MAX_TURNS,
        resume=sesion,
    )
    textos, final, nueva_sesion, error = [], None, None, None
    async for mensaje in consultar(prompt=texto, options=options):
        if isinstance(mensaje, AssistantMessage):
            for bloque in mensaje.content:
                if isinstance(bloque, TextBlock):
                    textos.append(bloque.text)
                elif isinstance(bloque, ToolUseBlock):
                    al_usar(bloque)
        elif isinstance(mensaje, ResultMessage):
            registrar_uso("bot", "telegram", mensaje)
            nueva_sesion, final = mensaje.session_id, mensaje.result
            if mensaje.is_error:
                error = describir_error_sdk(mensaje)
    respuesta = (final or (textos[-1] if textos else "")).strip()
    return respuesta or None, nueva_sesion, error


def _escribiendo_mientras(parar: threading.Event) -> None:
    # Telegram borra el "escribiendo..." a los 5 segundos: se renueva cada 4.
    while not parar.is_set():
        try:
            telegram.escribiendo()
        except Exception:
            pass
        parar.wait(4)


def separar_accion(texto: str) -> tuple[dict | None, str]:
    m = _ACCION.search(texto)
    if not m:
        return None, texto
    try:
        accion = json.loads(m.group(1))
    except ValueError:
        accion = None
    return (accion if isinstance(accion, dict) else None), _ACCION.sub("", texto).strip()


def validar_accion(accion: dict, datos: dict) -> str | None:
    """La pregunta de confirmacion, o None si la accion no tiene sentido. Las reglas
    viven en evaluaciones.revisar_accion_del_chat: aqui solo se redacta el texto.
    Una accion invalida no se ofrece: se pierde el boton, no la conversacion."""
    a = evaluaciones.revisar_accion_del_chat(accion, datos)
    if a is None:
        return None
    ramo, fecha = ramo_legible(a["ramo"]), secretaria._fecha(a["fecha"])
    if a["tipo"] == "clase_perdida":
        return f"📝 ¿Anoto que faltaste a {ramo} el {fecha}?"
    if a["tipo"] == "clase_suspendida":
        return f"📝 ¿Anoto que la clase de {ramo} del {fecha} se suspendió? No cuenta como clase perdida."
    if a["tipo"] == "hecho":
        return f"📝 ¿Marco como hecho, en {ramo}?\n• {a['que']}"
    if a["tipo"] == "pendiente":
        return f"📝 ¿Anoto para el {fecha}, en {ramo}?\n• {a['que']}"
    texto = f"📝 ¿Anoto la {NOMBRES.get(a['evaluacion'], a['evaluacion'])} de {ramo} para el {fecha}?"
    if a["choque"]:
        texto += (f"\n⚠️ Ojo: en la clase del {secretaria._fecha(a['choque']['clase'])} el profesor dijo "
                  f"\"{a['choque']['textual']}\", y normalmente manda lo que se dijo en clase.")
    return texto


def aplicar_accion(accion: dict) -> str:
    """Anota la accion ya confirmada y devuelve el mensaje para Telegram."""
    ramo, fecha = accion["ramo"], accion["fecha"]
    try:
        if accion["tipo"] == "pendiente":
            # Un control o una tarea que no salio de una clase grabada. Va a la misma
            # lista que los pedidos del profesor, asi la secretaria lo avisa y sale
            # en /pendientes, sin otra lista que mantener.
            evaluaciones.anotar_pendiente(ramo, fecha, accion["que"].strip(), "chat")
            return f"✅ Anotado para el {secretaria._fecha(fecha)}: {accion['que'].strip()}. Te lo voy a recordar."
        if accion["tipo"] == "clase_suspendida":
            evaluaciones.anotar_clase_suspendida(ramo, fecha)
            return (f"✅ Anotado: no hubo clase de {ramo_legible(ramo)} el {secretaria._fecha(fecha)}. "
                    "No cuenta como clase perdida.")
        if accion["tipo"] == "hecho":
            if not evaluaciones.marcar_hecho(ramo, accion["que"], fecha):
                return "🤔 Ese pendiente ya no estaba por hacer, así que no cambié nada."
            return f"✅ Anotado: «{accion['que']}» queda como hecho. Ya no te lo muestro."
        if accion["tipo"] == "clase_perdida":
            evaluaciones.anotar_clase_perdida(ramo, fecha)
            return (f"✅ Anotado: faltaste a {ramo_legible(ramo)} el {secretaria._fecha(fecha)}. "
                    "La hoja de la evaluación lo va a decir.")
        evaluaciones.fijar_fecha_desde_chat(ramo, accion["evaluacion"], fecha)
        nombre = NOMBRES.get(accion["evaluacion"], accion["evaluacion"])
        return f"✅ Anotado: la {nombre} de {ramo_legible(ramo)} queda para el {secretaria._fecha(fecha)}."
    except evaluaciones.CandadoOcupado:
        return "⏳ No pude anotarlo: justo se estaba guardando otra cosa. Inténtalo de nuevo en un momento."


def conversar(texto: str, estado: dict, config: dict) -> None:
    ahora = datetime.now()
    vigente = (estado.get("session_id") and estado.get("ultimo")
               and ahora - datetime.fromisoformat(estado["ultimo"]) < MEMORIA)
    datos = evaluaciones.cargar()
    raices = _raices_de_lectura(config)
    mid = telegram.enviar("⏳ Recibido, pensando...")["message_id"]
    parar = threading.Event()
    threading.Thread(target=_escribiendo_mientras, args=(parar,), daemon=True).start()
    inicio, ultima_edicion = time.monotonic(), [0.0]

    def al_usar(bloque: ToolUseBlock) -> None:
        # Editar cada vez que lee algo daria decenas de ediciones: una cada 2 s basta.
        if time.monotonic() - ultima_edicion[0] >= 2:
            ultima_edicion[0] = time.monotonic()
            try:
                telegram.editar(mid, "⏳ " + _describir(bloque))
            except Exception:
                pass

    async def correr():
        with anyio.fail_after(TIMEOUT_RESPUESTA_SEGUNDOS):
            return await _preguntar(_contexto_del_dia(config, datos) + texto,
                                    estado.get("session_id") if vigente else None,
                                    _system_prompt(config, datos, raices), raices, al_usar)

    try:
        respuesta, sesion, error = anyio.run(correr)
    except TimeoutError:
        respuesta, sesion, error = None, None, "tardé más de 6 minutos"
    except Exception as e:
        respuesta, sesion, error = None, None, f"{type(e).__name__}: {e}"
    finally:
        parar.set()
    segundos = round(time.monotonic() - inicio)
    if respuesta is None:
        telegram.editar(mid, f"⚠️ No pude responder ({segundos} s)")
        telegram.enviar(f"⚠️ No pude responder: {error or 'no llegó la respuesta'}. Tu mensaje quedó "
                        "arriba, mándamelo de nuevo cuando quieras.")
        return

    estado.update(session_id=sesion, ultimo=ahora.isoformat())
    accion, respuesta = separar_accion(respuesta)
    teclado, propuesta = None, None
    if accion and accion.get("tipo") == "cancelar":
        # El estudiante corrigio al bot: la propuesta que esperaba un boton se retira.
        retirada = _retirar_propuesta(estado)
        respuesta = respuesta or ("👌 Listo, retiré la propuesta. No anoté nada." if retirada
                                  else "👌 No tengo nada pendiente de confirmar.")
    else:
        confirmacion = validar_accion(accion, datos) if accion else None
        if confirmacion:
            # Los botones de la propuesta anterior seguirian activos y, al tocarlos,
            # anotarian la nueva: se quitan antes de mostrar la que reemplaza.
            _retirar_propuesta(estado)
            propuesta = {"tipo": "accion", "accion": evaluaciones.revisar_accion_del_chat(accion, datos)}
            respuesta = f"{respuesta}\n\n{confirmacion}".strip()
            teclado = telegram.botones([("Sí, anótalo", "si"), ("No", "no")])
    # Se guarda antes de enviar: si Telegram falla, la conversacion y lo retirado
    # no se pierden. La propuesta nueva se guarda despues, cuando su mensaje existe.
    _guardar_estado(estado)
    telegram.editar(mid, f"✅ Listo ({segundos} s)")
    enviado = telegram.enviar(respuesta, teclado=teclado)
    if propuesta:
        # El id del mensaje deja retirar sus botones y distinguirlos de los de otra propuesta.
        estado["pendiente"] = dict(propuesta, mensaje=enviado.get("message_id"))
        _guardar_estado(estado)


def _retirar_propuesta(estado: dict) -> bool:
    """Saca del estado la accion que espera confirmacion y le quita los botones a su
    mensaje. False si no habia ninguna."""
    pendiente = estado.get("pendiente") or {}
    if pendiente.get("tipo") != "accion":
        return False
    estado.pop("pendiente")
    if pendiente.get("mensaje"):
        telegram.quitar_botones(pendiente["mensaje"])
    return True


# --------------------------------------------------------------------------
# Archivos que manda el estudiante
# --------------------------------------------------------------------------

def _ramo_de_texto(texto: str, config: dict) -> str | None:
    estado, info = resolver_ramo([texto], config) if texto.strip() else (None, None)
    return info["nombre"] if estado == RECONOCIDO and info["nombre"] in ramos_con_carpeta(config) else None


def recibir_archivo(msg: dict, estado: dict, config: dict) -> None:
    ahora = datetime.now()
    if msg.get("photo"):
        archivo, nombre = msg["photo"][-1], f"Foto {ahora:%Y-%m-%d %H.%M.%S}.jpg"  # la ultima es la mas grande
    else:
        archivo = msg["document"]
        nombre = Path(archivo.get("file_name") or f"Archivo {ahora:%Y-%m-%d %H.%M.%S}").name
    if (archivo.get("file_size") or 0) > MAX_BYTES_ARCHIVO:
        telegram.enviar("⚠️ Ese archivo pesa más de 20 MB, y Telegram no deja que un bot baje archivos "
                        "más grandes. Súbelo tú a la carpeta del ramo en OneDrive.")
        return
    pendiente = {"tipo": "archivo", "file_id": archivo["file_id"], "nombre": nombre,
                 "leyenda": msg.get("caption") or ""}
    ramo = _ramo_de_texto(pendiente["leyenda"], config)
    if ramo:
        guardar_archivo(pendiente, ramo, estado, config)
        return
    estado["pendiente"] = pendiente
    _guardar_estado(estado)
    telegram.enviar("📂 ¿De qué ramo es?", teclado=telegram.botones(
        [(ramo_legible(r), f"ramo:{i}") for i, r in enumerate(ramos_con_carpeta(config))]))


def guardar_archivo(pendiente: dict, ramo: str, estado: dict, config: dict) -> None:
    """Lo guarda en "<ramo>/Bot de IA/" en OneDrive, sin pisar nada. Si el texto
    que venia con el archivo es una pregunta, se la hace al modelo con el
    archivo a mano: puede ver una foto o leer un PDF."""
    carpeta = onedrive.carpeta_del_ramo(ramo) / "Bot de IA"
    nombre = Path(pendiente["nombre"])
    destino, n = carpeta / nombre.name, 2
    while destino.exists():
        destino, n = carpeta / f"{nombre.stem} ({n}){nombre.suffix}", n + 1
    telegram.descargar(pendiente["file_id"], destino)
    telegram.enviar(f"📎 Guardado en {carpeta.parent.name}/Bot de IA/{destino.name}.")
    leyenda = pendiente.get("leyenda", "").strip()
    if "?" in leyenda or len(leyenda.split()) > 3:
        conversar(f"Te mandé un archivo de {ramo_legible(ramo)}, que quedó en {destino}. "
                  f"Mi mensaje: {leyenda}", estado, config)


# --------------------------------------------------------------------------
# Comandos y botones
# --------------------------------------------------------------------------

def enviar_hoja(ramo: str, config: dict) -> None:
    datos = evaluaciones.cargar()
    ev = evaluaciones.evaluacion_de(datos["ramos"][ramo], date.today().isoformat())
    if ev is None:
        telegram.enviar(f"🎉 No quedan evaluaciones este semestre en {ramo_legible(ramo)}.")
        return
    nombre = f"{NOMBRES.get(ev['nombre'], ev['nombre'])} de {ramo_legible(ramo)}"
    hoja = secretaria.hoja_de(ramo, ev["nombre"], config)
    if hoja:
        telegram.enviar_documento(hoja, nombre)
    else:
        telegram.enviar(f"📝 Todavía no hay hoja de la {nombre}. Se arma sola con la primera clase "
                        "que entre en ella.")


def atender_comando(comando: str, estado: dict, config: dict) -> None:
    if comando in ("start", "ayuda"):
        telegram.enviar(AYUDA)
    elif comando == "nuevaconversacion":
        for campo in ("session_id", "ultimo"):
            estado.pop(campo, None)
        _guardar_estado(estado)
        telegram.enviar("🧹 Listo, empezamos de cero. No recuerdo lo que hablamos antes.")
    elif comando == "hoy":
        texto, hojas = secretaria.resumen(date.today(), evaluaciones.cargar(), config)
        telegram.enviar(texto or "🙌 Hoy no hay nada pendiente ni evaluaciones en los próximos 7 días.")
        for hoja in hojas:
            telegram.enviar_documento(hoja, hoja.stem)
    elif comando == "pendientes":
        telegram.enviar(secretaria.lista_pendientes(evaluaciones.cargar(), date.today()))
    elif comando == "evaluaciones":
        telegram.enviar(secretaria.tabla_evaluaciones(evaluaciones.cargar(), config, date.today()))
    elif comando == "resumenhtmlparasolemne":
        telegram.enviar("📚 ¿De qué ramo?", teclado=telegram.botones(
            [(ramo_legible(r), f"hoja:{i}") for i, r in enumerate(evaluaciones.cargar()["ramos"])]))
    else:
        telegram.enviar("🤔 No conozco ese comando. Toca Menú para ver los que hay.")


def atender_boton(cb: dict, estado: dict, config: dict) -> None:
    telegram.responder_boton(cb["id"])
    dato, pendiente = cb.get("data", ""), estado.get("pendiente") or {}
    # Un boton de una propuesta anterior (retirada, o reemplazada por otra) no
    # confirma la actual. Telegram siempre trae el id del mensaje del boton.
    mensaje = (cb.get("message") or {}).get("message_id")
    if dato in ("si", "no") and pendiente.get("mensaje") and mensaje and mensaje != pendiente["mensaje"]:
        telegram.enviar("⌛ Ese botón ya no está vigente.")
    elif dato in ("si", "no") and pendiente.get("tipo") == "accion":
        estado.pop("pendiente")
        _guardar_estado(estado)
        telegram.enviar(aplicar_accion(pendiente["accion"]) if dato == "si"
                        else "👌 Listo, no anoté nada.")
    elif dato.startswith("ramo:") and pendiente.get("tipo") == "archivo":
        ramos = ramos_con_carpeta(config)
        i = int(dato[5:])
        if i < len(ramos):
            estado.pop("pendiente")
            _guardar_estado(estado)
            guardar_archivo(pendiente, ramos[i], estado, config)
    elif dato.startswith("hoja:"):
        ramos = list(evaluaciones.cargar()["ramos"])
        i = int(dato[5:])
        if i < len(ramos):
            enviar_hoja(ramos[i], config)
    else:
        telegram.enviar("⌛ Ese botón ya no está vigente.")


def atender(actualizacion: dict, chat: int) -> None:
    estado, config = _cargar_estado(), cargar_config()
    if "callback_query" in actualizacion:
        cb = actualizacion["callback_query"]
        if ((cb.get("message") or {}).get("chat") or {}).get("id") == chat:
            atender_boton(cb, estado, config)
        return
    msg = actualizacion.get("message") or {}
    if (msg.get("chat") or {}).get("id") != chat:
        return  # no es el estudiante: ni se contesta
    if msg.get("photo") or msg.get("document"):
        recibir_archivo(msg, estado, config)
        return
    texto = (msg.get("text") or "").strip()
    if not texto:
        telegram.enviar("🤔 Por ahora entiendo texto, fotos y archivos.")
    elif texto.startswith("/"):
        atender_comando(texto[1:].split()[0].split("@")[0].lower(), estado, config)
    else:
        conversar(texto, estado, config)


# --------------------------------------------------------------------------
# Emparejar y correr
# --------------------------------------------------------------------------

def emparejar(chat_id: str | None) -> None:
    actualizaciones = telegram.llamar("getUpdates", timeout=0)
    if chat_id is None:
        vistos = {}
        for u in actualizaciones:
            m = u.get("message") or {}
            if (m.get("chat") or {}).get("type") == "private":
                vistos[m["chat"]["id"]] = (m.get("from") or {}).get("first_name", "?")
        for id_chat, nombre in vistos.items():
            print(f"{id_chat}  {nombre}")
        if not vistos:
            print("Nadie le ha escrito al bot todavía. Mándale un mensaje y vuelve a correr esto.")
        return
    config = cargar_config()
    config["telegram"] = {"chat_id": int(chat_id)}
    guardar_config(config)
    if actualizaciones:
        # Descarta el "hola" de emparejar: si no, el bot lo contestaria al arrancar.
        telegram.llamar("getUpdates", timeout=0, offset=actualizaciones[-1]["update_id"] + 1)
    telegram.enviar("🤝 Listo, quedamos emparejados. Desde ahora solo te respondo a ti. Toca Menú para "
                    "ver lo que puedo hacer.", chat_id=int(chat_id))
    print(f"Emparejado con {chat_id}.")


def main() -> None:
    chat = telegram.chat_autorizado()
    if not chat:
        print("Falta emparejar el chat: python3 -m orquestador.bot --emparejar", flush=True)
        raise SystemExit(1)
    telegram.llamar("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMANDOS])
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            actualizaciones = telegram.llamar("getUpdates", espera=70, **params)
        except RuntimeError as e:
            print(f"{datetime.now():%d-%m %H:%M} sin conexión con Telegram: {e}", flush=True)
            time.sleep(10)
            continue
        for u in actualizaciones:
            offset = u["update_id"] + 1
            try:
                atender(u, chat)
            except Exception as e:
                traceback.print_exc()
                try:
                    telegram.enviar(f"⚠️ Algo falló de mi lado ({type(e).__name__}: {e}). Vuelve a "
                                    "intentarlo en un momento.")
                except Exception:
                    pass


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--emparejar":
        emparejar(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        main()
