"""
La memoria del agente: guardar en la wiki de la boveda un recuerdo que el estudiante
confirmo.

Por que existe. El bot olvidaba todo a las 3 horas, y lo que el estudiante le contaba
no quedaba en ninguna parte (issue #1). La boveda ya tiene una wiki que todas las
conversaciones leen (Wiki/, con su indice y su registro), asi que la memoria del bot
es esa misma: lo que se guarda aqui, otra conversacion tambien lo encuentra.

Una sola operacion hacia afuera, guardar_recuerdo. Por dentro decide la pagina
(persona u organizacion, tema o sintesis), escribe con el formato y el frontmatter de
la wiki, agrega la fuente, actualiza el indice y el registro, y nunca borra: solo
inserta. revisar es la misma validacion, para que el bot sepa antes de ofrecer el
boton si el recuerdo se puede guardar tal cual.

Las reglas de la wiki son las del CLAUDE.md de la boveda (formato de pagina,
"Ediciones simultaneas"). Si cambian alla, cambian aqui.

Privacidad. La proteccion real es la lista blanca: solo se escriben los campos
declarados (pagina, tipo, texto, fuente), el texto es exactamente el que el estudiante
vio y confirmo con un boton, y este modulo no lee ningun documento de la persona. Los
patrones de abajo (correo, RUT, numeros largos) no tachan nada: si aparecen, el
recuerdo entero se rechaza. Son un seguro contra lo obvio, no la proteccion, porque un
nombre o una direccion no tienen forma reconocible (regla 3 del CLAUDE.md global).

Ediciones simultaneas. La ingesta automatica de la wiki (memoria-ingesta.py, en
_herramientas/) toma su propio candado durante toda una sesion, hasta 45 minutos: no se
comparte, porque el bot esperaria eso para guardar una frase. En cambio se sigue lo que
pide la boveda: el indice se relee justo antes de editarlo y se reemplaza de una sola
vez, y el registro se agrega con una sola escritura al final.
"""
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

CARPETA_WIKI = "Wiki"
# tipo de pagina -> (carpeta dentro de Wiki/, seccion del indice)
TIPOS = {
    "entidad": ("Personas y organizaciones", "## Personas y organizaciones"),
    "tema": ("Temas", "## Temas"),
    "sintesis": ("Síntesis", "## Síntesis"),
}
INDICE, REGISTRO = "Índice - Wiki.md", "Registro - Wiki.md"
# Paginas que mantiene otra pieza y que un recuerdo no puede tocar: el indice, el
# registro y el perfil que un hook carga en cada sesion (tope de 30 lineas).
RESERVADAS = {"Índice - Wiki", "Registro - Wiki"}
PREFIJO_RESERVADO = "Perfil de "
MAX_TEXTO = 600
MAX_PAGINA = 80

# Lo que Obsidian no admite en un nombre de nota, mas los enlaces y encabezados.
_PROHIBIDOS_EN_NOMBRE = re.compile(r'[?"*:<>|\\/#^\[\]]')
_SENSIBLES = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                       # correo
    re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b"),            # RUT
    re.compile(r"(?<![\d.])\+?\d(?:[ -]?\d){8,}(?![\d.])"),       # telefono, cuenta o tarjeta
)


class RecuerdoInvalido(ValueError):
    """El recuerdo no se puede guardar tal cual. El mensaje dice por que."""


def _nfc(texto: str) -> str:
    return unicodedata.normalize("NFC", texto)


def revisar(recuerdo: dict, boveda: str | Path | None = None) -> dict:
    """El recuerdo limpio, o RecuerdoInvalido. No escribe nada. Con la boveda, tambien
    rechaza un nombre que ya es de una nota fuera de la wiki: guardar_recuerdo no la
    tocaria, y el boton se ofreceria para fallar despues. Es un seguro: el perfil enlaza
    notas por su nombre, y el modelo tiende a reusar esos nombres al proponer la pagina."""
    if not isinstance(recuerdo, dict):
        raise RecuerdoInvalido("no es un recuerdo")
    tipo = recuerdo.get("tipo")
    if tipo not in TIPOS:
        raise RecuerdoInvalido(f"tipo de página desconocido: {tipo!r}")
    pagina = " ".join(_PROHIBIDOS_EN_NOMBRE.sub(" ", _nfc(str(recuerdo.get("pagina") or ""))).split())
    if not pagina or len(pagina) > MAX_PAGINA:
        raise RecuerdoInvalido("falta el nombre de la página o es demasiado largo")
    if pagina in RESERVADAS or pagina.startswith(PREFIJO_RESERVADO):
        raise RecuerdoInvalido(f"«{pagina}» la mantiene otra parte del sistema")
    texto = " ".join(_nfc(str(recuerdo.get("texto") or "")).split())
    if not texto or len(texto) > MAX_TEXTO:
        raise RecuerdoInvalido("el texto está vacío o es demasiado largo")
    if any(p.search(texto) or p.search(pagina) for p in _SENSIBLES):
        raise RecuerdoInvalido("trae un dato sensible (correo, RUT, teléfono o un número de cuenta)")
    fuente = recuerdo.get("fuente")
    fuente = " ".join(_PROHIBIDOS_EN_NOMBRE.sub(" ", _nfc(fuente)).split()) if isinstance(fuente, str) else ""
    if boveda is not None:
        existente = _buscar_nota(Path(boveda), pagina)
        if existente is not None and Path(boveda) / CARPETA_WIKI not in existente.parents:
            raise RecuerdoInvalido(f"ya hay una nota «{pagina}» fuera de la wiki, y esas no se editan")
    return {"pagina": pagina, "tipo": tipo, "texto": texto, "fuente": fuente or None}


def _escribir(ruta: Path, texto: str) -> None:
    # Aparte y reemplazar: OneDrive nunca sube una pagina a medio escribir.
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_text(texto, encoding="utf-8")
    os.replace(temporal, ruta)


def _buscar_nota(boveda: Path, pagina: str) -> Path | None:
    """La nota con ese nombre en cualquier parte de la boveda: los nombres son unicos."""
    objetivo = f"{pagina}.md".casefold()
    for ruta in boveda.rglob("*.md"):
        if _nfc(ruta.name).casefold() == objetivo and not any(p.startswith(".") for p in ruta.parts):
            return ruta
    return None


def _frontmatter(texto: str) -> tuple[list[str], str] | None:
    """(lineas del frontmatter, resto) o None si la pagina no trae."""
    if not texto.startswith("---\n"):
        return None
    fin = texto.find("\n---\n", 4)
    if fin == -1:
        return None
    return texto[4:fin].split("\n"), texto[fin + 5:]


def _actualizar_frontmatter(texto: str, hoy: str, fuente: str | None) -> str:
    partes = _frontmatter(texto)
    if partes is None:
        return texto
    lineas, resto = partes
    lineas = [x for x in lineas if not x.startswith("actualizada:")]
    i = next((n for n, x in enumerate(lineas) if x.startswith("fecha:")), -1)
    lineas.insert(i + 1, f"actualizada: {hoy}")
    enlace = f'  - "[[{fuente}]]"'
    if fuente and enlace not in lineas:
        if "fuentes:" in lineas:
            lineas.insert(lineas.index("fuentes:") + 1, enlace)
        else:
            lineas += ["fuentes:", enlace]
    return "---\n" + "\n".join(lineas) + "\n---\n" + resto


_ENCABEZADO = re.compile(r"^#{1,6} ")


def _insertar_en_seccion(texto: str, encabezado: str, linea: str) -> str:
    """Agrega una linea despues de lo que hay bajo el encabezado, antes del encabezado
    que sigue, de cualquier nivel: en el indice, "## Temas" tiene una subseccion por
    ramo, y un tema nuevo no es de ese ramo. Si la seccion no existe, la crea al final.
    Nunca saca nada."""
    lineas = texto.split("\n")
    inicio = next((i for i, x in enumerate(lineas) if x.strip() == encabezado), None)
    if inicio is None:
        return texto.rstrip("\n") + f"\n\n{encabezado}\n\n{linea}\n"
    fin = next((i for i in range(inicio + 1, len(lineas)) if _ENCABEZADO.match(lineas[i])), len(lineas))
    ultimo = fin
    while ultimo > inicio + 1 and not lineas[ultimo - 1].strip():
        ultimo -= 1
    return "\n".join(lineas[:ultimo] + [linea] + [""] + lineas[ultimo:]).rstrip("\n") + "\n"


def _pagina_nueva(r: dict, hoy: str, vineta: str) -> str:
    fuentes = ["fuentes:", f'  - "[[{r["fuente"]}]]"'] if r["fuente"] else []
    return "\n".join([
        "---", f"fecha: {hoy}", f"tipo: {r['tipo']}", "tags:", "  - wiki", *fuentes, "---", "",
        f"# {r['pagina']}", "", r["texto"], "", "## Lo que sabemos", "", vineta, "",
        "## Contradicciones y dudas", "", "Ninguna por ahora.", "", "## Relacionado", "", "[[Índice - Wiki]]", ""])


def _resumen_corto(texto: str, largo: int = 90) -> str:
    if len(texto) <= largo:
        return texto.rstrip(".")
    return texto[:largo].rsplit(" ", 1)[0].rstrip(",.;") + "..."


def guardar_recuerdo(boveda: str | Path, recuerdo: dict, hoy: date | None = None) -> Path:
    """
    Guarda un recuerdo ya confirmado y devuelve la pagina donde quedo. recuerdo trae
    pagina (el nombre), tipo (entidad, tema o sintesis), texto (lo que el estudiante
    confirmo, tal cual) y fuente (el nombre de la nota de donde sale, por ejemplo una
    nota de reunion, o None si lo conto por el chat).

    Si la pagina existe en la wiki, agrega una viñeta a "Lo que sabemos", la fuente y la
    fecha de actualizacion. Si no existe, la crea y la anota en el indice. Siempre deja
    una entrada en el registro. Una nota con ese nombre fuera de la wiki es una fuente y
    no se toca: RecuerdoInvalido.
    """
    r = revisar(recuerdo)
    boveda, hoy = Path(boveda), (hoy or date.today())
    wiki = boveda / CARPETA_WIKI
    carpeta, seccion_indice = TIPOS[r["tipo"]]
    origen = f"[[{r['fuente']}]]" if r["fuente"] else f"contado por Telegram el {hoy:%d-%m-%Y}"
    vineta = f"- {r['texto'].rstrip('.')}. ({origen})"

    existente = _buscar_nota(boveda, r["pagina"])
    if existente is not None and wiki not in existente.parents:
        raise RecuerdoInvalido(f"ya hay una nota «{r['pagina']}» fuera de la wiki, y esas no se editan")
    if existente is not None:
        texto = existente.read_text(encoding="utf-8")
        texto = _actualizar_frontmatter(texto, hoy.isoformat(), r["fuente"])
        _escribir(existente, _insertar_en_seccion(texto, "## Lo que sabemos", vineta))
        pagina, accion = existente, "actualizó"
    else:
        pagina = wiki / carpeta / f"{r['pagina']}.md"
        pagina.parent.mkdir(parents=True, exist_ok=True)
        _escribir(pagina, _pagina_nueva(r, hoy.isoformat(), vineta))
        accion = "creó"
        indice = wiki / INDICE
        if indice.exists():
            actual = indice.read_text(encoding="utf-8")  # releido justo antes de editar
            if f"[[{r['pagina']}]]" not in actual:
                _escribir(indice, _insertar_en_seccion(
                    actual, seccion_indice, f"- [[{r['pagina']}]] - {_resumen_corto(r['texto'])}"))

    entrada = (f"\n## [{hoy.isoformat()}] ingesta | Memoria confirmada por Telegram: {r['pagina']}\n\n"
               f"Se {accion} [[{pagina.stem}]] con un recuerdo que el estudiante confirmó"
               + (f", sacado de [[{r['fuente']}]]" if r["fuente"] else "") + ".\n")
    with open(wiki / REGISTRO, "a", encoding="utf-8") as f:  # una sola escritura al final
        f.write(entrada)
    return pagina
