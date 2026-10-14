"""
Convenciones de nombre compartidas entre la etapa 6 (la hoja) y el archivado de
audio. El identificador permanente de una clase es su fecha real. "Clase N"
es un numero calculado.

En el flujo automatico (dia -> ramo del horario actual), N es la semana del
semestre (calculada en deteccion.py a partir de la fecha): un valor que no
depende de que otros archivos existan, asi que nunca hay que renombrar nada
ya creado (una semana salteada simplemente deja un hueco en la numeracion).

En el flujo manual (procesar_manual.py, o el dialogo de dia no reconocido en
transcripcion.py, para grabaciones fuera del horario actual, ej. ramos de
semestres anteriores), no existe un calendario de semestre al cual anclarse,
asi que N es el orden cronologico entre las clases de ese ramo ya archivadas
en Procesados/ (ver calcular_numero_clase_por_orden). A diferencia del flujo
automatico, este numero SI depende de que otros archivos existan: si llega
una clase mas antigua que las ya archivadas, las que venian despues de ella
en la fecha deben correrse un puesto. Por eso este flujo si necesita
corregir hojas ya creadas (ver renumerar_clases_ramo), algo que se confirmo
en vivo que pasaba mal (dos clases distintas etiquetadas "Clase 01"). Desde el
09-10-2026 el numero vive solo en la cabecera de la hoja: el nombre de los
archivos empieza por la fecha (ver nombre_base).
"""
import hashlib
import re
from datetime import date
from pathlib import Path

# El nombre de cada clase empieza por su fecha, "08.10.26 - Titulo - Ramo",
# igual para el audio, el .txt y la hoja (pedido del estudiante, 09-10-2026).
PATRON_FECHA_EN_NOMBRE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2}) - ")
# La cabecera de la hoja lleva el numero entre puntos medios (ver hoja_html.armar_documento).
PATRON_NUMERO_EN_CABECERA = re.compile(r"&middot; Clase (\d+) &middot;")


def slug_pendiente(clave: str) -> str:
    """Identificador para los archivos intermedios en transcripciones_pendientes/
    (etapas 3 a 6). No se puede usar solo la fecha: dos clases distintas
    pueden compartir la misma fecha corrupta (visto en vivo con dos clases de
    un ramo de intercambio el mismo dia 1970), lo que hacia que la segunda se diera por "ya
    procesada" al pisar el archivo de la primera. La clave (fecha + nombres
    de archivo) si es unica por grupo de audios, asi que se usa un hash corto
    de esa clave junto a la fecha, para que el nombre siga siendo legible."""
    fecha = clave.split("|", 1)[0]
    hash_corto = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:8]
    return f"{fecha}_{hash_corto}"


def sanitizar_nombre_archivo(texto: str) -> str:
    texto = re.sub(r'[/\\:*?"<>|]', "-", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def fecha_corta(iso: str) -> str:
    """ "2026-10-08" -> "08.10.26". Con el cero adelante, Finder ordena por fecha."""
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%y")
    except (TypeError, ValueError):
        return str(iso)


def fecha_del_nombre(nombre: str) -> str | None:
    """La fecha ISO de un archivo nombrado con nombre_base, o None."""
    m = PATRON_FECHA_EN_NOMBRE.match(nombre)
    return f"20{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def nombre_base(fecha: str, titulo: str, ramo: str) -> str:
    """El nombre compartido de una clase o reunion: el audio, su .txt y su hoja
    se llaman igual, para encontrar los tres juntos. Antes era "Clase 10 -
    2026-10-08 - Titulo": el numero se sigue viendo en la cabecera de la hoja."""
    partes = (fecha_corta(fecha), sanitizar_nombre_archivo(titulo), ramo_legible(ramo).strip())
    return sanitizar_nombre_archivo(" - ".join(p for p in partes if p))


def calcular_numero_clase_por_orden(procesados_dir: Path, ramo: str, fecha: date) -> int:
    carpeta_ramo = procesados_dir / ramo
    fechas_existentes = set()
    if carpeta_ramo.exists():
        fechas_existentes = {f for a in carpeta_ramo.iterdir() if (f := fecha_del_nombre(a.name))}
    fechas_existentes.add(fecha.isoformat())
    return sorted(fechas_existentes).index(fecha.isoformat()) + 1


def renumerar_clases_ramo(procesados_dir: Path, output_dir: Path, ramo: str) -> list[Path]:
    """
    Solo para el esquema de "orden cronologico" (ver docstring del modulo):
    si una clase mas antigua llega despues, las que venian despues se corren un
    puesto. Desde el 09-10-2026 el numero ya no va en el nombre del archivo,
    asi que no se renombra nada: solo se corrige el numero en la cabecera de
    cada hoja. Se reemplaza esa aparicion y no todas, porque el mismo texto
    puede estar dentro de la materia ("lo vimos en la Clase 03"). Nunca toca la
    nota de Obsidian, que se identifica por fecha.

    No usar esto para ramos del flujo automatico (semana de semestre): ahi un
    numero salteado es real (una semana sin clase) y no se debe compactar.
    """
    carpetas = [c for c in (procesados_dir / ramo, output_dir / ramo) if c.exists()]
    fechas = {f for c in carpetas for a in c.iterdir() if (f := fecha_del_nombre(a.name))}
    numero_por_fecha = {fecha: i + 1 for i, fecha in enumerate(sorted(fechas))}

    corregidas = []
    for hoja in (output_dir / ramo).glob("*.html") if (output_dir / ramo).exists() else []:
        correcto = numero_por_fecha.get(fecha_del_nombre(hoja.name))
        texto = hoja.read_text(encoding="utf-8")
        m = PATRON_NUMERO_EN_CABECERA.search(texto)
        if correcto is None or not m or int(m.group(1)) == correcto:
            continue
        hoja.write_text(texto[:m.start()] + f"&middot; Clase {correcto:02d} &middot;" + texto[m.end():],
                        encoding="utf-8")
        corregidas.append(hoja)
    return corregidas


def ramo_legible(ramo: str) -> str:
    """ "TALLER DE QUÍMICA III" -> "Taller de Química III".
    Una sigla corta se queda en mayusculas: "MIC", no "Mic"."""
    menores = {"y", "de", "del", "en", "la", "el", "los", "las", "of", "and"}
    palabras = ramo.split()
    return " ".join(
        p.upper() if re.fullmatch(r"[ivx]+", p.lower())
        else p.lower() if i and p.lower() in menores
        else p if p.isupper() and len(p) <= 3 and p.lower() not in menores
        else p[:1].upper() + p[1:].lower()
        for i, p in enumerate(palabras)
    )
