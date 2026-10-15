"""
De que es una grabacion: se decide sin preguntar cuando el nombre lo dice, y si no,
se le pregunta al estudiante por Telegram, con un boton por ramo.

Por que existe. Antes lo decidia una ventana del Mac (pantalla_confirmacion.py) que
aparecia siempre, aunque el ramo estuviera claro, y que solo servia si el estudiante
estaba frente al Mac. Una grabacion guardada desde el telefono no tenia como
preguntarse (issue #1). Ahora la ventana no aparece en ningun camino.

Las reglas, en orden:
  1. El nombre de todos los archivos dice el ramo (ramo_por_nombre) y la fecha: se
     procesa sin preguntar.
  2. Cualquier otra duda se pregunta por Telegram, con un boton por ramo, Reunion y
     No es una clase. Si el dia de la semana sugiere un ramo, el mensaje lo dice.
La grabacion con duda no se procesa a ciegas: queda en Input hasta la respuesta. La
secretaria la recuerda una vez a las 9:00 (recordar), y nunca se decide sola.

Como viaja la respuesta. Solo el bot escucha a Telegram, y el procesamiento es otro
proceso. Entonces el procesamiento envia la pregunta, la anota aqui y sigue con lo
demas. Cuando el estudiante toca un boton, el bot anota la respuesta (responder) y
relanza el procesamiento, que ahora la encuentra. Este archivo lo escriben los dos
procesos, asi que cada escritura va dentro de un candado propio, como evaluaciones.

El contrato hacia transcripcion.procesar_pendientes es el mismo de la ventana: un
diccionario clave -> decision. Una grabacion que no aparece queda para la proxima
corrida. Por eso la decision se puede inyectar en las pruebas.
"""
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from . import deteccion, ramo_por_nombre, telegram
from .nombres import ramo_legible

# Que hacer con cada grabacion: los mismos caminos y nombres de la ventana anterior,
# asi transcripcion no distingue por donde se decidio.
PROCESAR, SOLO_TRANSCRIBIR, OMITIR = "procesar", "solo_transcribir", "omitir"
REUNION = "REUNIÓN"  # el mismo de reuniones.RAMO, sin importar el SDK del modelo aqui
NO_ES_CLASE = "No es una clase"

RUTA = Path(__file__).parent / "preguntas_grabaciones.json"
SEGUNDOS_CANDADO = 10
# Cuanto esperar antes de recordar. El recordatorio lo manda la secretaria a las 9:00,
# asi que una pregunta de anoche se recuerda en la manana y una de las 8:50, al dia
# siguiente.
RECORDAR_TRAS = timedelta(hours=3)

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


class CandadoOcupado(Exception):
    """Otro proceso tiene el archivo de preguntas hace mas de SEGUNDOS_CANDADO."""


@contextmanager
def _modificando():
    """Lee, deja cambiar y guarda, todo dentro del candado. Mismo patron que
    evaluaciones._modificando: el bot y el procesamiento escriben este archivo."""
    with open(RUTA.with_suffix(".lock"), "w") as candado:
        limite = time.monotonic() + SEGUNDOS_CANDADO
        while True:
            try:
                fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= limite:
                    raise CandadoOcupado(f"{RUTA.name} sigue ocupado despues de {SEGUNDOS_CANDADO} s")
                time.sleep(0.05)
        try:
            try:
                estado = json.loads(RUTA.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                estado = {}
            yield estado
            temporal = RUTA.with_suffix(".tmp")
            temporal.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporal, RUTA)
        finally:
            fcntl.flock(candado, fcntl.LOCK_UN)


def _leer() -> dict:
    try:
        return json.loads(RUTA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def opciones(config: dict) -> list[str]:
    """Los ramos de los botones: los del horario en el orden de la semana, despues los
    adicionales, y al final Reunion. "No es una clase" va aparte, como ultimo boton."""
    ramos = [info["nombre"] for info in (config.get("ramos") or {}).values()]
    ramos += [r for r in (config.get("ramos_adicionales") or {}) if r != REUNION]
    return list(dict.fromkeys(ramos)) + [REUNION]


def sin_preguntar(trabajo: dict, config: dict) -> str | None:
    """El ramo, si el nombre de cada archivo dice el ramo y la fecha. Con eso la
    grabacion se procesa sin preguntar nada (regla 1)."""
    nombres = [Path(a).name for a in trabajo["archivos"]]
    estado, info = ramo_por_nombre.resolver(nombres, config)
    if estado == ramo_por_nombre.RECONOCIDO and all(deteccion._fecha_de_nombre_plausible(n) for n in nombres):
        return info["nombre"]
    return None


def _cuando(trabajo: dict) -> str:
    d = datetime.fromisoformat(trabajo["fecha"])
    texto = f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}"
    momento = deteccion.momento_de_grabacion(Path(trabajo["archivos"][0]))
    return texto + (f", empezó a las {momento:%H:%M}" if momento and momento.date() == d.date() else "")


def _sugerencia(trabajo: dict) -> str | None:
    """El ramo que sugiere lo que se sabe de la grabacion, para decirlo en la pregunta.
    Hoy es el dia de la semana segun el horario (deteccion)."""
    return trabajo.get("ramo") if trabajo.get("reconocido") else None


def _texto_pregunta(p: dict, recordatorio: bool = False) -> str:
    cabeza = ("⏰ Sigue esperando tu respuesta: ¿de qué es esta grabación?" if recordatorio
              else "🎙️ ¿De qué es esta grabación?")
    lineas = [cabeza, f"📅 {p['cuando']}", f"🗂️ {', '.join(Path(a).name for a in p['archivos'])}"]
    if p.get("sugerido"):
        lineas.append(f"👉 Por el día, parece de {ramo_legible(p['sugerido'])}")
    lineas.append("⏸️ La dejo sin procesar hasta que me digas.")
    return "\n".join(lineas)


def _botones(p: dict) -> dict:
    nombres = [("Reunión" if r == REUNION else ramo_legible(r)) for r in p["opciones"]] + [NO_ES_CLASE]
    return telegram.botones([(n, f"grab:{p['id']}:{i}") for i, n in enumerate(nombres)])


def preguntar(trabajos: list[dict], config: dict) -> dict[str, dict]:
    """
    Que hacer con cada trabajo, indexado por su clave. Lo que se procesa sin preguntar
    y lo que el estudiante ya respondio vienen con su decision. Lo que se acaba de
    preguntar, o sigue esperando, no viene: queda en Input para la proxima corrida.

    La pregunta se envia fuera del candado: Telegram puede tardar hasta 15 segundos, y
    el bot esperaria eso para anotar una respuesta.
    """
    decisiones, nuevas = {}, []
    estado = _leer()
    for t in trabajos:
        ramo = sin_preguntar(t, config)
        if ramo:
            decisiones[t["clave"]] = {"que_hacer": PROCESAR, "ramo": ramo}
            continue
        p = estado.get(t["clave"])
        if p and p.get("respuesta"):
            decisiones[t["clave"]] = dict(p["respuesta"])
        elif p is None:
            nuevas.append({"id": hashlib.sha1(t["clave"].encode()).hexdigest()[:10], "clave": t["clave"],
                           "fecha": t["fecha"], "cuando": _cuando(t), "archivos": t["archivos"],
                           "sugerido": _sugerencia(t), "opciones": opciones(config)})
    enviadas = []
    for p in nuevas:
        # Sin token, sin chat o sin red, avisar devuelve False y no se anota: la
        # proxima corrida vuelve a intentarlo, en vez de esperar una respuesta a una
        # pregunta que nunca llego.
        if telegram.avisar(_texto_pregunta(p), teclado=_botones(p)):
            enviadas.append(dict(p, preguntado=datetime.now().isoformat(timespec="seconds")))
    if enviadas or decisiones:
        with _modificando() as estado:
            for p in enviadas:
                estado.setdefault(p["clave"], p)
            for clave in decisiones:
                if clave in estado:
                    estado[clave]["usada"] = datetime.now().isoformat(timespec="seconds")
    return decisiones


def esperando(archivo: Path) -> bool:
    """Si el archivo esta en una pregunta que sigue sin respuesta."""
    archivo = str(archivo)
    return any(archivo in p.get("archivos", []) and not p.get("respuesta") for p in _leer().values())


def responder(id_pregunta: str, indice: int) -> dict | None:
    """Anota lo que el estudiante toco. Devuelve la pregunta con su respuesta, o None si
    ya no hay una abierta con ese id (ya respondida, o un boton que no existe)."""
    with _modificando() as estado:
        p = next((x for x in estado.values() if x.get("id") == id_pregunta), None)
        if p is None or p.get("respuesta"):
            return None
        if indice == len(p["opciones"]):
            p["respuesta"] = {"que_hacer": SOLO_TRANSCRIBIR}
        elif 0 <= indice < len(p["opciones"]):
            p["respuesta"] = {"que_hacer": PROCESAR, "ramo": p["opciones"][indice]}
        else:
            return None
        p["respondida"] = datetime.now().isoformat(timespec="seconds")
        return dict(p)


def recordar(ahora: datetime | None = None) -> int:
    """Recuerda una sola vez cada pregunta sin respuesta que lleva mas de RECORDAR_TRAS,
    con los mismos botones. Lo llama la secretaria a las 9:00. Una grabacion que ya no
    esta (el estudiante la saco de Input) no se recuerda. Devuelve cuantas recordo."""
    ahora = ahora or datetime.now()
    por_recordar = [p for p in _leer().values()
                    if not p.get("respuesta") and not p.get("recordada")
                    and ahora - datetime.fromisoformat(p["preguntado"]) >= RECORDAR_TRAS
                    and any(Path(a).exists() for a in p["archivos"])]
    recordadas = [p["clave"] for p in por_recordar
                  if telegram.avisar(_texto_pregunta(p, recordatorio=True), teclado=_botones(p))]
    if recordadas:
        with _modificando() as estado:
            for clave in recordadas:
                if clave in estado:
                    estado[clave]["recordada"] = ahora.isoformat(timespec="seconds")
    return len(recordadas)
