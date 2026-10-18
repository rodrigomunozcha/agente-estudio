"""
Encadena las etapas 4, 5 y 6 para las clases ya transcritas (etapa 3) y con
ramo reconocido: una pasada de la skill que limpia, destila, elige titulo y
detecta los conceptos mas repetidos (etapa 4+5 fusionadas, ver
skill_runner.py), la revision independiente y su correccion cuando hay
hallazgos graves, y por ultimo la hoja de repaso en HTML y el archivado del
audio (etapa 6).

Eso son hasta cuatro llamadas al SDK por clase, no una: las tres de
skill_runner.py (ver su encabezado para cuales son y por que la revision no se
colapsa dentro de la sesion que escribio las notas) y la redaccion de la hoja
(ver hoja_html.py para por que reemplazo al .docx).

Los trabajos no reconocidos ya quedaron resueltos en Output/Sin clasificar
por la etapa 3 y no pasan por aqui (ver transcripcion.py).

Consume los archivos que dejo la etapa 3 en transcripciones_pendientes/:
<slug>.json (metadata del trabajo) y <slug>.txt (texto transcrito). Una vez
que una clase ya paso por aqui (existe <slug>_skill.json), no se vuelve a
procesar. El slug (ver nombres.py) no es solo la fecha: dos clases distintas
pueden compartir una fecha corrupta (visto en vivo con clases de un ramo de
intercambio), y
usar solo la fecha hacia que la segunda se diera por "ya procesada" al pisar
el archivo de la primera.
"""
import json
import subprocess
import sys
from pathlib import Path

import anyio

from . import cancelacion
from .archivado import archivar_audio, publicar_en_onedrive
from .carpetas import resolver_carpeta_ramo
from .config import cargar_config, dir_pendientes, guardar_config
from .hoja_html import TIMEOUT_SEGUNDOS as TIMEOUT_HOJA_SEGUNDOS
from .hoja_html import (
    armar_documento,
    escribir_hoja,
    redactar_cuerpo,
    ruta_de_hoja,
    tarjeta_lo_que_pidio,
)
from .ensayo import es_ensayo
from . import estado_vivo, evaluaciones, hoja_evaluacion, material_ramo, reuniones
from .nombres import renumerar_clases_ramo
from .notificaciones import notificar_aviso, notificar_error, notificar_exito, notificar_progreso
from .revisor import hallazgos_graves, leer_calidad, revisar
from .skill_runner import aplicar_skill, corregir_con_revision, normalizar_resultado

# Topes de tiempo por etapa. El tope de turnos no alcanza: una llamada al SDK
# puede quedarse esperando una respuesta que no llega nunca (visto en vivo, con
# el subproceso vivo y 0% de CPU durante veinte minutos). Sin esto, una clase
# queda colgada para siempre y el estudiante no tiene forma de saberlo.
TIMEOUT_SKILL_SEGUNDOS = 45 * 60
TIMEOUT_REVISION_SEGUNDOS = 20 * 60

def _cargar_evaluaciones() -> dict | None:
    try:
        return evaluaciones.cargar()
    except (OSError, ValueError):
        return None  # sin el archivo, la clase se procesa igual, sin ese contexto


def aplicar_anuncios_de_clase(resultado_skill: dict, trabajo_metadata: dict, config: dict) -> None:
    """
    Pasa al archivo de evaluaciones lo que el profesor anuncio en esta clase:
    cambios en la S1, la S2 o el examen, y lo que pidio para una fecha (para
    que la secretaria lo recuerde ese dia). Los anuncios de clase son la fuente
    que manda: casi siempre el alcance, los cambios de fecha y las tareas se
    saben por primera vez ahi. Las reglas de cuando un anuncio cambia algo
    estan en evaluaciones.aplicar_anuncios y aplicar_pedidos.

    Nunca lanza: un anuncio mal leido no puede costar la clase.
    """
    anuncios = resultado_skill.get("evaluaciones")
    avisos = (resultado_skill.get("llamados") or {}).get("avisos")
    if not (anuncios or avisos) or not evaluaciones.RUTA.exists():
        return  # sin el archivo, la clase se procesa igual, sin ese contexto
    ramo = trabajo_metadata["ramo"]
    fecha = trabajo_metadata.get("fecha", "")
    try:
        transcripcion = Path(trabajo_metadata["archivo_texto"]).read_text(encoding="utf-8")
        cambios, pedidos = evaluaciones.registrar_clase(
            ramo, anuncios, avisos, fecha, transcripcion, evaluaciones.dia_del_ramo(ramo, config),
            ensayo=es_ensayo(config))
        if not (cambios or pedidos):
            return
    except Exception as e:
        notificar_aviso(f"No se revisaron los anuncios de {ramo}",
                        f"La clase se procesó igual. Detalle: {type(e).__name__}: {e}")
        return
    en_ensayo = " En ensayo no se guardó." if es_ensayo(config) else ""
    if cambios:
        notificar_aviso(f"Cambió una evaluación de {ramo}", ". ".join(cambios) + "." + en_ensayo)
    if pedidos:
        notificar_aviso(f"Te lo recuerdo el día que toca: {ramo}", ". ".join(pedidos) + "." + en_ensayo)


def _leer_nota(ruta: str | None, vault_dir: str) -> str:
    """
    Lee una de las notas que la skill dice haber escrito, para armar la hoja
    de repaso. Las rutas vienen del JSON que reporta el modelo, asi que se
    verifica que apunten dentro del vault antes de abrirlas: si no, una
    corrida que se desviara (ej. por texto raro colado en la transcripcion)
    podria hacer que el contenido de cualquier archivo del disco terminara
    dentro de la hoja. Y no solo eso: lo que se lee aqui entra al prompt de
    otra llamada al modelo (ver hoja_html.py). Fuera del vault se devuelve
    vacio en vez de leer: la hoja se arma igual, sin esa nota.
    """
    if not ruta:
        return ""
    ruta_path = Path(ruta).expanduser().resolve()
    raiz_vault = Path(vault_dir).expanduser().resolve()
    if not (ruta_path == raiz_vault or ruta_path.is_relative_to(raiz_vault)):
        return ""
    if not ruta_path.is_file():
        return ""
    return ruta_path.read_text(encoding="utf-8")


def _listar_metadatas_pendientes() -> list[Path]:
    if not dir_pendientes().exists():
        return []
    return sorted(
        p for p in dir_pendientes().glob("*.json") if not p.stem.endswith("_skill")
    )


def _slug_de(trabajo_metadata: dict) -> str:
    # Con fallback a la fecha por si queda algun archivo pendiente de antes
    # de este cambio (sin campo "slug" propio).
    return trabajo_metadata.get("slug") or trabajo_metadata["fecha"]


def _clase_ya_archivada(trabajo_metadata: dict) -> bool:
    """
    La marca real de que una clase termino es que su audio ya no esta donde
    empezo: archivar_audio() lo mueve como el ultimo paso de la etapa 6.

    Antes se usaba la existencia de <slug>_skill.json para esto, pero ese
    archivo lo escribe la etapa 4, mucho antes de terminar. Una clase que se
    cortaba entre la etapa 4 y la 6 (revision, documento, archivado) quedaba
    marcada como lista para siempre: cada clic siguiente la saltaba en
    silencio, sin audio movido, sin documento y sin ningun aviso. Paso en vivo con
    SISTEMAS Y ESTRUCTURA DIGITAL (04-08-2026): la revision fallo con un bug
    ya corregido, la clase nunca llego a archivar_audio(), y siguio
    "terminada" en cada corrida siguiente porque _skill.json ya estaba ahi.
    """
    archivos = trabajo_metadata.get("archivos_originales") or []
    return bool(archivos) and all(not Path(a).exists() for a in archivos)


async def _revisar_y_corregir(
    ruta_texto: str, resultado_skill: dict, ramo: str, vault_dir: str, slug: str,
    ruta_material: Path | None = None,
) -> dict:
    """
    Etapa 5: un revisor independiente compara las notas contra la transcripcion
    cruda, y si encontro algo grave, la sesion que las escribio las corrige.
    Ver revisor.py para por que la revision es una corrida aparte.

    La correccion solo se dispara con hallazgos de gravedad alta (contenido que
    la transcripcion no respalda). Los de gravedad media quedan anotados en
    <slug>_revision.json y no gastan una pasada mas: un revisor al que se le
    pide encontrar algo siempre encuentra algo, y corregir por cada detalle
    duplicaria el costo de cada clase para cambiar comas.

    Lo que la correccion no logra arreglar queda marcado dentro de la nota, con
    un callout `> [!verificar]` pegado al parrafo afectado. Antes esos avisos
    iban juntos en una seccion al principio del documento, y esa seccion se
    saltea: obliga a recordar una advertencia durante diez paginas hasta llegar
    al parrafo del que hablaba. Puesto al lado del contenido, se lee cuando
    sirve (ver references/diseno-documento.md).

    Toda esta etapa es opcional por diseño. Si falla, se sigue con lo que
    escribio la skill: las notas ya estan en el vault y son utiles. Que la
    revision se caiga no puede costarle la clase al estudiante.
    """
    notificar_progreso(estado_vivo.PASO_REVISION, "comprobando contra la transcripcion")
    try:
        with anyio.fail_after(TIMEOUT_REVISION_SEGUNDOS):
            revision = await revisar(ruta_texto, resultado_skill, ramo, vault_dir, slug,
                                     str(ruta_material) if ruta_material else None)
    except TimeoutError:
        notificar_aviso(
            "La revision tardo demasiado",
            f"{ramo}: se corto para no dejar la clase colgada. Las notas quedaron "
            "como las escribio la skill, sin revisar.",
        )
        return resultado_skill
    except Exception as e:
        notificar_aviso(
            "La revision no corrio",
            f"{ramo}: {type(e).__name__}. Las notas quedaron como las escribio la skill, "
            "sin revisar.",
        )
        # Dos valores, como el resto de las salidas de esta funcion. Devolver
        # tres aqui costo una clase entera en vivo (SISTEMAS Y ESTRUCTURA
        # DIGITAL, 04-08-2026): la revision fallo, este manejador reventó al
        # desempaquetar, y el codigo que existe para degradar en vez de abortar
        # fue justamente el que abortó.
        return resultado_skill

    # Tercer modo de falla de la revision, y el unico que no reventaba: el
    # revisor arranco pero no llego a emitir su resultado (ver revisor.py). Se
    # avisa igual que en los otros dos, porque para el estudiante el efecto es
    # el mismo: las notas van a la hoja sin que nadie las comprobara.
    if revision.get("revision_fallida"):
        notificar_aviso(
            "La revision no se pudo completar",
            f"{ramo}: {revision['revision_fallida']}. Las notas quedaron como "
            "las escribio la skill, sin revisar.",
        )
        return resultado_skill

    graves = hallazgos_graves(revision)
    if revision.get("veredicto") != "corregir" or not graves:
        return resultado_skill

    notificar_progreso(
        estado_vivo.PASO_REVISION,
        f"corrigiendo {len(graves)} hallazgo(s) de gravedad alta",
    )
    try:
        with anyio.fail_after(TIMEOUT_REVISION_SEGUNDOS):
            return await corregir_con_revision(resultado_skill, graves, vault_dir, slug)
    except TimeoutError:
        notificar_aviso(
            "La correccion tardo demasiado",
            f"{ramo}: se corto. Revisa los hallazgos en {slug}_revision.json.",
        )
        return resultado_skill
    except Exception as e:
        notificar_aviso(
            "La correccion no se pudo aplicar",
            f"{ramo}: {type(e).__name__}. Revisa los hallazgos en "
            f"{slug}_revision.json y corrige a mano lo que corresponda.",
        )
        return resultado_skill


async def _redactar_hoja(
    texto_aprendizaje: str, texto_fuente: str, texto_contexto: str, conceptos: list,
    ramo: str, titulo: str, fecha: str, slug: str,
) -> tuple[str | None, str | None]:
    """
    Envoltorio que degrada. Si la redaccion falla o tarda demasiado, la clase
    sigue con una hoja que dice arriba y en rojo que le falta la materia
    condensada (ver hoja_html.armar_documento). Las notas ya estan en el vault,
    y perder la clase entera por la hoja seria mucho peor que una hoja
    incompleta y honesta.

    El aborto pedido desde la barra de menu no lo atrapa el except Exception de
    abajo: Abortado hereda de BaseException (ver cancelacion.Abortado). Antes
    heredaba de Exception y aqui habia que dejarlo pasar a mano.
    """
    try:
        with anyio.fail_after(TIMEOUT_HOJA_SEGUNDOS):
            cuerpo, motivo = await redactar_cuerpo(
                texto_aprendizaje, texto_fuente, texto_contexto, conceptos,
                ramo, titulo, fecha, slug,
            )
    except TimeoutError:
        cuerpo, motivo = None, "la redacción tardó demasiado y se cortó"
    except Exception as e:
        cuerpo, motivo = None, f"falló la redacción ({type(e).__name__})"

    if motivo:
        notificar_aviso(
            "La hoja de repaso salió incompleta",
            f"{ramo}: {motivo}. La clase se procesó igual: las notas completas "
            "están en Obsidian, y la hoja avisa arriba que le falta la materia. "
            "Se puede volver a generar más tarde.",
        )
    return cuerpo, motivo


async def procesar_clase_reconocida(trabajo_metadata: dict, config: dict, bitacora=None) -> Path:
    ruta_texto = trabajo_metadata["archivo_texto"]
    ramo = trabajo_metadata["ramo"]
    slug = _slug_de(trabajo_metadata)
    vault_dir = config["rutas"]["vault_obsidian"]

    # La carpeta del ramo se resuelve aca, en Python, y se cachea en config.json:
    # buscarla es comparar nombres, no requiere criterio, y hacerlo dentro de la
    # skill obligaba a listar el vault entero en cada clase (ver carpetas.py).
    carpeta_ramo, config_cambio = resolver_carpeta_ramo(ramo, vault_dir, config)
    # En un ensayo el cache apunta al vault de mentira: guardarlo envenenaria
    # la configuracion real para la proxima clase de verdad.
    if config_cambio and not es_ensayo(config):
        guardar_config(config)

    # Antes de que la skill escriba nada: se respalda lo que ya habia en la
    # carpeta del ramo. Es el unico cambio que no se podria revertir despues,
    # porque la skill puede editar notas existentes (el indice del ramo) y esas
    # no se reconstruyen solas (ver bitacora.py).
    if bitacora is not None:
        bitacora.fotografiar_carpeta(carpeta_ramo)

    estado_vivo.fijar_clase(f"{ramo} - {trabajo_metadata.get('fecha', '')}")
    # Si una corrida anterior llego hasta aca y se corto despues (revision,
    # documento o archivado), <slug>_skill.json ya existe y es bueno: se
    # reusa en vez de pagar la skill de nuevo (~16.000 tokens fijos mas la
    # transcripcion completa). Visto en vivo con SISTEMAS Y ESTRUCTURA
    # DIGITAL (04-08-2026, ver _clase_ya_archivada mas abajo).
    ruta_skill_json = dir_pendientes() / f"{slug}_skill.json"
    # El programa del ramo y la lamina de la clase, si ya esta (ver
    # material_ramo.py). Se arma aunque la skill ya haya corrido: el revisor
    # tambien lo usa. Lee los PDF de PPTs/ con pypdf en cada clase: segundos, sin modelo.
    ruta_material = material_ramo.preparar(ramo, config, ruta_texto, slug)
    if ruta_skill_json.exists():
        notificar_progreso(estado_vivo.PASO_SKILL, "ya estaba analizada, retomando desde ahi")
        resultado_skill = json.loads(ruta_skill_json.read_text(encoding="utf-8"))
    else:
        notificar_progreso(estado_vivo.PASO_SKILL, "puede tardar unos minutos")
        datos_evaluaciones = _cargar_evaluaciones()
        extras = [
            evaluaciones.resumen_para_skill(datos_evaluaciones, ramo, trabajo_metadata.get("fecha", ""))
            if datos_evaluaciones else "",
            material_ramo.linea_para_prompt(str(ruta_material)) if ruta_material else "",
        ]
        with anyio.fail_after(TIMEOUT_SKILL_SEGUNDOS):
            resultado_skill = await aplicar_skill(
                ruta_texto, ramo, vault_dir, slug, str(carpeta_ramo),
                nota_extra="\n\n".join(e for e in extras if e),
            )

    resultado_skill = await _revisar_y_corregir(
        ruta_texto, resultado_skill, ramo, vault_dir, slug, ruta_material
    )

    # Recien aca, porque este es el punto donde convergen los tres caminos que
    # pueden producir el dict (ver normalizar_resultado). El respaldo del titulo
    # es el nombre del ramo y no el ramo con la fecha: la fecha ya va aparte en
    # el nombre del archivo (ver nombres.nombre_base).
    resultado_skill = normalizar_resultado(resultado_skill, ramo)
    aplicar_anuncios_de_clase(resultado_skill, trabajo_metadata, config)

    titulo = resultado_skill["titulo"]
    conceptos = resultado_skill["conceptos_repetidos"]

    texto_fuente = _leer_nota(resultado_skill.get("fuente"), vault_dir)
    texto_aprendizaje = _leer_nota(resultado_skill.get("aprendizaje"), vault_dir)
    # Opcional a proposito: si la skill no la genero (una clase que no
    # necesita contexto previo), la hoja se arma igual sin ella.
    texto_contexto = _leer_nota(resultado_skill.get("contexto"), vault_dir)

    trabajo = dict(trabajo_metadata)
    trabajo["archivos"] = trabajo_metadata["archivos_originales"]

    notificar_progreso(estado_vivo.PASO_DOCUMENTO, titulo)
    # La redaccion va antes de la seccion critica y no dentro: tarda minutos y
    # tiene que poder abortarse. Adentro solo queda escribir el archivo y mover
    # el audio, que duran segundos. El .docx cabia adentro porque se armaba sin
    # modelo, en un instante.
    cuerpo, motivo = await _redactar_hoja(
        texto_aprendizaje, texto_fuente, texto_contexto, conceptos, ramo, titulo,
        trabajo.get("fecha", ""), slug,
    )
    documento = armar_documento(
        trabajo, titulo, cuerpo,
        tarjeta_lo_que_pidio(texto_aprendizaje, resultado_skill.get("llamados")),
        Path(resultado_skill.get("aprendizaje") or "").name, motivo, slug,
        calidad=leer_calidad(slug),
    )

    # El unico tramo que no se puede cortar por la mitad: mover el audio lo
    # deja entre dos carpetas. Dura segundos y un aborto pedido aqui se aplica
    # apenas termina (ver cancelacion.py).
    with cancelacion.seccion_critica():
        ruta_hoja = escribir_hoja(ruta_de_hoja(trabajo, titulo, config), documento)
        if bitacora is not None:
            bitacora.archivo_creado(ruta_hoja)
        archivados = archivar_audio(trabajo, titulo, config, bitacora)
        # La clase en un solo lugar: audio, transcripcion y hoja juntos en
        # "<ramo>/Transcripciones/" de OneDrive (ver archivado.publicar_en_onedrive).
        en_onedrive = publicar_en_onedrive(archivados, ruta_hoja, ramo, config, bitacora) is not None

    if trabajo.get("numeracion") == "orden":
        # Solo para ramos manuales (fuera del horario con calendario propio):
        # si esta clase quedo mas antigua que otras ya archivadas, las que
        # venian despues deben correrse un puesto (ver nombres.py).
        renumerar_clases_ramo(
            Path(config["rutas"]["procesados"]), Path(config["rutas"]["output"]), ramo
        )

    # Aqui terminaba antes con las flashcards de Anki. Salieron el 14-09-2026,
    # junto con el .docx: una clase termina en sus notas de Obsidian y en la
    # hoja, y nada mas. Las preguntas con sus respuestas modelo siguen en la
    # nota de aprendizaje.
    if not en_onedrive and not es_ensayo(config):
        notificar_aviso("La clase no llegó a OneDrive",
                        f"{ramo}: está lista, pero no quedó entera en la carpeta Clases del ramo. Lo que "
                        "falte sigue en Procesados y Output. Revisa que el ramo tenga su carpeta en "
                        "carpetas_onedrive de config.json.")
    notificar_exito(trabajo, titulo, ruta_hoja, en_onedrive)
    await actualizar_hoja_de_evaluacion(ramo, trabajo.get("fecha", ""), config)
    return ruta_hoja


async def actualizar_hoja_de_evaluacion(ramo: str, fecha_clase: str, config: dict) -> None:
    """
    Con la clase ya cerrada, rehace la hoja de la proxima evaluacion de su
    ramo (ver hoja_evaluacion.py). Va despues del aviso de la clase a
    proposito: la hoja de clase ya esta lista y no tiene que esperar esta.

    Nunca lanza. La clase ya esta en el vault, en Output y en OneDrive, y una
    hoja de evaluacion fallida no puede costarla.
    """
    try:
        with anyio.fail_after(hoja_evaluacion.TIMEOUT_SEGUNDOS):
            await hoja_evaluacion.actualizar_tras_clase(ramo, fecha_clase, config)
    except Exception as e:
        notificar_aviso("No se actualizó la hoja de la evaluación",
                        f"{ramo}: la clase quedó bien. Detalle: {type(e).__name__}: {e}")


# Memoria de Rodrigo: cuando una clase termina, su wiki en la bóveda de Obsidian puede
# ingestarla de inmediato (ver el CLAUDE.md de esa bóveda, sección "Ingesta automática").
# El script vive en _herramientas porque lo comparten todas las sesiones, no solo este agente.
INGESTA_WIKI = Path.home() / "Documents" / "Claude Code" / "_herramientas" / "memoria-ingesta.py"


def lanzar_ingesta_wiki(config: dict) -> None:
    """Pide al wiki que ingeste las clases recién terminadas, sin esperar su resultado.

    Solo actúa si config.json trae "ingesta_wiki": true. Es opt-in a propósito: lanza una
    sesión de Claude sobre la bóveda real, y un efecto así no puede dispararse desde las
    pruebas ni desde una configuración de mentira (ya pasó una vez, ver el commit de este cambio).
    Es un extra: si falta el script o no se puede lanzar, la clase ya quedó cerrada y
    la próxima sesión de Claude Code reintenta sola. En un ensayo no se hace nada,
    porque el vault es de mentira.
    """
    if not config.get("ingesta_wiki") or es_ensayo(config) or not INGESTA_WIKI.exists():
        return
    try:
        subprocess.Popen([sys.executable, "-I", str(INGESTA_WIKI), "--ahora"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


async def procesar_pendientes_reconocidos(config: dict | None = None, bitacora=None) -> list[Path]:
    if config is None:
        config = cargar_config()

    generados = []
    for ruta_metadata in _listar_metadatas_pendientes():
        trabajo_metadata = json.loads(ruta_metadata.read_text(encoding="utf-8"))
        if not trabajo_metadata.get("reconocido"):
            continue
        if _clase_ya_archivada(trabajo_metadata):
            continue  # ya se proceso antes

        try:
            # Una reunion no es clase: tiene su propio analisis (ver reuniones.py).
            procesar = (reuniones.procesar_reunion if trabajo_metadata["ramo"] == reuniones.RAMO
                        else procesar_clase_reconocida)
            ruta_hoja = await procesar(trabajo_metadata, config, bitacora)
        except Exception as e:
            contexto = f"{trabajo_metadata['ramo']} - {trabajo_metadata['fecha']}"
            notificar_error(contexto, f"{type(e).__name__}: {e}")
            continue
        generados.append(ruta_hoja)
    if generados:
        lanzar_ingesta_wiki(config)
    return generados


if __name__ == "__main__":
    anyio.run(procesar_pendientes_reconocidos)
