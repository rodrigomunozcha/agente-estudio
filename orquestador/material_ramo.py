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
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .config import dir_pendientes
from .onedrive import _texto_pdf, carpeta_del_ramo

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
