"""
Hoja de estudio de una evaluacion (Solemne 1, Solemne 2, Examen).

Por que existe. El estudiante tiene poco tiempo y estudia para la proxima
prueba, no para el ramo entero. Esta hoja junta las clases que entran en esa
evaluacion, con un hilo conductor entre ellas, en un HTML independiente que
basta solo para estudiar y sacar sobre 6.0. Reemplaza a las hojas de solemne
que antes se armaban a mano en conversaciones aparte.

Como se arma. Las clases que entran las decide evaluaciones.py. Se leen sus
notas de aprendizaje del vault (ya revisadas contra la grabacion, a diferencia
de los HTML de clase, que son una condensacion) y el material de OneDrive con
lista blanca (ver onedrive.py). La redaccion es una llamada al modelo sin
herramientas, igual que la hoja de clase, con las mismas instrucciones mas las
de plantilla_hoja/instrucciones_evaluacion.md, que mandan cuando chocan.

Lo que arma Python y no el modelo: la cabecera y la tarjeta "La evaluacion"
(fecha, tipo, clases que entran, clases perdidas y material usado). Son los
datos que no se pueden inventar.

Cuando se rehace. Despues de cada clase, solo la de la proxima evaluacion de
ese ramo. Cuando pasa su fecha queda congelada. Se sobrescribe en el mismo
lugar, pero solo si la version nueva paso la validacion: una corrida fallida
deja la anterior y avisa.

A mano, para una evaluacion cualquiera (por ejemplo, la S1 hacia atras):
    python3 -m orquestador.hoja_evaluacion "<RAMO>" <S1|S2|Examen>
"""
import html
import os
import re
from datetime import date
from pathlib import Path

from . import estado_vivo, evaluaciones, material_ramo, onedrive
from .carpetas import resolver_carpeta_ramo
from .config import cargar_config
from .ensayo import es_ensayo
from .hoja_html import (
    MARCA_FIN,
    MARCA_INICIO,
    RUTA_INSTRUCCIONES,
    _fecha_legible,
    envolver_documento,
    pedir_hoja,
)
from .nombres import fecha_corta, ramo_legible
from .ramo_por_nombre import normalizar
from .notificaciones import notificar_aviso, notificar_hoja_evaluacion, notificar_progreso

RUTA_INSTRUCCIONES_EVALUACION = Path(__file__).parent / "plantilla_hoja" / "instrucciones_evaluacion.md"

NOMBRES = {"S1": "Solemne 1", "S2": "Solemne 2", "Examen": "Examen", "Control": "Control"}

TIMEOUT_SEGUNDOS = 25 * 60

# Una hoja que junta varias clases y queda por debajo de esto no condenso nada:
# se trata como falla y la version anterior se conserva.
MINIMO_PALABRAS = 600

# Seguro contra un prompt desbocado, no un limite que se espere tocar. El
# examen lee todas las clases del ramo (decision del estudiante, 29-09-2026):
# unas 14 notas de 24.000 a 50.000 caracteres, cerca de 630.000 caracteres o
# 170.000 tokens. El modelo que usa el plan (claude-sonnet-5) tiene una ventana
# de 1.000.000 de tokens y 64.000 de salida, medido ese dia con model_usage del
# SDK. Este tope deja la entrada bajo 400.000 tokens.
MAX_CARACTERES_ENTRADA = 1_500_000

_NOTA = re.compile(r"^Aprendizaje - Clase (\d{4}-\d{2}-\d{2}) - (.+)\.md$")

# Como se nombra cada tipo de material, en la tarjeta y en el prompt.
ROTULOS = {"programa": "programa del ramo", "enunciado": "enunciado o pauta del trabajo",
           "evaluacion pasada": "pauta de un semestre anterior",
           "lámina": "lámina del profesor", "ayudantía": "ayudantía",
           "código R": "código R del profesor", "control anterior": "control anterior"}


def nombre_archivo(ramo: str, evaluacion: str, fecha: str | None = None) -> str:
    # Un ramo tiene varios controles: el del control lleva su fecha para no pisar el anterior.
    nombre = f"Control {fecha_corta(fecha)}" if evaluacion == "Control" and fecha else NOMBRES.get(evaluacion, evaluacion)
    return f"{nombre} - {ramo_legible(ramo)}.html"


# --------------------------------------------------------------------------
# Controles
# --------------------------------------------------------------------------
#
# Un control no esta en evaluaciones.json como las solemnes, que salen del
# calendario: se anota como pendiente cuando el profesor lo anuncia en clase.
# Su hoja se pide a mano (pedido del estudiante el 16-10-2026, para un control
# de ese dia). No se rehace sola despues de cada clase.

def evaluacion_control(datos: dict, ramo: str, fecha: str) -> dict:
    """La evaluacion de un control, con lo que dijo el profesor al anunciarlo si se
    anoto. Sin anuncio se arma igual, solo con la fecha."""
    ev = {"nombre": "Control", "fecha": fecha, "tipo": "prueba", "acumula": False}
    anuncio = next((p for p in datos.get("pendientes", [])
                    if p.get("ramo") == ramo and p.get("fecha") == fecha
                    and "control" in normalizar(p.get("que") or "")), None)
    if anuncio and anuncio.get("textual"):
        ev.update(fuente="clase", textual=anuncio["textual"], clase=anuncio.get("clase"))
    return ev


def clases_del_control(evs: list[dict], fechas: list[str], fecha: str) -> set[str]:
    """Las clases desde la ultima evaluacion antes del control hasta el dia anterior.
    ponytail: un control anterior no corta el alcance, porque los controles no
    quedan anotados como evaluacion. Si importa, anotarlos en evaluaciones.json."""
    desde = max((e["fecha"] for e in evs if e.get("fecha") and e["fecha"] < fecha), default="")
    return {f for f in fechas if desde < f < fecha}


def clases_del_ramo(carpeta: Path) -> list[tuple[str, str, Path]]:
    """(fecha, titulo, ruta) de cada nota de aprendizaje de la carpeta del ramo."""
    clases = []
    for ruta in carpeta.glob("Aprendizaje - Clase *.md"):
        m = _NOTA.match(ruta.name)
        if m:
            clases.append((m.group(1), m.group(2), ruta))
    return sorted(clases)


def tarjeta_evaluacion(ev: dict, clases: list[tuple[str, str, str]], perdidas: list[dict],
                       material: list[dict]) -> str:
    """La tarjeta con los datos que no se pueden inventar, armada sin modelo."""
    e = lambda s: html.escape(str(s or ""), quote=False)  # noqa: E731
    cl, duda = '<span class="f cl">CL</span>', '<span class="f duda">?</span>'
    datos = []
    if ev.get("fecha"):
        datos.append(f"<b>Fecha:</b> {e(_fecha_legible(ev['fecha']))}")
    else:
        posible = (f" Posible: {e(_fecha_legible(ev['fecha_posible']))}."
                   if ev.get("fecha_posible") else "")
        datos.append(f"<b>Fecha:</b> sin confirmar.{posible}{duda}")
    tipo = {"prueba": "prueba", "trabajo": "trabajo"}.get(ev.get("tipo"), "no se sabe todavía")
    datos.append(f"<b>Tipo:</b> {tipo}")
    if ev.get("acumula"):
        datos.append("<b>Materia:</b> entra también la de las evaluaciones anteriores")
    if ev.get("fuente") == "clase" and ev.get("textual"):
        datos.append(f"<b>Según el anuncio de la clase del {e(_fecha_legible(ev.get('clase')))}:</b> "
                     f'<span class="cita">"{e(ev["textual"])}"</span>{cl}')
    elif ev.get("fuente") == "calendario":
        datos.append("<b>De dónde sale:</b> tu calendario. Si el profesor anuncia otra cosa en "
                     "clase, esa manda.")
    if ev.get("nota"):
        datos.append(e(ev["nota"]))

    partes = ['<div class="t pro plena pidio"><h3><span class="n">!</span>La evaluación</h3><ul>',
              "".join(f"<li>{d}</li>" for d in datos), "</ul>",
              f'<span class="clave">Clases que entran ({len(clases)})</span><ul>',
              "".join(f"<li>{e(_fecha_legible(f))}: {e(t)}</li>" for f, t, _ in clases), "</ul>"]
    if perdidas:
        partes += ['<span class="clave">Clases a las que faltaste</span><ul>',
                   "".join(f"<li>{e(_fecha_legible(p['fecha']))}: su materia no está en esta "
                           f"hoja.{duda}</li>" for p in perdidas), "</ul>"]
    if material:
        partes += ['<span class="clave">Material de la carpeta del ramo</span><ul>',
                   "".join(f"<li>{e(m['archivo'])} ({ROTULOS.get(m['categoria'], m['categoria'])}"
                           + (f", clase del {e(_fecha_legible(m['clase']))}" if m.get("clase") else "") + ")"
                           + (f". No se pudo leer: es escaneado.{duda}" if m["sin_texto"] else "")
                           + "</li>" for m in material), "</ul>"]
    return "".join(partes) + "</div>"


# La practica va al final de la hoja, asi que su titulo dice que la hoja llego entera.
# El 18-10-2026 el modelo paro a la mitad de la materia y dejo dentro de la hoja una
# pregunta para el estudiante ("¿Quieres que te devuelva la hoja completa?"). Pasaba el
# minimo de palabras y reemplazo a la version buena. Se busca el titulo de seccion y no
# la palabra suelta: ese mensaje tambien nombraba la seccion que faltaba. El titulo
# llega como <h2><span>Práctica</span><span class="peso">...</span></h2>: la primera
# version del chequeo no admitia el <span> y rechazo dos hojas buenas ese mismo dia.
_TITULO_PRACTICA = re.compile(r"<h2[^>]*>(?:\s*<[^>]+>)*\s*Pr[aá]ctica\b", re.IGNORECASE)


def llego_entera(cuerpo: str) -> bool:
    return bool(_TITULO_PRACTICA.search(cuerpo))


def _construir_prompt(ramo: str, ev: dict, tipo: str, clases: list[tuple[str, str, str]],
                      material: list[dict]) -> str:
    partes = [
        f"Ramo: {ramo_legible(ramo)}",
        f"Evaluación: {NOMBRES.get(ev['nombre'], ev['nombre'])}",
        f"Fecha: {ev.get('fecha') or 'sin confirmar'}",
        f"Tipo: {tipo}",
        f"Clases que entran: {len(clases)}",
    ]
    for i, (fecha, titulo, texto) in enumerate(clases, start=1):
        partes += ["", f"===CLASE {i} DE {len(clases)}: {fecha} - {titulo}===", texto,
                   "===FIN DE LA CLASE==="]
    for m in material:
        if not m["sin_texto"]:
            clase = f", clase del {m['clase']}" if m.get("clase") else ""
            partes += ["", f"===MATERIAL: {ROTULOS.get(m['categoria'], m['categoria'])} "
                           f"({m['archivo']}{clase})===", m["texto"], "===FIN DEL MATERIAL==="]
    partes += ["", "Redacta la hoja de esta evaluación siguiendo tus instrucciones. Devuelve solo "
                   f"el HTML entre {MARCA_INICIO} y {MARCA_FIN}."]
    return "\n".join(partes)


def _escribir(ruta: Path, documento: str) -> None:
    # Aparte y de una vez: la version anterior sigue entera hasta el ultimo instante.
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_text(documento, encoding="utf-8")
    os.replace(temporal, ruta)


async def generar(ramo: str, nombre: str, config: dict, datos: dict | None = None,
                  en_el_pipeline: bool = False, fecha_control: str | None = None) -> Path | None:
    """
    Rehace la hoja de una evaluacion. Devuelve su ruta, o None si no habia
    clases que juntar o si la redaccion fallo (y en ese caso avisa y la hoja
    anterior queda intacta).
    """
    datos = datos or evaluaciones.cargar()
    evs = datos["ramos"][ramo]
    es_control = nombre == "Control"
    ev = (evaluacion_control(datos, ramo, fecha_control) if es_control
          else next(e for e in evs if e["nombre"] == nombre))
    nombre_largo = f"Control del {_fecha_legible(fecha_control)}" if es_control else NOMBRES.get(nombre, nombre)

    # El cache de carpetas no se guarda desde aqui: la clase ya lo resolvio y lo
    # guardo antes (finalizar_clase), y escribir config.json desde una etapa que
    # tambien corre a mano es arriesgar la configuracion por un atajo de busqueda.
    carpeta, _ = resolver_carpeta_ramo(ramo, config["rutas"]["vault_obsidian"], config)
    todas = clases_del_ramo(carpeta)
    en_alcance = (clases_del_control(evs, [f for f, _, _ in todas], fecha_control) if es_control
                  else set(evaluaciones.clases_de(evs, nombre, [f for f, _, _ in todas])))
    clases = [(f, t, r.read_text(encoding="utf-8")) for f, t, r in todas if f in en_alcance]
    if not clases:
        return None

    tipo = ev.get("tipo") if ev.get("tipo") in ("prueba", "trabajo") else "prueba"
    try:
        material = onedrive.leer_material(ramo, nombre, tipo,
                                          (config.get("programas") or {}).get(ramo), config)
    except Exception:
        material = []  # sin OneDrive la hoja se arma igual, solo con las clases
    # Las laminas y ayudantias de las clases que entran (ver material_ramo). Se
    # comparan con las notas de todas las clases del ramo para asignarlas.
    material += material_ramo.material_de_la_evaluacion(
        ramo, config, {f: r.read_text(encoding="utf-8") for f, _, r in todas}, en_alcance, es_control)
    perdidas = [p for p in datos.get("clases_perdidas", []) if p.get("ramo") == ramo and (
        clases_del_control(evs, [p.get("fecha", "")], fecha_control) if es_control
        else evaluaciones.clases_de(evs, nombre, [p.get("fecha", "")]))]

    prompt = _construir_prompt(ramo, ev, tipo, clases, material)
    if len(prompt) > MAX_CARACTERES_ENTRADA:
        notificar_aviso(f"No se actualizó la hoja de la {nombre_largo}",
                        f"{ramo_legible(ramo)}: junta demasiado material para una sola lectura del "
                        "modelo. La versión anterior sigue igual.")
        return None
    if en_el_pipeline:
        # La hoja de la clase ya se aviso. Esto tarda unos minutos mas, y la
        # barra de menu tiene que decir que sigue trabajando y en que.
        notificar_progreso(estado_vivo.PASO_DOCUMENTO, f"Actualizando la hoja de la {nombre_largo}")
    system_prompt = (RUTA_INSTRUCCIONES.read_text(encoding="utf-8") + "\n\n"
                     + RUTA_INSTRUCCIONES_EVALUACION.read_text(encoding="utf-8"))
    cuerpo, motivo = await pedir_hoja(system_prompt, prompt, "hoja_evaluacion",
                                      f"{ramo} {nombre}", MINIMO_PALABRAS)
    if cuerpo is None:
        notificar_aviso(f"No se actualizó la hoja de la {nombre_largo}",
                        f"{ramo_legible(ramo)}: {motivo}. La versión anterior sigue igual.")
        return None
    if tipo != "trabajo" and not llego_entera(cuerpo):
        notificar_aviso(f"No se actualizó la hoja de la {nombre_largo}",
                        f"{ramo_legible(ramo)}: salió cortada, sin la sección de práctica. "
                        "La versión anterior sigue igual.")
        return None

    fecha = html.escape(_fecha_legible(ev["fecha"]) if ev.get("fecha") else "fecha sin confirmar")
    # El tipo que se asume para redactar no se muestra como sabido: la
    # cabecera decia "prueba" mientras la tarjeta decia "no se sabe todavia".
    tipo_sabido = f" &middot; {ev['tipo']}" if ev.get("tipo") in ("prueba", "trabajo") else ""
    documento = envolver_documento(
        f"{nombre_largo} - {ramo_legible(ramo)}",
        f"{html.escape(ramo_legible(ramo))} &middot; {fecha}{tipo_sabido}",
        nombre_largo,
        ("Hoja del trabajo. Junta lo que el profesor pidió y la materia que sirve para hacerlo."
         if tipo == "trabajo" else
         f"Hoja de estudio de la evaluación. Junta las {len(clases)} clases que entran, con un "
         "hilo conductor entre ellas."),
        "<b>CL</b> dicho en una de las clases que entran &nbsp;&middot;&nbsp; <b>MP</b> de una lámina, "
        "ayudantía o código del profesor &nbsp;&middot;&nbsp; <b>PA</b> de una pauta o un control "
        "anterior &nbsp;&middot;&nbsp; <b>?</b> dudoso por audio o reconstruido, "
        "conviene verificar &nbsp;&middot;&nbsp; <b>+</b> agregado aquí, no se dijo en clase",
        tarjeta_evaluacion(ev, clases, perdidas, material),
        cuerpo,
        "Fuente: las notas de aprendizaje de las clases que entran, revisadas contra la grabación"
        + (", y el material de la carpeta del ramo que se nombra arriba" if material else "")
        + ". Lo marcado con <b>PA</b> sale de pautas o controles anteriores y lo marcado con "
        "<b>+</b> es agregado.",
    )
    ruta = Path(config["rutas"]["output"]) / ramo / "Evaluaciones" / nombre_archivo(ramo, nombre, fecha_control)
    _escribir(ruta, documento)

    en_onedrive = False
    if not es_ensayo(config):
        en_onedrive = onedrive.copiar_pisando(ruta, ramo, config) is not None
        if not en_onedrive:
            notificar_aviso(f"La hoja de la {nombre_largo} no llegó a OneDrive",
                            f"{ramo_legible(ramo)}: quedó en Output, pero no en tu carpeta del semestre.")
    notificar_hoja_evaluacion(ramo_legible(ramo), nombre_largo, len(clases), ruta, en_onedrive)
    return ruta


async def actualizar_tras_clase(ramo: str, fecha_clase: str, config: dict) -> Path | None:
    """La hoja de la evaluacion a la que pertenece esta clase, si todavia no paso."""
    try:
        datos = evaluaciones.cargar()
    except (OSError, ValueError):
        return None
    evs = datos.get("ramos", {}).get(ramo)
    ev = evaluaciones.evaluacion_de(evs, fecha_clase) if evs else None
    if ev is None or (ev.get("fecha") and ev["fecha"] < date.today().isoformat()):
        return None  # sin evaluacion por delante, o ya paso y su hoja queda congelada
    return await generar(ramo, ev["nombre"], config, datos, en_el_pipeline=True)


if __name__ == "__main__":
    import sys

    import anyio

    _datos = evaluaciones.cargar()
    _control = len(sys.argv) == 4 and sys.argv[2] == "Control"
    if not (len(sys.argv) == 3 or _control) or sys.argv[1] not in _datos["ramos"]:
        print('Uso: python3 -m orquestador.hoja_evaluacion "<RAMO>" <S1|S2|Examen>')
        print('     python3 -m orquestador.hoja_evaluacion "<RAMO>" Control <AAAA-MM-DD>')
        print("\n  Rehace la hoja de una evaluacion desde las notas de sus clases.")
        print("  Cuesta una llamada al modelo. Reemplaza la hoja anterior solo si sale bien.")
        for _ramo, _evs in _datos["ramos"].items():
            print(f"\n  {_ramo}: " + ", ".join(e["nombre"] for e in _evs))
        raise SystemExit(1)

    async def _una():
        with anyio.fail_after(TIMEOUT_SEGUNDOS):
            return await generar(sys.argv[1], sys.argv[2], cargar_config(), _datos,
                                 fecha_control=sys.argv[3] if _control else None)

    _ruta = anyio.run(_una)
    print(f"  -> {_ruta}" if _ruta else "  No se generó: no hay clases que entren o falló la redacción.")
