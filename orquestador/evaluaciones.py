"""
Las evaluaciones del semestre y que clases entran en cada una.

Por que existe. El estudiante tiene poco tiempo y estudia para la proxima
evaluacion, no para el ramo entero: la hoja por evaluacion (S1, S2, examen)
junta solo las clases que entran en esa prueba. Para eso hace falta saber, de
cada clase, a que evaluacion pertenece.

El archivo evaluaciones.json se sembro el 29-09-2026 con los eventos "S1",
"S2" y "Examen" de su Google Calendar. Desde ahi lo actualizan los anuncios de
clase. El pipeline nunca lee el calendario: tendria que descargar tambien sus
eventos personales, y eso choca con la regla de no obtener datos privados.

Cuando dos fuentes dicen cosas distintas manda, de mayor a menor, lo que el
profesor dijo en clase, lo que el estudiante dijo por el chat y el calendario.
El calendario lo anota a mano una sesion de Claude. Lo de clase llega por
registrar_clase, y lo del chat y las reuniones por las operaciones de abajo
(anotar_pendiente, anotar_clase_perdida, anotar_clase_suspendida, marcar_hecho,
fijar_fecha_desde_chat).

Este module es el unico que escribe evaluaciones.json (decision del
08-10-2026, tras la revision de arquitectura). Antes escribian el bot, las
reuniones y el cierre de cada clase, cada uno con su forma y su regla de
duplicado, y sin candado: el bot es un proceso aparte, y si guardaba en el
mismo instante que una clase, uno pisaba lo que anoto el otro. Ahora cada
escritura lee, cambia y guarda dentro de un candado propio del archivo.

Formato: {"ramos": {"<RAMO como en config.json>": [evaluacion, ...]},
"clases_perdidas": [{"ramo", "fecha"}], "clases_suspendidas": [{"ramo", "fecha"}],
"pendientes": [...]}. Un pendiente hecho trae "estado": "hecho" y no se borra. Cada evaluacion trae nombre, fecha
(ISO o null si no esta confirmada), tipo (prueba, trabajo o desconocido),
acumula y fuente. Las evaluaciones de un ramo van en orden cronologico.
"""
import fcntl
import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

RUTA = Path(__file__).parent / "evaluaciones.json"


def cargar(ruta: Path | None = None) -> dict:
    datos = json.loads(Path(ruta or RUTA).read_text(encoding="utf-8"))
    # Los pendientes anteriores al 08-10-2026 no decian su origen. Los del chat y
    # las reuniones ya lo traian, asi que el que falta es de clase.
    for p in datos.get("pendientes", []):
        p.setdefault("origen", "clase")
    return datos


def guardar(datos: dict, ruta: Path | None = None) -> None:
    # Escribir aparte y reemplazar: un corte a mitad de escritura no puede
    # dejar el archivo de fechas del semestre a medias.
    ruta = Path(ruta or RUTA)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporal, ruta)


# El candado es solo de este archivo y dura milesimas. No es el de lock.py: ese
# queda tomado durante toda una clase (unos 40 minutos) y el bot esperaria eso
# para anotar un control.
SEGUNDOS_CANDADO = 10


class CandadoOcupado(Exception):
    """Otro proceso tiene el archivo hace mas de SEGUNDOS_CANDADO: no se anota nada."""


@contextmanager
def _modificando(ruta: Path | None = None, ensayo: bool = False):
    """Lee, deja cambiar y guarda, todo dentro del candado. En ensayo no guarda."""
    ruta = Path(ruta or RUTA)
    with open(ruta.with_suffix(".lock"), "w") as candado:
        limite = time.monotonic() + SEGUNDOS_CANDADO
        while True:
            try:
                fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= limite:
                    raise CandadoOcupado(f"{ruta.name} sigue ocupado despues de {SEGUNDOS_CANDADO} s")
                time.sleep(0.05)
        try:
            datos = cargar(ruta)
            yield datos
            if not ensayo:
                guardar(datos, ruta)
        finally:
            fcntl.flock(candado, fcntl.LOCK_UN)


def registrar_clase(ramo: str, anuncios: list, avisos: list, fecha_clase: str, transcripcion: str,
                    dia_semana: int | None, ensayo: bool = False) -> tuple[list[str], list[str]]:
    """Los anuncios de evaluacion y los pedidos de una clase. Devuelve, en palabras,
    (lo que cambio, lo que se anoto)."""
    with _modificando(ensayo=ensayo) as datos:
        cambios = aplicar_anuncios(datos, ramo, anuncios, fecha_clase, transcripcion)
        pedidos = aplicar_pedidos(datos, ramo, avisos, fecha_clase, transcripcion, dia_semana)
    return cambios, pedidos


def anotar_pendiente(ramo: str, fecha: str, que: str, origen: str, *, textual: str = "",
                     clase: str = "", reunion: str = "", ensayo: bool = False) -> bool:
    """Un pendiente del chat o de una reunion. False si ya estaba (mismo ramo,
    fecha y texto): repetirlo no lo duplica."""
    with _modificando(ensayo=ensayo) as datos:
        pendientes = datos.setdefault("pendientes", [])
        if any((p.get("ramo"), p.get("fecha"), p.get("que")) == (ramo, fecha, que) for p in pendientes):
            return False
        nuevo = {"ramo": ramo, "fecha": fecha, "que": que, "origen": origen}
        nuevo.update({k: v for k, v in (("textual", textual), ("clase", clase), ("reunion", reunion)) if v})
        pendientes.append(nuevo)
    return True


def anotar_clase_perdida(ramo: str, fecha: str, ensayo: bool = False) -> None:
    with _modificando(ensayo=ensayo) as datos:
        perdidas = datos.setdefault("clases_perdidas", [])
        if not any(p.get("ramo") == ramo and p.get("fecha") == fecha for p in perdidas):
            perdidas.append({"ramo": ramo, "fecha": fecha})


def anotar_clase_suspendida(ramo: str, fecha: str, ensayo: bool = False) -> None:
    """Una fecha sin clase del ramo (el 14-10-2026 el bot proponia anotar una clase
    suspendida como perdida, que es falso). No toca clases_perdidas: el estudiante
    no falto, asi que ni las hojas ni las clases de una evaluacion cambian. Solo
    proxima_clase la mira, para no dejar un recordatorio en una clase que no hubo."""
    with _modificando(ensayo=ensayo) as datos:
        suspendidas = datos.setdefault("clases_suspendidas", [])
        if not any(p.get("ramo") == ramo and p.get("fecha") == fecha for p in suspendidas):
            suspendidas.append({"ramo": ramo, "fecha": fecha})


def _mismo_texto(a: str, b: str) -> bool:
    return _normalizar(a) == _normalizar(b)


def _pendiente_a_cerrar(datos: dict, ramo: str, que: str, fecha: str | None) -> dict | None:
    """El unico pendiente sin hacer que calza con lo que dijo el estudiante. Si el
    ramo y el texto calzan con varios (mismo control en dos fechas) y no vino la
    fecha, ninguno: marcar el equivocado esconderia algo que falta por hacer."""
    candidatos = [p for p in pendientes_activos(datos)
                  if p.get("ramo") == ramo and _mismo_texto(p.get("que", ""), que)
                  and (fecha is None or p.get("fecha") == fecha)]
    return candidatos[0] if len(candidatos) == 1 else None


def marcar_hecho(ramo: str, que: str, fecha: str | None = None, ensayo: bool = False) -> bool:
    """Marca un pendiente como hecho. No se borra: /pendientes y la secretaria
    solo muestran los que faltan. False si ya no hay uno que calce."""
    with _modificando(ensayo=ensayo) as datos:
        p = _pendiente_a_cerrar(datos, ramo, que, fecha)
        if p is None:
            return False
        p["estado"] = "hecho"
    return True


def pendientes_activos(datos: dict) -> list[dict]:
    """Los pendientes que faltan por hacer. Los antiguos no traen estado: faltan."""
    return [p for p in datos.get("pendientes", []) if p.get("estado") != "hecho"]


def fijar_fecha_desde_chat(ramo: str, evaluacion: str, fecha: str, ensayo: bool = False) -> None:
    """Lo que el estudiante dijo por el chat pisa al calendario y a un anuncio
    dudoso. La confirmacion ya le advirtio si el profesor dijo otra cosa en clase."""
    with _modificando(ensayo=ensayo) as datos:
        ev = next(e for e in datos["ramos"][ramo] if e["nombre"] == evaluacion)
        ev.update(fecha=fecha, fuente="chat")
        for campo in ("fecha_posible", "clase", "textual"):
            ev.pop(campo, None)


def revisar_accion_del_chat(accion: dict, datos: dict) -> dict | None:
    """
    Valida lo que el bot quiere anotar y devuelve la accion limpia, con lo que el
    estudiante tiene que saber antes de confirmar (el campo "choque": una fecha que
    el profesor dijo en clase). None si no tiene sentido: ramo o evaluacion que no
    existen, fecha mal escrita, texto vacio o demasiado largo. Redactar la pregunta
    es cosa del bot.
    """
    tipo, ramo = accion.get("tipo"), accion.get("ramo")
    fecha = validar_fecha(accion.get("fecha"))
    if tipo == "hecho":
        # Antes que el chequeo del ramo: los pendientes de una reunion tienen como
        # ramo "REUNIÓN", que no esta entre los ramos con evaluaciones.
        que = accion.get("que")
        if not isinstance(que, str) or not _normalizar(que) or len(que) > 200:
            return None
        p = _pendiente_a_cerrar(datos, ramo, que, fecha if accion.get("fecha") else None)
        return {"tipo": tipo, "ramo": ramo, "fecha": p["fecha"], "que": p["que"]} if p else None
    evs = datos.get("ramos", {}).get(ramo)
    if not evs or not fecha:
        return None
    if tipo in ("clase_perdida", "clase_suspendida"):
        return {"tipo": tipo, "ramo": ramo, "fecha": fecha}
    if tipo == "fecha_evaluacion":
        ev = next((e for e in evs if e["nombre"] == accion.get("evaluacion")), None)
        if ev is None:
            return None
        choque = ({"clase": ev["clase"], "textual": ev["textual"]}
                  if ev.get("fuente") == "clase" and ev.get("textual") else None)
        return {"tipo": tipo, "ramo": ramo, "fecha": fecha, "evaluacion": ev["nombre"], "choque": choque}
    if tipo == "pendiente":
        que = accion.get("que")
        if not isinstance(que, str) or not que.strip() or len(que) > 200:
            return None
        return {"tipo": tipo, "ramo": ramo, "fecha": fecha, "que": que.strip()}
    return None


def evaluacion_de(evaluaciones: list[dict], fecha_clase: str) -> dict | None:
    """
    La evaluacion a la que pertenece una clase: la primera que todavia no
    ocurre ese dia. Una clase dictada el mismo dia de una evaluacion ya va a
    la siguiente. Si una fecha no esta confirmada (null), esa evaluacion se
    queda con todas las clases posteriores a la anterior, hasta que tenga
    fecha. Despues de la ultima evaluacion no pertenece a ninguna.

    Las fechas son ISO (AAAA-MM-DD), asi que compararlas como texto es
    compararlas como fechas.
    """
    for ev in evaluaciones:
        if ev.get("fecha") is None or ev["fecha"] > fecha_clase:
            return ev
    return None


def clases_de(evaluaciones: list[dict], nombre: str, fechas_clases: list[str]) -> list[str]:
    """
    Las clases que entran en una evaluacion. Por defecto, las que van desde la
    evaluacion anterior hasta esta. Si acumula (el examen siempre, una S2 solo
    si el profesor lo anuncio), entran todas las anteriores a su fecha.
    """
    ev = next(e for e in evaluaciones if e["nombre"] == nombre)
    if ev.get("acumula"):
        return sorted(f for f in fechas_clases if ev.get("fecha") is None or f < ev["fecha"])
    return sorted(f for f in fechas_clases if evaluacion_de(evaluaciones, f) is ev)


def _dd_mm(fecha_iso: str | None) -> str:
    return f"{fecha_iso[8:10]}-{fecha_iso[5:7]}" if fecha_iso else "sin fecha"


def resumen_para_skill(datos: dict, ramo: str, fecha_clase: str) -> str:
    """Lo que la skill necesita para asociar un anuncio a S1, S2 o Examen."""
    evs = datos.get("ramos", {}).get(ramo)
    if not evs:
        return ""
    lineas = []
    for e in evs:
        detalles = [_dd_mm(e.get("fecha"))]
        if e.get("fecha_posible"):
            detalles.append(f"posible {_dd_mm(e['fecha_posible'])}, sin confirmar")
        if e.get("tipo") in ("prueba", "trabajo"):
            detalles.append(e["tipo"])
        if e.get("fecha") and e["fecha"] <= fecha_clase:
            detalles.append("ya paso")
        lineas.append(f"- {e['nombre']}: " + ", ".join(detalles))
    return ("Evaluaciones conocidas de este ramo, para el campo `evaluaciones` del "
            "RESULTADO_ORQUESTADOR:\n" + "\n".join(lineas))


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def cita_respaldada(textual: str, transcripcion: str) -> bool:
    """
    Si la frase que el modelo dice que dijo el profesor esta de verdad en la
    transcripcion. Un anuncio cambia las fechas del semestre y el estudiante
    las cree sin revisarlas, asi que esto lo comprueba Python y no el modelo.

    El modelo une trozos con "..." y completa palabras entre corchetes, asi que
    se busca cada trozo por separado, sin tildes ni puntuacion. Calibrado el
    29-09-2026 contra las 54 citas de anuncios del semestre: 46 pasan. Las que
    no pasan son casi todas "seguro: false" o una palabra que el modelo corrigio
    sobre el audio, y en ese caso lo correcto es no confiar en la cita.
    """
    trozos = [_normalizar(t) for t in re.split(r"\.\.\.|…|\[[^\]]*\]", textual or "")]
    trozos = [t for t in trozos if len(t.split()) >= 3]
    base = _normalizar(transcripcion)
    return bool(trozos) and all(t in base for t in trozos)


def validar_fecha(valor) -> str | None:
    try:
        return date.fromisoformat(valor).isoformat() if isinstance(valor, str) else None
    except ValueError:
        return None


def aplicar_anuncios(datos: dict, ramo: str, anuncios: list, fecha_clase: str,
                     transcripcion: str) -> list[str]:
    """
    Aplica al archivo los anuncios de evaluacion de una clase y devuelve, en
    palabras, que cambio, para la notificacion.

    Un anuncio solo cambia un dato si viene "seguro" y su cita esta en la
    transcripcion. Uno dudoso nunca pisa nada: su fecha queda como
    fecha_posible y se avisa, porque "sin confirmar" y "no se dijo" son cosas
    distintas para quien estudia. Un anuncio que no calza con S1, S2 o Examen
    (un control, una tarea) no toca el archivo: queda en la hoja de la clase.
    """
    evs = {e["nombre"]: e for e in datos.get("ramos", {}).get(ramo, [])}
    cambios = []
    for a in anuncios if isinstance(anuncios, list) else []:
        ev = evs.get(a.get("evaluacion")) if isinstance(a, dict) else None
        if ev is None:
            continue
        if ev.get("fuente") == "clase" and ev.get("clase", "") > fecha_clase:
            continue  # una clase posterior ya dijo algo mas nuevo
        fecha = validar_fecha(a.get("fecha"))
        if a.get("seguro") is True and cita_respaldada(a.get("textual"), transcripcion):
            nuevos = {}
            if fecha and fecha != ev.get("fecha"):
                nuevos["fecha"] = fecha
            if a.get("tipo") in ("prueba", "trabajo") and a["tipo"] != ev.get("tipo"):
                nuevos["tipo"] = a["tipo"]
            if isinstance(a.get("acumula"), bool) and a["acumula"] != bool(ev.get("acumula")):
                nuevos["acumula"] = a["acumula"]
            if not nuevos:
                continue
            partes = []
            if "fecha" in nuevos:
                partes.append(f"fecha {_dd_mm(ev.get('fecha'))} a {_dd_mm(fecha)}")
                ev.pop("fecha_posible", None)
            if "tipo" in nuevos:
                partes.append(f"es {nuevos['tipo']}")
            if "acumula" in nuevos:
                partes.append("acumula la materia anterior" if nuevos["acumula"]
                              else "no acumula la materia anterior")
            ev.update(nuevos, fuente="clase", clase=fecha_clase, textual=a["textual"])
            cambios.append(f"{ev['nombre']}: " + ", ".join(partes)
                           + f" (anuncio del {_dd_mm(fecha_clase)})")
        elif fecha and fecha != ev.get("fecha") and fecha != ev.get("fecha_posible"):
            ev["fecha_posible"] = fecha
            cambios.append(f"{ev['nombre']}: posible fecha {_dd_mm(fecha)}, sin confirmar "
                           f"(sigue {_dd_mm(ev.get('fecha'))})")
    return cambios


DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def dia_del_ramo(ramo: str, config: dict) -> int | None:
    """El dia de la semana en que se dicta el ramo (0 = lunes), segun config.json.
    None para un ramo anexo sin dia fijo."""
    for dia, info in (config.get("ramos") or {}).items():
        if info.get("nombre") == ramo and dia in DIAS:
            return DIAS.index(dia)
    return None


def proxima_clase(fecha_clase: str, dia_semana: int, feriados: list[str]) -> str:
    """La clase siguiente del ramo: su proximo dia de la semana que no sea feriado."""
    d = date.fromisoformat(fecha_clase) + timedelta(days=1)
    while d.weekday() != dia_semana or d.isoformat() in feriados:
        d += timedelta(days=1)
    return d.isoformat()


def fechas_sin_clase(datos: dict, ramo: str) -> list[str]:
    """Los feriados y las clases suspendidas del ramo: los dias en que no hay clase."""
    return datos.get("feriados", []) + [s["fecha"] for s in datos.get("clases_suspendidas", [])
                                         if s.get("ramo") == ramo]


def aplicar_pedidos(datos: dict, ramo: str, avisos: list, fecha_clase: str,
                    transcripcion: str, dia_semana: int | None) -> list[str]:
    """
    Anota lo que el profesor pidio para una fecha ("para la proxima clase traigan
    el caso leido"), para que la secretaria lo recuerde ese dia a las 9:00.
    Devuelve, en palabras, lo anotado.

    Mismas reglas que los anuncios de evaluacion: solo un pedido seguro y cuya
    cita esta en la transcripcion. Un recordatorio falso hace llegar a clase con
    la tarea equivocada, asi que ante la duda no se anota (igual queda en la hoja
    de la clase). La fecha la calcula Python: la que dijo el profesor, o la
    proxima clase del ramo, saltando feriados.
    """
    pendientes = datos.setdefault("pendientes", [])
    anotados = []
    for a in avisos if isinstance(avisos, list) else []:
        if not isinstance(a, dict) or not a.get("que") or a.get("seguro") is not True:
            continue
        if not cita_respaldada(a.get("textual"), transcripcion):
            continue
        fecha = validar_fecha(a.get("fecha"))
        if fecha is None and a.get("para_la_proxima_clase") is True and dia_semana is not None:
            fecha = proxima_clase(fecha_clase, dia_semana, fechas_sin_clase(datos, ramo))
        if fecha is None or fecha <= fecha_clase:
            continue
        if any((x.get("ramo"), x.get("fecha"), x.get("que")) == (ramo, fecha, a["que"]) for x in pendientes):
            continue
        pendientes.append({"ramo": ramo, "fecha": fecha, "que": a["que"], "origen": "clase",
                           "textual": a["textual"], "clase": fecha_clase})
        anotados.append(f"{a['que']} (para el {_dd_mm(fecha)})")
    return anotados
