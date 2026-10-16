"""
Material del profesor para la skill y el revisor: el programa del ramo y, si se
encuentra, la lamina de esta clase.

Por que. En la clase del 15-10-2026 la nota quedo con 13 avisos "a verificar",
casi todos por numeros dudosos o por lo que el profesor mostraba y el audio no
trae. El programa del ramo, que estaba en OneDrive, nombraba justo los tests
de esa clase, y las laminas de otras clases estaban en PPTs/. Ninguna etapa los
leia.

Se probo antes darle un glosario del programa a Whisper, en cada ventana de 30
segundos (16-10-2026, tres tramos de 5 minutos de esa clase). Arreglo
"heterocedasticidad", pero cambio "test" por "TED" y "p-valor" por "píbar", y
tardo un 40% mas. Ademas no hacia falta para los nombres: la skill ya los
escribia bien en las notas. El material sirve mas ahi, donde se escribe.

Se le pasa como texto en un archivo dentro de transcripciones_pendientes, no
como ruta al PDF en OneDrive: asi el gate de rutas sigue igual (proyecto y
vault) y la skill no gana permiso de escribir en la carpeta del ramo.

Lista blanca, la misma de onedrive.elegir_archivos: el programa es solo el
declarado a mano en config.json ("programas"), y las laminas salen solo de
PPTs/. La raiz del ramo no se recorre: ahi puede haber documentos personales.

La lamina de la clase casi nunca esta al procesarla: el profesor la sube
durante la semana. Lo que siempre sirve es el programa, que nombra los temas
del semestre. Una lamina anterior entra solo si la clase la continua, que es
lo que mide el parecido (la clase del 07-10-2026 de un ramo calzo con la
lamina que el profesor siguio usando la semana siguiente).

Como se elige la lamina. Por parecido de palabras con la transcripcion,
pesando mas las que aparecen en pocas laminas del ramo. Calibrado el
16-10-2026 contra las clases ya procesadas: la lamina correcta da 0,56 y 0,61
y la segunda 0,45 y 0,41. Cuando no habia lamina de la clase, la mejor quedaba
en 0,27 a 0,45 y empatada con la segunda. En la duda no va ninguna: una lamina
de otra clase confunde mas de lo que ayuda.
"""
import hashlib
import math
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

from .config import dir_pendientes
from .onedrive import MAX_CARACTERES_POR_ARCHIVO, _texto_pdf, carpeta_del_ramo

CARPETA_LAMINAS = "PPTs"
PUNTAJE_MINIMO = 0.5
VENTAJA_MINIMA = 0.1
# Tope del archivo: un programa ronda los 10.000 caracteres y una lamina entre
# 5.000 y 30.000. Pasado esto el costo de leerlo supera lo que aporta.
MAX_CARACTERES = 40_000

# Palabras de cinco letras o mas que salen en cualquier clase y no dicen el tema.
_VACIAS = set("""porque cuando donde entonces tambien despues antes ahora todos todas estos
estas esto esta este esas esos sobre entre desde hasta hacia segun sino aunque mientras
siempre nunca puede pueden tiene tienen hacer hace hacen tener cada otro otra otros otras
mismo misma bien solo pero como para cual cuales cuanto mucho muchos mucha poco pocos nada
algo alguien vamos ustedes nosotros ellos ellas usted quiero quieren digamos verdad cierto
bueno ejemplo clase clases profesor""".split())


def _palabras(texto: str) -> list[str]:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return [p for p in re.findall(r"[a-z]{5,}", t) if p not in _VACIAS]


def elegir_lamina(transcripcion: str, laminas: dict[str, str]) -> str | None:
    """Nombre de la lamina que corresponde a la clase, o None si ninguna destaca."""
    if not laminas:
        return None
    dichas = set(_palabras(transcripcion))
    cuentas = {nombre: set(_palabras(texto)) for nombre, texto in laminas.items()}
    en_cuantas = Counter(p for palabras in cuentas.values() for p in palabras)
    puntajes = []
    for nombre, palabras in cuentas.items():
        pesos = {p: math.log(1 + len(cuentas) / en_cuantas[p]) for p in palabras}
        total = sum(pesos.values()) or 1
        puntajes.append((sum(v for p, v in pesos.items() if p in dichas) / total, nombre))
    puntajes.sort(reverse=True)
    mejor, nombre = puntajes[0]
    segunda = puntajes[1][0] if len(puntajes) > 1 else 0.0
    return nombre if mejor >= PUNTAJE_MINIMO and mejor - segunda >= VENTAJA_MINIMA else None


def preparar(ramo: str, config: dict, ruta_transcripcion: str, slug: str) -> Path | None:
    """
    Escribe <slug>_material.txt con el programa y la lamina elegida. None si el
    ramo no tiene carpeta o no hay nada que darle. Nunca lanza: sin material la
    clase se procesa igual que antes.
    """
    try:
        carpeta = carpeta_del_ramo(ramo, config)
        if carpeta is None or not carpeta.is_dir():
            return None
        declarado = (config.get("programas") or {}).get(ramo)
        programa = carpeta / declarado if declarado else None
        laminas = {p.name: _texto_pdf(p) for p in sorted((carpeta / CARPETA_LAMINAS).glob("*.pdf"))}
        elegida = elegir_lamina(Path(ruta_transcripcion).read_text(encoding="utf-8"), laminas)

        partes = []
        texto_programa = _texto_pdf(programa).strip() if programa and programa.is_file() else ""
        if texto_programa:
            partes.append(f"## Programa del ramo ({programa.name})\n\n{texto_programa}")
        if elegida:
            partes.append(f"## Lámina de esta clase ({elegida})\n\n{laminas[elegida].strip()}")
        if not partes:
            return None
        ruta = dir_pendientes() / f"{slug}_material.txt"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text("\n\n".join(partes)[:MAX_CARACTERES], encoding="utf-8")
        return ruta
    except Exception:
        return None


def linea_para_prompt(ruta: str) -> str:
    return (
        f"Material del profesor (el programa del ramo y, si se encontró, la lámina de esta "
        f"clase): {ruta}\nLéelo antes de escribir. Úsalo para escribir bien los nombres, "
        "fórmulas y definiciones que el profesor dijo, y para resolver números o pasos que "
        "el audio deja dudosos. Lo que está solo en el material y no se dijo en clase no va "
        "como dicho en clase: si lo agregas, márcalo como aporte externo. Si la lámina no "
        "corresponde a lo que se vio, ignórala."
    )


# --------------------------------------------------------------------------
# Laminas y ayudantias para la hoja de una evaluacion
# --------------------------------------------------------------------------
#
# La lamina de una clase casi nunca esta al procesarla: el profesor la sube en la
# semana. Cuando se arma la hoja de una solemne o del examen, en cambio, ya estan
# todas. Por eso ahi entran las laminas y las ayudantias de las clases de esa
# evaluacion (pedido del estudiante el 16-10-2026).
#
# Lista blanca: solo estas carpetas, por su nombre exacto, sin subcarpetas. En
# otras carpetas de los ramos hay un perfil de LinkedIn de una persona y libros
# enteros (ver onedrive.elegir_archivos), y en la raiz puede haber de todo.
CARPETAS_DEL_PROFESOR = {"ppts": "lámina", "ayudantias": "ayudantía"}

# Bajo esto, un PDF no se parece a ninguna clase: no se asigna y no entra.
# Calibrado el 16-10-2026 con las notas de cuatro ramos: las asignaciones
# correctas dieron 0,24 a 0,48.
PUNTAJE_MINIMO_CLASE = 0.2
# Una fecha en el nombre cuenta si cae a menos de esto de una clase: el
# profesor a veces la fecha el dia que la sube.
DIAS_FECHA_EN_NOMBRE = 3


def _sin_tildes(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _fecha_en_nombre(nombre: str, anio: int) -> date | None:
    """2026.08.26, 26.08.26, 26-08-2026 o 27-08 (sin año, el de las clases). Un
    numero que parece fecha y no lo es se descarta despues: tiene que caer
    cerca de una clase real."""
    m = re.search(r"(20\d\d)[._-](\d{1,2})[._-](\d{1,2})", nombre)
    if m:
        a, mes, dia = (int(x) for x in m.groups())
    else:
        m = re.search(r"(?<!\d)(\d{1,2})[.-](\d{1,2})(?:[.-](\d{2,4}))?(?!\d)", nombre)
        if not m:
            return None
        dia, mes = int(m.group(1)), int(m.group(2))
        a = int(m.group(3)) if m.group(3) else anio
        a = a + 2000 if a < 100 else a
    try:
        return date(a, mes, dia)
    except ValueError:
        return None


def asignar_a_clases(textos: dict[str, str], clases: dict[str, str]) -> dict[str, str | None]:
    """
    A que clase corresponde cada PDF: {nombre: fecha de la clase, o None}.
    Primero la fecha del nombre, si cae cerca de una clase. Si no, la clase cuya
    nota mas se le parece, pesando mas las palabras que estan en pocos PDF del
    ramo. Calibrado el 16-10-2026: en cuatro ramos todas las laminas fechadas y
    todas las de un ramo sin fechas cayeron en su clase.
    """
    if not clases:
        return {n: None for n in textos}
    fechas = sorted(clases)
    anio = int(fechas[0][:4])
    palabras_clase = {f: set(_palabras(t)) for f, t in clases.items()}
    palabras_pdf = {n: set(_palabras(t)) for n, t in textos.items()}
    en_cuantos = Counter(p for ps in palabras_pdf.values() for p in ps)
    asignadas = {}
    for nombre, palabras in palabras_pdf.items():
        fecha = _fecha_en_nombre(nombre, anio)
        if fecha:
            cerca = [f for f in fechas if abs((date.fromisoformat(f) - fecha).days) <= DIAS_FECHA_EN_NOMBRE]
            if cerca:
                asignadas[nombre] = min(cerca, key=lambda f: abs((date.fromisoformat(f) - fecha).days))
                continue
        pesos = {p: math.log(1 + len(palabras_pdf) / en_cuantos[p]) for p in palabras}
        total = sum(pesos.values())
        if not total:
            asignadas[nombre] = None
            continue
        puntaje, fecha_clase = max((sum(v for p, v in pesos.items() if p in palabras_clase[f]) / total, f)
                                   for f in fechas)
        asignadas[nombre] = fecha_clase if puntaje >= PUNTAJE_MINIMO_CLASE else None
    return asignadas


def material_de_la_evaluacion(ramo: str, config: dict, clases: dict[str, str],
                              en_alcance: set[str]) -> list[dict]:
    """
    Las laminas y ayudantias de las clases que entran, con la forma de
    onedrive.leer_material. `clases` es {fecha: texto de su nota} de todas las
    clases del ramo, no solo las que entran: una lamina se asigna comparandola
    con todas, y entra si su clase esta en `en_alcance`. Nunca lanza.
    """
    try:
        carpeta = carpeta_del_ramo(ramo, config)
        if carpeta is None or not carpeta.is_dir():
            return []
        candidatos = []
        for sub in sorted(carpeta.iterdir()):
            categoria = CARPETAS_DEL_PROFESOR.get(_sin_tildes(sub.name)) if sub.is_dir() else None
            if categoria:
                candidatos += [(categoria, p) for p in sorted(sub.glob("*.pdf"))]
        textos, categorias, vistos = {}, {}, set()
        for categoria, ruta in candidatos:
            texto = _texto_pdf(ruta).strip()
            huella = hashlib.md5(texto.encode()).hexdigest()
            # Un escaneado no trae texto, y no se puede asignar a ninguna clase.
            if texto and huella not in vistos and ruta.name not in textos:
                vistos.add(huella)
                textos[ruta.name], categorias[ruta.name] = texto, categoria
        asignadas = asignar_a_clases(textos, clases)
        return [{"categoria": categorias[n], "archivo": n, "texto": textos[n][:MAX_CARACTERES_POR_ARCHIVO],
                 "sin_texto": False, "clase": f}
                for n, f in asignadas.items() if f in en_alcance]
    except Exception:
        return []
