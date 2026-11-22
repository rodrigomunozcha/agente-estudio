"""
Las grabaciones ya procesadas, anotadas por su huella, para no procesar dos veces la misma.

Por que. El 22-11-2026 llegaron de nuevo dos grabaciones del 11-11 que ya estaban
procesadas como partes 2 y 3 de una reunion. deteccion.apartar_copias solo compara las
grabaciones que estan juntas en Input, asi que se transcribieron y analizaron otra vez, y
quedaron dos reuniones que repetian lo de la primera. Hubo que apartarlas a mano.

Como. archivado.archivar_audio anota cada audio justo antes de moverlo, porque ahi la clase
o la reunion ya quedo escrita. Al llegar una grabacion a Input, deteccion la busca aqui. La
comparacion es por contenido, no por nombre: el 13-11-2026 llegaron dos reuniones
distintas con casi el mismo nombre.

La huella es del audio, no del archivo. Las dos del 22-11-2026 pesaban lo mismo que las
originales, pero el archivo no era igual: el iPhone reescribe al compartir las fechas de la
cabecera (6 bytes en los atomos mvhd, tkhd y mdhd). El audio, el atomo mdat, era identico.
En un formato sin atomos se usa el archivo entero.

Un archivo por grabacion, con el largo del audio en el nombre (<largo>_<huella>.json). Asi
se busca sin abrir nada, y solo se calcula la huella de lo que llega cuando su largo calza
con uno anotado, que casi nunca pasa: leer cada audio entero en cada corrida no hace falta. Y cada
archivo entra a la bitacora como creado: si el estudiante detiene la corrida, cancelacion
lo deshace junto con el resto, y la grabacion no queda marcada como procesada sin estarlo.
Con un JSON comun habria que respaldarlo y restaurarlo entero.

Lo anotado antes de este cambio se siembra una vez con
    python3 -m orquestador.huellas <carpeta> [<carpeta> ...]
sobre las carpetas donde quedaron los audios ya procesados.
"""
import hashlib
import json
import os
import struct
import sys
from datetime import date
from pathlib import Path

REGISTRO = Path(__file__).parent / "grabaciones_procesadas"
AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".caf", ".ogg", ".opus", ".flac", ".mov"}
_CON_ATOMOS = {".m4a", ".mp4", ".mov"}
# Un archivo de OneDrive que solo esta en la nube (macOS: SF_DATALESS). Leerlo lo descarga.
_SOLO_EN_LA_NUBE = 0x40000000


def _audio(archivo: Path) -> tuple[int, int]:
    """(inicio, largo) de los bytes de audio: el primer atomo mdat de un m4a, mp4 o mov,
    o el archivo entero si no tiene atomos o no se encuentra."""
    total = archivo.stat().st_size
    if archivo.suffix.lower() in _CON_ATOMOS:
        with open(archivo, "rb") as f:
            inicio = 0
            while inicio + 8 <= total:
                f.seek(inicio)
                cabecera = f.read(16)
                largo, tipo = struct.unpack(">I4s", cabecera[:8])
                salto = 8
                if largo == 1 and len(cabecera) == 16:
                    largo, salto = struct.unpack(">Q", cabecera[8:16])[0], 16
                elif largo == 0:
                    largo = total - inicio
                if largo < salto:
                    break
                if tipo == b"mdat":
                    return inicio + salto, min(largo, total - inicio) - salto
                inicio += largo
    return 0, total


def largo_audio(archivo: Path) -> int:
    return _audio(archivo)[1]


def huella(archivo: Path) -> str:
    inicio, falta = _audio(archivo)
    h = hashlib.sha256()
    with open(archivo, "rb") as f:
        f.seek(inicio)
        while falta > 0:
            bloque = f.read(min(1 << 20, falta))
            if not bloque:
                break
            h.update(bloque)
            falta -= len(bloque)
    return h.hexdigest()


def registrar(archivo: Path, como: str, bitacora=None) -> None:
    """Anota que este audio ya se proceso, con el nombre que recibio al archivarse."""
    ruta = REGISTRO / f"{largo_audio(archivo)}_{huella(archivo)}.json"
    if ruta.exists():
        return
    REGISTRO.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"nombre": archivo.name, "como": como,
                                "procesada": date.today().isoformat()}, ensure_ascii=False),
                    encoding="utf-8")
    if bitacora is not None:
        bitacora.archivo_creado(ruta)


def ya_procesada(archivo: Path) -> dict | None:
    """Lo anotado de la grabacion identica a esta, o None si es nueva."""
    candidatas = list(REGISTRO.glob(f"{largo_audio(archivo)}_*.json"))
    if not candidatas:
        return None
    propia = huella(archivo)
    for ruta in candidatas:
        if ruta.stem.split("_", 1)[1] == propia:
            return json.loads(ruta.read_text(encoding="utf-8"))
    return None


def sembrar(carpeta: Path) -> int:
    """Anota los audios de una carpeta como ya procesados. Devuelve cuantos."""
    n = 0
    for archivo in sorted(carpeta.rglob("*")):
        if (archivo.suffix.lower() in AUDIO and archivo.is_file()
                and not os.stat(archivo).st_flags & _SOLO_EN_LA_NUBE):
            registrar(archivo, archivo.stem)
            n += 1
    return n


if __name__ == "__main__":
    for c in sys.argv[1:]:
        print(f"{sembrar(Path(c).expanduser())} audios anotados de {c}")
