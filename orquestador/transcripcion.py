"""
Transcripcion orquestada de los trabajos que arma la etapa 2 (deteccion).

Procesa los trabajos pendientes de a uno a la vez, sin paralelismo (Whisper
es intensivo en CPU/GPU del M3, no queremos varias transcripciones compitiendo
por los mismos nucleos). Si un trabajo tiene varias partes de audio (una clase
grabada en mas de un archivo), se transcriben en el orden cronologico ya
resuelto por la etapa 2 y se concatena el texto.

Antes de transcribir nada se decide de que es cada grabacion (pregunta_ramo.py):
sin preguntar si el nombre dice el ramo y la fecha, y si no, preguntandole al
estudiante por Telegram. Una grabacion que espera respuesta queda en Input.
Hasta el 14-10-2026 lo decidia una ventana del Mac (pantalla_confirmacion.py) o
un dialogo por grabacion (dialogo_no_reconocido.py), que solo servian frente al
Mac: ya no se llaman en ningun camino (issue #1).

Con el ramo resuelto, el texto queda listo para que la etapa 4 (skill
transcripciones-a-conocimiento) lo procese.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from . import cancelacion, deteccion, estado_vivo, pregunta_ramo, ramo_por_nombre
from .config import cargar_config, dir_pendientes, guardar_config
from .deteccion import construir_trabajos, marcar_emitido
from .nombres import calcular_numero_clase_por_orden, fecha_corta, slug_pendiente
from .notificaciones import notificar_aviso, notificar_error, notificar_progreso
from .uso import registrar_transcripcion

PERFIL_WHISPER_POR_DEFECTO = "es-chile"


def _obtener_transcribe(transcriptotem_dir: str):
    ruta = str(Path(transcriptotem_dir))
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
    from backend.transcriber import transcribe
    return transcribe


def transcribir_trabajo(trabajo: dict, config: dict) -> str:
    transcribe = _obtener_transcribe(config["rutas"]["transcriptotem"])
    perfil = trabajo["perfil_whisper"] or PERFIL_WHISPER_POR_DEFECTO
    # Se probó darle a Whisper los títulos de las clases anteriores del ramo, con
    # el ramo al final del prompt de Transcriptotem (09-10-2026). En 20 minutos
    # reales de una clase no reconoció más términos (beta, hipótesis, matriz
    # quedaron igual o peor), sacó 21% menos texto y más "Gracias." inventados.
    # El prompt de Whisper solo pesa en las primeras ventanas de 30 segundos. Se
    # descartó: el intento está en _herramientas/respaldos del contenedor.
    contexto = trabajo.get("contexto") or ""
    archivos = trabajo["archivos"]
    partes = []
    for i, ruta_audio in enumerate(archivos, start=1):
        detalle = f"Parte {i} de {len(archivos)}" if len(archivos) > 1 else trabajo["fecha"]
        # La transcripcion es la unica etapa cuyo tiempo si se puede estimar
        # de antemano: depende de la duracion del audio, no del modelo.
        duracion = estado_vivo.duracion_audio_segundos(ruta_audio)
        eta = duracion / estado_vivo.velocidad_transcripcion() if duracion else None
        notificar_progreso(estado_vivo.PASO_TRANSCRIBIR, detalle, eta,
                           sub=(i, len(archivos)) if len(archivos) > 1 else None)
        comenzo = time.monotonic()
        # Los audios largos se cortan solos antes de transcribir: uno de casi
        # dos horas devolvia cero texto sin dar error (ver _transcribir_archivo).
        # La verificacion de que salio texto ocurre tramo por tramo ahi adentro.
        texto = _transcribir_archivo(transcribe, ruta_audio, perfil, contexto, duracion, detalle)
        # Con la medicion real, la estimacion de la proxima clase deja de
        # depender de una constante escrita a mano (ver uso.py).
        registrar_transcripcion(trabajo.get("clave", "?"), duracion, time.monotonic() - comenzo)
        if len(archivos) > 1:
            texto = f"--- Parte {i} ({Path(ruta_audio).name}) ---\n{texto}"
        partes.append(texto)

    return "\n\n".join(partes)


# Una clase deja del orden de 900 caracteres por minuto de audio (medido sobre
# grabaciones reales). El umbral se pone muy por debajo: no busca juzgar si la
# transcripcion es buena, solo distinguir "hay clase aca" de "no salio nada".
CARACTERES_MINIMOS_POR_MINUTO = 100
MINIMO_ABSOLUTO = 200

# Duracion a partir de la cual el audio se corta antes de transcribirlo, y
# tamano de cada trozo.
#
# Existe por una clase real de 1 h 56 min: Whisper corrio 14 minutos y devolvio
# CERO caracteres, sin lanzar ningun error. Comprobado en vivo que el mismo
# audio si se transcribe bien por tramos (60 s dan 752 caracteres, 15 min dan
# 13.452), o sea que el problema es el largo del archivo, no la grabacion.
#
# No se sabe donde esta exactamente el limite y averiguarlo cuesta una corrida
# de varios minutos por prueba, asi que se corta muy por debajo: 20 minutos
# esta probado y 35 deja pasar directo a las clases cortas. Cortar de mas no
# cuesta casi nada (la copia es sin recodificar, segundos) y ademas da un
# avance visible mas fino en el icono de la barra.
PREFIJO_TROZOS = "trozos_"
UMBRAL_PARTIR_SEGUNDOS = 35 * 60
DURACION_TROZO_SEGUNDOS = 20 * 60


def _borrar_carpeta_de_trozos(carpeta: Path) -> None:
    """
    Borra la carpeta temporal de trozos, y solo esa.

    Este es el unico borrado que hace el sistema por su cuenta, asi que se
    escribe para que NO PUEDA alcanzar otra cosa, en vez de para que "no
    deberia":

    1. La carpeta la crea `tempfile.mkdtemp()` en el area temporal del sistema,
       con un nombre unico e irrepetible. No es una carpeta del estudiante:
       Input, Procesados y el vault estan fuera de su alcance por construccion.
    2. No se borra por patron ni por nombre. No hay glob, ni comodines, ni
       nombres armados con datos del audio. Se borra exactamente la ruta que
       este mismo modulo acaba de crear y tiene en una variable. Un patron es
       la forma en que estos borrados se vuelven catastroficos.
    3. Antes de borrar se comprueba que la ruta siga estando dentro del area
       temporal del sistema y conserve el prefijo con el que se creo. Si algo
       no calza, no se borra nada y se sigue: una carpeta temporal de sobra no
       le hace dano a nadie.
    4. El audio original nunca esta aca adentro. Los trozos son copias; el
       original se lee y se archiva despues, intacto.
    """
    try:
        resuelta = carpeta.resolve()
        raiz_temporal = Path(tempfile.gettempdir()).resolve()
        dentro_del_area_temporal = (
            resuelta != raiz_temporal and resuelta.is_relative_to(raiz_temporal)
        )
        if not (dentro_del_area_temporal and resuelta.name.startswith(PREFIJO_TROZOS)):
            return
        shutil.rmtree(resuelta, ignore_errors=True)
    except Exception:
        pass  # no borrar nunca puede costar mas que no limpiar


def _partir_audio(ruta_audio: str, carpeta: Path) -> list[Path]:
    """
    Corta un audio largo en trozos, sin recodificar.

    Los trozos son temporales y se borran al terminar: el archivo original no
    se toca, y se archiva despues como cualquier otro. El corte cae en un punto
    cualquiera de la clase, asi que en cada empalme se puede perder una palabra
    a medias. Con cinco o seis empalmes en una clase de dos horas eso es mucho
    menos dano que perder la clase entera, que es lo que pasaba antes.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    patron = carpeta / f"{Path(ruta_audio).stem} - trozo %03d{Path(ruta_audio).suffix}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(ruta_audio), "-f", "segment",
         "-segment_time", str(DURACION_TROZO_SEGUNDOS), "-c", "copy", str(patron), "-y"],
        check=True, capture_output=True,
    )
    return sorted(carpeta.glob(f"{Path(ruta_audio).stem} - trozo *"))


def _transcribir_archivo(transcribe, ruta_audio: str, perfil: str, contexto: str,
                         duracion: float | None, etiqueta: str) -> str:
    """Transcribe un archivo, cortandolo antes si es largo."""
    if not duracion or duracion <= UMBRAL_PARTIR_SEGUNDOS:
        return _transcribir_verificando(transcribe, ruta_audio, perfil, contexto, duracion)

    temporal = Path(tempfile.mkdtemp(prefix=PREFIJO_TROZOS))
    try:
        trozos = _partir_audio(ruta_audio, temporal)
        piezas = []
        for i, trozo in enumerate(trozos, start=1):
            notificar_progreso(
                estado_vivo.PASO_TRANSCRIBIR,
                f"{etiqueta}, tramo {i} de {len(trozos)}",
                (duracion / estado_vivo.velocidad_transcripcion()) * (1 - (i - 1) / len(trozos)),
                sub=(i, len(trozos)),
            )
            texto = _transcribir_verificando(transcribe, str(trozo), perfil, contexto,
                                             estado_vivo.duracion_audio_segundos(trozo))
            piezas.append(texto.strip())
        return "\n".join(piezas)
    finally:
        _borrar_carpeta_de_trozos(temporal)


def _transcribir_verificando(transcribe, ruta_audio: str, perfil: str, contexto: str,
                             duracion: float | None) -> str:
    """
    Transcribe y verifica, con un reintento si sale vacio.

    La causa conocida de los tramos vacios estaba en el filtro de alucinaciones de
    Transcriptotem, que borraba 20 minutos enteros por una racha como "....." o
    "100000" (arreglado el 07-10-2026, ver backend/transcriber.py, _texto_por_frases).
    El reintento queda igual: Whisper no da siempre el mismo texto con el mismo
    audio, y el 07-10-2026 el tramo que salio vacio transcribio bien a la segunda.
    Reintentar cuesta un par de minutos, y perder la clase cuesta volver a procesarla.
    """
    for intento in (1, 2):
        texto, _, _ = transcribe(ruta_audio, language_profile=perfil, context_text=contexto)
        try:
            _verificar_parte(texto, ruta_audio, duracion)
            return texto
        except ValueError:
            if intento == 2:
                raise


def _verificar_parte(texto: str, ruta_audio: str, duracion_s: float | None) -> None:
    """
    Corta si un audio no produjo practicamente texto.

    Existe por una clase real de casi dos horas: Whisper corrio 14 minutos y
    devolvio cero caracteres. El pipeline siguio igual, le entrego un archivo
    vacio a la skill, y lo que vio el estudiante fue "la skill no reporto
    RESULTADO_ORQUESTADOR": un mensaje que no dice nada del problema real, y
    que ademas llego despues de gastar una llamada al modelo para nada.

    Fallar aca no cuesta nada, el mensaje apunta al audio, y como el archivo
    todavia no se archivo, se puede reintentar.
    """
    utiles = len(texto.strip())
    minutos = (duracion_s or 0) / 60
    esperado = max(MINIMO_ABSOLUTO, int(minutos * CARACTERES_MINIMOS_POR_MINUTO))
    if utiles >= esperado:
        return

    nombre = Path(ruta_audio).name
    detalle = f"{utiles} caracteres para {minutos:.0f} minutos de audio" if minutos else \
              f"{utiles} caracteres"
    raise ValueError(
        f"La transcripción de '{nombre}' salió vacía o casi vacía ({detalle}), "
        "también al reintentarla. El audio no se procesó y sigue donde estaba, así que "
        "se puede volver a procesar. Si vuelve a pasar con el mismo audio, escúchalo: "
        "puede que el teléfono haya grabado silencio."
    )


def _guardar_pendiente(trabajo: dict, texto: str, bitacora=None) -> None:
    dir_pendientes().mkdir(parents=True, exist_ok=True)
    slug = slug_pendiente(trabajo["clave"])
    ruta_txt = dir_pendientes() / f"{slug}.txt"
    ruta_txt.write_text(texto, encoding="utf-8")
    metadata = dict(trabajo)
    metadata["archivos_originales"] = metadata.pop("archivos")
    metadata["archivo_texto"] = str(ruta_txt)
    metadata["slug"] = slug
    ruta_json = dir_pendientes() / f"{slug}.json"
    ruta_json.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if bitacora is not None:
        bitacora.archivo_creado(ruta_txt)
        bitacora.archivo_creado(ruta_json)


def _guardar_sin_clasificar(trabajo: dict, texto: str, output_dir: str) -> None:
    carpeta = Path(output_dir) / "Sin clasificar"
    carpeta.mkdir(parents=True, exist_ok=True)
    encabezado = (
        f"Fecha detectada: {trabajo['fecha']} ({trabajo['dia_semana']})\n"
        f"Archivos originales: {', '.join(Path(a).name for a in trabajo['archivos'])}\n"
        "Este audio no se trato como clase, asi que se transcribio pero no se\n"
        "aplicaron las skills. El audio quedo en Procesados/_Sin clasificar.\n"
        "Revisar a mano.\n"
        + "-" * 60 + "\n\n"
    )
    # El nombre del audio va en el del archivo. Con solo la fecha, dos
    # grabaciones sin clasificar del mismo dia escribian el mismo .txt y la
    # segunda reemplazaba la transcripcion de la primera.
    # Con el mismo nombre que su audio en Procesados/_Sin clasificar (ver _sacar_de_input).
    ruta = carpeta / f"{fecha_corta(trabajo['fecha'])} - {Path(trabajo['archivos'][0]).stem}.txt"
    ruta.write_text(encabezado + texto, encoding="utf-8")


def _sacar_de_input(trabajo: dict, config: dict, subcarpeta: str, bitacora=None) -> None:
    """
    Mueve a Procesados/<subcarpeta> los audios de un trabajo que no es clase.

    Lo usan "Ignorar archivo" (_Ignorados) y "No es una clase" (_Sin clasificar).
    Este ultimo antes dejaba el audio en Input: ya estaba marcado como emitido,
    asi que ningun clic lo volvia a tomar, pero seguia contando como pendiente,
    y si era lo unico en Input cada clic avisaba "Todavia copiando" aunque nada
    se estuviera copiando.

    Nunca pisa un archivo: si el nombre ya existe en destino, se numera. Mover
    encima de otro archivo lo reemplaza sin aviso, y eso es borrarlo. Igual que
    archivar_audio, se anota en la bitacora antes de mover y va en la seccion
    critica, para que detener la corrida devuelva el audio a Input.
    """
    carpeta = Path(config["rutas"]["procesados"]) / subcarpeta
    carpeta.mkdir(parents=True, exist_ok=True)
    with cancelacion.seccion_critica():
        for origen in trabajo["archivos"]:
            origen_path = Path(origen)
            if not origen_path.is_file():
                continue  # ya no esta en Input: no hay nada que mover
            destino = carpeta / f"{fecha_corta(trabajo['fecha'])} - {origen_path.name}"
            contador = 2
            while destino.exists():
                destino = carpeta / f"{fecha_corta(trabajo['fecha'])} - {origen_path.stem} ({contador}){origen_path.suffix}"
                contador += 1
            if bitacora is not None:
                bitacora.audio_movido(origen_path, destino)
            shutil.move(str(origen_path), str(destino))


def _aplicar_decision_de_pantalla(trabajo: dict, decision: dict, config: dict) -> str:
    """
    Traduce lo que se decidio (pregunta_ramo, o la decision inyectada en una
    prueba) al estado que el resto del pipeline espera. Devuelve que hacer con el trabajo: "seguir",
    "omitir".

    Un ramo escrito a mano en la pantalla se guarda en config igual que si se
    hubiera creado por el dialogo, para que la proxima grabacion con ese nombre
    ya se reconozca sola (ver ramo_por_nombre.py).
    """
    que_hacer = decision.get("que_hacer")
    if que_hacer == pregunta_ramo.OMITIR:
        return "omitir"
    if que_hacer == pregunta_ramo.SOLO_TRANSCRIBIR:
        trabajo["reconocido"] = False
        trabajo["ramo"] = None
        return "seguir"

    ramo = decision.get("ramo")
    if decision.get("ramo_nuevo") and ramo:
        # Perfil por defecto a proposito: la pantalla no pregunta el idioma
        # para no convertir una correccion de una linea en un formulario. Pero
        # hoy ninguna pantalla permite ajustarlo despues ("Configurar Sistema"
        # solo pregunta los ramos del horario): un ramo que no es en espanol
        # hay que corregirlo a mano en ramos_adicionales de config.json.
        config.setdefault("ramos_adicionales", {}).setdefault(
            ramo, {"perfil_whisper": PERFIL_WHISPER_POR_DEFECTO, "contexto": ""}
        )
        guardar_config(config)

    ya_estaba = trabajo.get("reconocido") and trabajo.get("ramo") == ramo
    trabajo["reconocido"] = True
    trabajo["ramo"] = ramo
    trabajo["perfil_whisper"] = (
        ramo_por_nombre.ramos_conocidos(config).get(ramo) or PERFIL_WHISPER_POR_DEFECTO
    )
    if not ya_estaba:
        # Mismo motivo que en el camino del dialogo: la semana de semestre no
        # aplica a un ramo elegido a mano, y daba numeros sin sentido.
        trabajo["numero_clase"] = calcular_numero_clase_por_orden(
            Path(config["rutas"]["procesados"]), ramo, date.fromisoformat(trabajo["fecha"])
        )
        trabajo["numeracion"] = "orden"
    return "seguir"


def procesar_pendientes(config: dict | None = None, bitacora=None, decidir=None) -> list[dict]:
    """
    `decidir` existe para poder probar sin escribirle a nadie. Por defecto es
    pregunta_ramo.preguntar. Una grabacion sin decision queda en Input para la
    proxima corrida: espera la respuesta del estudiante por Telegram.
    """
    if config is None:
        config = cargar_config()

    trabajos = construir_trabajos(config)
    if decidir is None:
        decidir = pregunta_ramo.preguntar
    decisiones = (decidir(trabajos, config) if trabajos else {}) or {}

    # Marcar un trabajo como emitido escribe trabajos_emitidos.json, y detener la
    # corrida tiene que revertirlo. Si no, una grabacion cuya transcripcion se
    # deshizo quedaria marcada como tomada y ningun clic la volveria a procesar.
    if bitacora is not None and trabajos:
        bitacora.respaldar(deteccion.TRABAJOS_EMITIDOS_PATH)

    procesados = []
    for trabajo in trabajos:
        # marcar_emitido() se llama recien cuando cada camino termina bien,
        # no al principio: si la transcripcion falla a mitad de camino, el
        # audio no debe quedar en el limbo marcado como "ya hecho" para
        # siempre. Asi un reintento (otro clic) lo vuelve a tomar.
        decision = decisiones.get(trabajo["clave"])
        if decision is None:
            continue  # espera la respuesta del estudiante: queda para la proxima corrida
        if _aplicar_decision_de_pantalla(trabajo, decision, config) == "omitir":
            continue

        try:
            texto = transcribir_trabajo(trabajo, config)
            if trabajo["reconocido"]:
                _guardar_pendiente(trabajo, texto, bitacora)
            else:
                _guardar_sin_clasificar(trabajo, texto, config["rutas"]["output"])
                _sacar_de_input(trabajo, config, "_Sin clasificar", bitacora)
                # Sin este aviso la corrida terminaba en silencio: la transcripcion
                # quedaba lista y nadie lo decia (entrevista grupal del 29-09-2026).
                notificar_aviso(
                    "Transcripción lista, sin clasificar",
                    f"{Path(trabajo['archivos'][0]).name}: no se trató como clase. "
                    "El texto quedó en Output/Sin clasificar.",
                )
        except Exception as e:
            notificar_error(
                f"{trabajo.get('ramo') or trabajo['dia_semana']} - {trabajo['fecha']}",
                f"Fallo al transcribir: {type(e).__name__}: {e}",
            )
            continue

        marcar_emitido(trabajo)
        procesados.append(trabajo)
    return procesados


if __name__ == "__main__":
    procesar_pendientes()
