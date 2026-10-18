"""
Todo lo que el agente hace con la carpeta de cada ramo en OneDrive.

Es la carpeta que el estudiante mas mira. Ahi guarda todo lo de la intranet de
la universidad, y la abre desde el iPhone con o sin el Mac. El agente deja en
"<ramo>/Transcripciones/" cada clase (archivado.publicar_en_onedrive) y las hojas de
evaluacion, lo que el estudiante manda por el chat en "<ramo>/Bot de IA/", y lee,
con lista blanca, el material que sirve para la hoja de cada evaluacion.

Hasta el 18-10-2026 las clases iban a "Clases/" y las hojas de evaluacion a "Creada
por Claude/". El estudiante pidio una sola carpeta por ramo con todo lo que el
agente genera: el .txt, las hojas .html y el audio comprimido.

La carpeta de cada ramo la declara config.json en "carpetas_onedrive", como
fuente unica (issue #1). Hasta el 14-10-2026 salia de FUENTES del copiador comun
de _herramientas, que copiaba las hojas de clase a "Creada por Claude": las
clases ahora viven junto a su audio y su transcripcion, y el copiador dejo de
hacer falta aqui.
"""
import hashlib
import logging
import os
import re
import shutil
import unicodedata
from pathlib import Path

TRANSCRIPCIONES = "Transcripciones"
# Dentro de Transcripciones, lo que no es una clase: las hojas de evaluacion y los
# repasos. Las clases van cada una en su carpeta con fecha (nombres.carpeta_por_fecha).
EVALUACIONES_Y_REPASOS = "Evaluaciones y repasos"

# ponytail: tope por archivo para que una pauta enorme no se coma el prompt de
# la hoja. Las pautas reales miden 5.000 a 10.000 caracteres (medido el
# 29-09-2026). Subirlo si un programa largo queda cortado.
MAX_CARACTERES_POR_ARCHIVO = 20_000


def copiar_pisando(ruta: Path, ramo: str, config: dict) -> Path | None:
    """
    Copia una hoja por evaluacion a "<ramo>/Transcripciones/Evaluaciones y repasos/", reemplazando la
    version anterior. Es la unica excepcion a la regla de no pisar, autorizada
    por el estudiante el 29-09-2026 para estas hojas: se rehacen despues de cada
    clase, y sin pisar se acumularian "(2)", "(3)" que tendria que borrar a
    mano. Quien llama solo la usa con una hoja que paso la validacion.

    Se escribe aparte y se reemplaza de una vez, para que OneDrive nunca
    sincronice una hoja a medio copiar. Nunca lanza: devuelve None si no pudo.
    """
    try:
        carpeta = carpeta_del_ramo(ramo, config)
        if carpeta is None or not carpeta.is_dir():
            return None
        destino = carpeta / TRANSCRIPCIONES / EVALUACIONES_Y_REPASOS / ruta.name
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_name(f".{destino.name}.tmp")
        shutil.copyfile(ruta, temporal)
        os.replace(temporal, destino)
        return destino
    except Exception:
        return None


def _nfc(texto: str) -> str:
    return unicodedata.normalize("NFC", texto)


def carpeta_del_ramo(ramo: str, config: dict) -> Path | None:
    """La carpeta del ramo en OneDrive, segun "carpetas_onedrive" de config.json. None
    si el ramo no la declara. Compara sin importar la forma Unicode de las tildes, que
    puede venir distinta del disco y del archivo de configuracion."""
    for nombre, ruta in (config.get("carpetas_onedrive") or {}).items():
        if _nfc(nombre) == _nfc(ramo):
            return Path(ruta).expanduser()
    return None


def _palabras(nombre: str) -> list[str]:
    """ "Pauta-SOLEMNE-N2_Química" -> ["pauta", "solemne", "n2", "quimica"]"""
    texto = unicodedata.normalize("NFD", nombre.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", texto)


def _numero_de_solemne(palabras: list[str]) -> str | None:
    # Formas reales en las carpetas: "Solemne-2", "SOLEMNE-N2", "solemne n 2",
    # "Ensayo-solemne-1", "S2". "2s2016" o "S2024" no cuentan: son años.
    m = re.search(r"\bsolemne (?:n ?)?(\d)\b|\bs(\d)\b", " ".join(palabras))
    return m and (m.group(1) or m.group(2))


def elegir_archivos(carpeta: Path, evaluacion: str, tipo: str,
                    programa: str | None = None) -> list[tuple[str, Path]]:
    """
    La lista blanca. Solo PDF dentro de la carpeta del ramo, fuera de "Creada
    por Claude", cuyo nombre diga que es:
    - el programa del ramo, solo el declarado a mano en config.json ("programas"),
      por su ruta dentro de la carpeta. Hasta el 07-10-2026 entraba cualquier PDF con
      la palabra "programa" en el nombre, y un "programa de becas - postulacion.pdf"
      tambien habria entrado: eso es un patron, no una lista blanca,
    - el enunciado, las instrucciones, la rubrica o la pauta de un trabajo
      (solo si la evaluacion es un trabajo),
    - una evaluacion pasada del mismo numero: para la S2, "Solemne 2" y sus
      variantes, y para el examen, un examen (solo si la evaluacion es prueba).

    Todo lo demas no se abre. Existe por casos reales: en esas carpetas hay un
    perfil de LinkedIn de una persona, trabajos del estudiante con su nombre y
    libros enteros, y en la raiz del semestre su contrato de matricula, que por
    eso nunca se recorre. Los controles quedan fuera: no calzan con ninguna
    solemne. Una misma pauta guardada en dos carpetas entra una vez.

    Devuelve (categoria, ruta), con categoria "programa", "enunciado" o
    "evaluacion pasada".
    """
    numero = evaluacion[1:] if re.fullmatch(r"S\d", evaluacion) else None
    declarado = carpeta / programa if programa else None
    elegidos, vistos = [], set()
    # Primero lo que esta mas arriba: de dos copias iguales queda la de la
    # carpeta del ramo, no la de una subcarpeta de respaldo.
    for ruta in sorted(carpeta.rglob("*"), key=lambda r: (len(r.parts), str(r))):
        # Los PDF del propio agente (hojas exportadas) viven en Transcripciones:
        # leerlos como material del profesor seria citarse a si mismo.
        if ruta.suffix.lower() != ".pdf" or TRANSCRIPCIONES in ruta.parts:
            continue
        palabras = _palabras(ruta.stem)
        if ruta == declarado:
            categoria = "programa"
        elif tipo == "trabajo":
            # "pauta" sola es ambigua: tambien se llaman asi las soluciones de
            # pruebas pasadas. Cuenta como pauta de trabajo solo si el nombre no
            # dice que es de una prueba.
            es_de_prueba = (_numero_de_solemne(palabras)
                            or {"control", "examen", "prueba"} & set(palabras))
            es_de_trabajo = {"enunciado", "instrucciones", "rubrica"} & set(palabras) or (
                "pauta" in palabras and not es_de_prueba)
            if not es_de_trabajo:
                continue
            categoria = "enunciado"
        elif "control" not in palabras and (
                (numero and _numero_de_solemne(palabras) == numero)
                or (evaluacion == "Examen" and "examen" in palabras)):
            categoria = "evaluacion pasada"
        else:
            continue
        huella = hashlib.md5(ruta.read_bytes()).hexdigest()
        if huella not in vistos:
            vistos.add(huella)
            elegidos.append((categoria, ruta))
    return elegidos


def _texto_pdf(ruta: Path) -> str:
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # avisos de formato, no errores
    try:
        from pypdf import PdfReader
        return "".join(pagina.extract_text() or "" for pagina in PdfReader(ruta).pages)
    except Exception:
        return ""  # un PDF danado se trata igual que uno escaneado: sin texto


def leer_material(ramo: str, evaluacion: str, tipo: str, programa: str | None, config: dict) -> list[dict]:
    """
    El material de OneDrive para la hoja de una evaluacion, ya en texto.
    Cada elemento: categoria, archivo, texto y sin_texto (un PDF escaneado no
    trae texto: se omite, y la hoja tiene que decir que no se pudo leer).
    """
    carpeta = carpeta_del_ramo(ramo, config)
    if carpeta is None or not carpeta.is_dir():
        return []
    material = []
    for categoria, ruta in elegir_archivos(carpeta, evaluacion, tipo, programa):
        texto = _texto_pdf(ruta).strip()
        material.append({"categoria": categoria, "archivo": ruta.name,
                         "texto": texto[:MAX_CARACTERES_POR_ARCHIVO], "sin_texto": not texto})
    return material
