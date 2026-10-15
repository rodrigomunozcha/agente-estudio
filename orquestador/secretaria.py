"""
La secretaria: el mensaje de las 9:00 por Telegram.

De lunes a viernes trae lo que el profesor pidio para hoy y las evaluaciones de
los proximos 7 dias. Todos los dias, tambien el fin de semana, avisa cada
evaluacion (solemnes, examenes y controles) dos dias antes, la vispera, con su
hoja adjunta, y el mismo dia (pedido del estudiante, 08-10-2026). Si no hay nada, no escribe: un mensaje
diario vacio se aprende a ignorar. Lo decidio el estudiante el 29-09-2026, y
pidio explicitamente que no diga que clase hay hoy, porque eso ya lo sabe.

Todo sale de evaluaciones.json, sin llamar al modelo: son fechas y listas, y
Python no se equivoca ni cobra por leerlas. Lo lanza un LaunchAgent a las 9:00
(ver launchd/instalar_bot.sh). El bot usa las mismas funciones para /hoy y
/evaluaciones.
"""
import re
import time
from datetime import date, timedelta
from pathlib import Path

from . import evaluaciones, pregunta_ramo, telegram
from .carpetas import resolver_carpeta_ramo
from .config import cargar_config
from .hoja_evaluacion import NOMBRES, clases_del_ramo, nombre_archivo, ramo_legible

DIAS_ADELANTE = 7
# A las 9:00 el Mac puede estar recien despertando y sin red todavia: el 12-10-2026
# el mensaje se perdio con "sin conexión" y nadie lo reintento. Se reintenta una
# vez por minuto, hasta 15 minutos, y solo si el error es la falta de conexion.
INTENTOS_SIN_CONEXION = 15
SEGUNDOS_ENTRE_INTENTOS = 60

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}"


def horario(config: dict) -> str:
    """Para el bot, que sin esto no sabia ni a que hora tiene clases (30-09-2026).
    Sale de config.json, que se lleno a mano desde el calendario: el pipeline no
    lo lee (ver evaluaciones.py)."""
    return "\n".join(
        f"• {d.replace('miercoles', 'miércoles')}: {ramo_legible(r['nombre'])}, "
        f"{r.get('inicio', '?')} a {r.get('fin', '?')}, sala {r.get('sala', '?')}"
        for d, r in (config.get("ramos") or {}).items())


def hoja_de(ramo: str, evaluacion: str, config: dict) -> Path | None:
    ruta = Path(config["rutas"]["output"]) / ramo / "Evaluaciones" / nombre_archivo(ramo, evaluacion)
    return ruta if ruta.is_file() else None


# Que pendiente es una evaluacion. Los controles no tienen lugar propio en
# evaluaciones.json: llegan como pedidos del profesor ("Control 2", "Hay un
# control la proxima semana") o desde el chat, asi que se reconocen por el texto.
# ponytail: palabras clave, un campo "tipo" en el pendiente si alguna se escapa.
_ES_EVALUACION = re.compile(r"\b(control|prueba|quiz|test|solemne|examen)", re.IGNORECASE)

# Cuantos dias antes se avisa una evaluacion: el dia mismo, la vispera y dos
# dias antes. Lo pidio el estudiante el 08-10-2026, para controles y solemnes.
_AVISOS = {0: "🚨 Hoy", 1: "⏰ Mañana", 2: "📆 Pasado mañana"}


def _evaluaciones(datos: dict, hoy: date, limite: str) -> list[tuple[str, str, str, str | None]]:
    """(fecha, ramo, como se nombra, nombre de la evaluacion o None si es un control)
    de hoy hasta el limite, en orden."""
    lista = [(ev["fecha"], ramo, f"{NOMBRES.get(ev['nombre'], ev['nombre'])} de {ramo_legible(ramo)}"
              + (f" ({ev['tipo']})" if ev.get("tipo") in ("prueba", "trabajo") else ""), ev["nombre"])
             for ramo, evs in datos.get("ramos", {}).items() for ev in evs
             if ev.get("fecha") and hoy.isoformat() <= ev["fecha"] <= limite]
    lista += [(p["fecha"], p["ramo"], f"{ramo_legible(p['ramo'])}: {p['que']}", None)
              for p in evaluaciones.pendientes_activos(datos)
              if hoy.isoformat() <= p.get("fecha", "") <= limite and p.get("tipo") != "seguimiento"
              and _ES_EVALUACION.search(p.get("que", ""))]
    return sorted(lista)


def resumen(hoy: date, datos: dict, config: dict, fin_de_semana: bool = False) -> tuple[str | None, list[Path]]:
    """El texto del mensaje (None si no hay nada que decir) y las hojas que se
    adjuntan: las de las evaluaciones de mañana. El fin de semana solo van los
    avisos de evaluaciones."""
    limite = (hoy + timedelta(days=DIAS_ADELANTE)).isoformat()
    evals = _evaluaciones(datos, hoy, limite)
    dias = lambda f: (date.fromisoformat(f) - hoy).days
    alertas = [(f, r, nombre, ev) for f, r, nombre, ev in evals if dias(f) in _AVISOS]
    pedidos = [p for p in evaluaciones.pendientes_activos(datos)
               if p.get("fecha") == hoy.isoformat() and p.get("tipo") != "seguimiento"
               and not _ES_EVALUACION.search(p.get("que", ""))]

    # Se lee en el telefono, caminando: un bloque por tema, cada uno con su emoji,
    # y lo mas urgente arriba (pedido del estudiante, 30-09-2026).
    partes = []
    for f, r, nombre, ev in alertas:
        linea = f"{_AVISOS[dias(f)]}: {nombre}"
        if dias(f) == 1 and ev:
            linea += "\n" + ("📎 Te adjunto su hoja" if hoja_de(r, ev, config)
                             else "📝 Todavía no hay hoja para esa evaluación")
        partes.append(linea)
    if not fin_de_semana:
        if pedidos:
            de_clase = [p for p in pedidos if p.get("origen") != "reunion"]
            de_reunion = [p for p in pedidos if p.get("origen") == "reunion"]
            if de_clase:
                # Cada pendiente dice su origen (evaluaciones.cargar lo completa en
                # los antiguos): lo del chat no trae cita, solo lo que el dijo.
                partes.append("✅ Para hoy, en tus ramos\n" + "\n".join(
                    f"• {ramo_legible(p['ramo'])}: {p['que']}"
                    + ("\n  💬 Me lo contaste por el chat" if p.get("origen") == "chat"
                       else f"\n  💬 \"{p.get('textual', '')}\" (clase del {_fecha(p['clase'])})")
                    for p in de_clase))
            if de_reunion:
                partes.append("✅ Para hoy, de tus reuniones\n" + "\n".join(
                    f"• {p['que']}\n  🤝 {p.get('reunion', 'Reunión')} ({_fecha(p['clase'])})"
                    for p in de_reunion))
        otras = [(f, nombre) for f, _, nombre, _ in evals if dias(f) not in _AVISOS]
        if otras:
            partes.append(f"📅 Evaluaciones de los próximos {DIAS_ADELANTE} días\n" + "\n".join(
                f"• {_fecha(f)}: {nombre}" for f, nombre in otras))
    if not partes:
        return None, []
    hojas = [h for f, r, _, ev in alertas if dias(f) == 1 and ev and (h := hoja_de(r, ev, config))]
    return "☀️ Buenos días\n\n" + "\n\n".join(partes), hojas


def lista_pendientes(datos: dict, hoy: date) -> str:
    """Para /pendientes: lo que falta por hacer, de hoy en adelante, de clases y
    de reuniones, agrupado por dia. Lo que ya paso no se lista, ni lo que el
    estudiante marco como hecho."""
    futuros = sorted((p for p in evaluaciones.pendientes_activos(datos) if p.get("fecha", "") >= hoy.isoformat()),
                     key=lambda p: p["fecha"])
    if not futuros:
        return "🙌 No tienes pendientes anotados de hoy en adelante."
    lineas, dia = ["📋 Tus pendientes"], None
    for p in futuros:
        if p["fecha"] != dia:
            dia = p["fecha"]
            lineas.append(f"\n📆 {_fecha(dia)}")
        origen = (f"🤝 {p.get('reunion', 'Reunión')}" if p.get("origen") == "reunion"
                  else f"🏫 {ramo_legible(p['ramo'])}")
        lineas.append(f"• {p['que']}\n  {origen}")
    return "\n".join(lineas)


def tabla_evaluaciones(datos: dict, config: dict, hoy: date) -> str:
    """Para /evaluaciones: cada evaluacion con su fecha, tipo y las clases que
    entran en la proxima de cada ramo."""
    bloques = []
    for ramo, evs in datos.get("ramos", {}).items():
        try:
            carpeta, _ = resolver_carpeta_ramo(ramo, config["rutas"]["vault_obsidian"], config)
            fechas = [f for f, _, _ in clases_del_ramo(carpeta)]
        except Exception:
            fechas = []
        proxima = evaluaciones.evaluacion_de(evs, hoy.isoformat())
        lineas = [f"🏫 {ramo_legible(ramo)}"]
        for ev in evs:
            nombre = NOMBRES.get(ev["nombre"], ev["nombre"])
            tipo = f" ({ev['tipo']})" if ev.get("tipo") in ("prueba", "trabajo") else ""
            if ev.get("fecha") and ev["fecha"] < hoy.isoformat():
                lineas.append(f"✔️ {nombre}: ya pasó ({_fecha(ev['fecha'])})")
                continue
            cuando = (_fecha(ev["fecha"]) if ev.get("fecha") else "sin confirmar"
                      + (f", posible {_fecha(ev['fecha_posible'])}" if ev.get("fecha_posible") else ""))
            lineas.append(f"{'👉' if ev is proxima else '•'} {nombre}: {cuando}{tipo}")
            if ev is proxima:
                entran = evaluaciones.clases_de(evs, ev["nombre"], fechas)
                lineas.append(f"   📚 Entran {len(entran)} clase{'s' if len(entran) != 1 else ''}"
                              + (": " + ", ".join(f[8:10] + "-" + f[5:7] for f in entran) if entran else ""))
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques) or "📅 Todavía no hay evaluaciones anotadas."


def seguimientos(datos: dict, hoy: date) -> list[tuple[str, dict]]:
    """(texto, botones) de cada promesa de una reunion que vence hoy, o que vencio y
    sigue sin respuesta: si el Mac estuvo apagado ese dia, se pregunta al siguiente.
    Los botones los atiende el bot (bot.atender_boton), que es el que escucha a Telegram."""
    salida = []
    for p in sorted(evaluaciones.pendientes_activos(datos), key=lambda x: x.get("fecha", "")):
        if p.get("tipo") != "seguimiento" or p.get("fecha", "") > hoy.isoformat():
            continue
        clave = evaluaciones.clave_pendiente(p)
        texto = (f"⏳ ¿Cumplieron?\n• {p['que']}\n🤝 {p.get('reunion', 'Reunión')}"
                 + (f" ({_fecha(p['clase'])})" if p.get("clase") else ""))
        salida.append((texto, telegram.botones([("Sí", f"seg:si:{clave}"), ("No, pospón 1 semana", f"seg:no:{clave}"),
                                                ("Descartar", f"seg:x:{clave}")], por_fila=3)))
    return salida


def _enviar_con_paciencia(funcion, *args, **kwargs) -> None:
    for intento in range(1, INTENTOS_SIN_CONEXION + 1):
        try:
            funcion(*args, **kwargs)
            return
        except RuntimeError as e:
            if "sin conexión" not in str(e) or intento == INTENTOS_SIN_CONEXION:
                raise
            time.sleep(SEGUNDOS_ENTRE_INTENTOS)


def main() -> None:
    config = cargar_config()
    hoy = date.today()
    datos = evaluaciones.cargar()
    texto, hojas = resumen(hoy, datos, config, fin_de_semana=hoy.weekday() >= 5)
    if texto is not None:
        _enviar_con_paciencia(telegram.enviar, texto)
        for hoja in hojas:
            _enviar_con_paciencia(telegram.enviar_documento, hoja, hoja.stem)
    for texto_seg, botones in seguimientos(datos, hoy):
        _enviar_con_paciencia(telegram.enviar, texto_seg, teclado=botones)
    # Las grabaciones que siguen esperando de que son se recuerdan una vez, aqui: es lo
    # unico que corre todos los dias aunque no llegue nada nuevo.
    pregunta_ramo.recordar()


if __name__ == "__main__":
    main()
