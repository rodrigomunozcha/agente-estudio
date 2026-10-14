# -*- coding: utf-8 -*-
"""
Comprime a AAC de 48 kbps en mono las grabaciones pesadas: las que vienen sin
comprimir (ALAC/WAV/FLAC/AIFF) y, desde el 14-10-2026, tambien los AAC sobre
80 kbps, como las notas de voz de 251 kbps en estereo. Libera espacio una vez
que ya se transcribieron.

Por que existe: Whisper nunca ve el audio original. mlx_whisper y
openai-whisper bajan TODO a 16 kHz mono con ffmpeg antes de transcribir (ver
mlx_whisper/audio.py y whisper/audio.py, funcion load_audio, en el venv de
Transcriptotem). Grabar en Lossless no mejora ni una palabra de la
transcripcion, solo ocupa mas espacio. Este script es limpieza de las
grabaciones ya usadas, no toca nada del pipeline de orquestador/.

Este script nunca borra un archivo, ni a mano ni automatico desde
archivado.py: el original sin comprimir se mueve a
"Grabaciones ya procesadas y por borrar/<carpeta>/", junto a la raiz del
proyecto, en una subcarpeta con el nombre de la carpeta que lo contenia (el
ramo, si viene de Procesados/<Ramo>/). Ahi queda esperando a que el
estudiante decida borrarlo el mismo. El archivo comprimido en AAC queda en
el lugar de siempre, con el mismo nombre: nada mas que abrir cambia de
lugar.

`comprimir_en_sitio()` es la pieza que hace esto para un solo archivo y la
usa tanto `archivado.py` (automatico, apenas una clase se archiva) como
`comprimir_carpeta()` (a mano, para limpiar audio viejo).

Uso manual:
    python3 -m orquestador.comprimir_notas_voz <carpeta> [bitrate]

    bitrate es opcional, ej. 64k (default 48k, en mono).
"""
import shutil
import subprocess
import sys
from pathlib import Path

# Solo estos formatos se revisan. mp3 y ogg quedan afuera: llegan ya chicos.
FORMATOS_ENTRADA = {".wav", ".aiff", ".aif", ".flac", ".m4a", ".caf"}

# Un .m4a puede traer adentro ALAC (sin comprimir) o AAC (ya comprimido): la
# extension sola no alcanza, hay que preguntarle a ffprobe el codec real.
CODECS_SIN_COMPRIMIR = {"alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "flac"}

# 48 kbps en mono: el estudiante escucho el mismo minuto de una clase a 251, 48 y
# 32 kbps y eligio este (14-10-2026). Se entiende igual, y una clase de 2 horas
# baja de 220 MB a 43 MB. Para Whisper no cambia nada: igual baja todo a 16 kHz
# mono antes de transcribir, y esto corre despues, al archivar.
BITRATE_AAC_DEFAULT = "48k"

# Un AAC ya comprimido tambien se reconvierte si viene pesado: las notas de voz
# llegan a 251 kbps en estereo. Doble compresion con perdida, si, pero desde 251
# a 48 kbps de voz no se nota (ver arriba). Bajo este umbral no vale la pena.
UMBRAL_KBPS_PARA_RECOMPRIMIR = 80

# Adonde van los originales que se reemplazan por su version en AAC. Junto a
# Input/Output/Procesados, para que sea facil de encontrar (ver .gitignore).
CARPETA_A_BORRAR = Path(__file__).resolve().parent.parent / "Grabaciones ya procesadas y por borrar"


def _ffmpeg_disponible() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _codec_de(ruta: Path) -> str | None:
    try:
        salida = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(ruta),
            ],
            capture_output=True, text=True, check=True,
        )
        return salida.stdout.strip().lower() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _kbps_de(ruta: Path) -> float | None:
    try:
        salida = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=bit_rate", "-of", "csv=p=0", str(ruta)],
            capture_output=True, text=True, check=True,
        )
        return int(salida.stdout.strip()) / 1000
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


def necesita_comprimir(ruta: Path) -> bool:
    if _codec_de(ruta) in CODECS_SIN_COMPRIMIR:
        return True
    kbps = _kbps_de(ruta)
    return kbps is not None and kbps > UMBRAL_KBPS_PARA_RECOMPRIMIR


def _convertir(origen: Path, destino: Path, bitrate: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(origen),
            "-ac", "1", "-c:a", "aac", "-b:a", bitrate,
            "-movflags", "+faststart", str(destino),
        ],
        capture_output=True, check=True,
    )


def _destino_para_borrar(ruta: Path) -> Path:
    """
    Un nombre libre dentro de Grabaciones ya procesadas y por borrar/<carpeta
    que lo contenia>/. Si dos clases distintas comparten nombre de archivo
    (raro, pero ya paso con clases de un ramo de intercambio, ver
    nombres.py), se numera en vez de pisar el original de la otra.
    """
    carpeta = CARPETA_A_BORRAR / ruta.parent.name
    carpeta.mkdir(parents=True, exist_ok=True)
    candidato = carpeta / ruta.name
    contador = 2
    while candidato.exists():
        candidato = carpeta / f"{ruta.stem} ({contador}){ruta.suffix}"
        contador += 1
    return candidato


def comprimir_en_sitio(ruta: Path, bitrate: str = BITRATE_AAC_DEFAULT) -> tuple[int, int, Path] | None:
    """
    Convierte a AAC un audio, dejando el resultado en el mismo lugar y con
    el mismo nombre. El original nunca se borra: se mueve a
    Grabaciones ya procesadas y por borrar/<carpeta>/.

    Devuelve (peso_original, peso_nuevo, ruta_del_original_movido) si
    convirtio, o None si no habia nada que hacer (ffmpeg no esta, el audio ya
    viene comprimido, o la conversion fallo). Nunca lanza: quedarse con el
    archivo sin comprimir es un resultado aceptable, perder la clase por
    esto no lo es.

    Solo actua sobre .m4a. Es el unico contenedor que de verdad aparece en
    este pipeline (las grabaciones de Notas de Voz siempre llegan asi), y
    mantener la extension de entrada y salida identica evita que la ruta que
    quedo anotada en la bitacora (para deshacer un aborto) deje de apuntar
    al archivo real.
    """
    if ruta.suffix.lower() != ".m4a" or not ruta.is_file():
        return None
    if not _ffmpeg_disponible():
        return None
    if not necesita_comprimir(ruta):
        return None

    temporal = ruta.with_suffix(".comprimiendo.m4a")
    try:
        _convertir(ruta, temporal, bitrate)
    except subprocess.CalledProcessError:
        temporal.unlink(missing_ok=True)
        return None

    peso_original = ruta.stat().st_size
    peso_nuevo = temporal.stat().st_size
    if peso_nuevo >= peso_original:
        temporal.unlink()  # el temporal es nuestro, no del estudiante
        return None
    destino_original = _destino_para_borrar(ruta)
    shutil.move(str(ruta), str(destino_original))
    temporal.rename(ruta)
    return peso_original, peso_nuevo, destino_original


def _listar_candidatos(carpeta: Path) -> list[Path]:
    candidatos = []
    for ruta in sorted(carpeta.rglob("*")):
        if not ruta.is_file() or ruta.suffix.lower() not in FORMATOS_ENTRADA:
            continue
        if CARPETA_A_BORRAR in ruta.resolve().parents:
            continue  # no reconvertir lo que ya se aparto para borrar
        if necesita_comprimir(ruta):
            candidatos.append(ruta)
    return candidatos


def comprimir_carpeta(carpeta: str, bitrate: str = BITRATE_AAC_DEFAULT) -> None:
    raiz = Path(carpeta)

    if not _ffmpeg_disponible():
        print("Falta ffmpeg. Instalalo con 'brew install ffmpeg' y vuelve a correr esto.")
        return
    if not raiz.is_dir():
        print(f"No existe esa carpeta: {raiz}")
        return

    candidatos = _listar_candidatos(raiz)
    if not candidatos:
        print(f"No encontre audios pesados (sin comprimir o sobre {UMBRAL_KBPS_PARA_RECOMPRIMIR} kbps) en {raiz}.")
        print("Si esperabas encontrar algo, revisa que la carpeta sea la correcta.")
        return

    print(f"Encontre {len(candidatos)} audio(s) pesados. Convirtiendo a AAC {bitrate} mono...")
    print(f"Carpeta: {raiz}")
    print()

    peso_original = 0
    peso_nuevo = 0
    fallos = []
    for i, ruta in enumerate(candidatos, start=1):
        print(f"  [{i}/{len(candidatos)}] {ruta.relative_to(raiz)}")
        resultado = comprimir_en_sitio(ruta, bitrate)
        if resultado is None:
            print("    fallo, lo dejo como estaba")
            fallos.append(ruta.name)
            continue
        peso_original += resultado[0]
        peso_nuevo += resultado[1]

    print()
    print("Listo. Los archivos comprimidos quedaron donde estaban, con el mismo nombre.")
    print(f"Los originales sin comprimir se movieron a: {CARPETA_A_BORRAR}")
    if fallos:
        print(f"No se pudieron convertir {len(fallos)}: {', '.join(fallos)}")

    if peso_original:
        ahorro = peso_original - peso_nuevo
        porcentaje = (ahorro / peso_original) * 100 if peso_original else 0
        print()
        print(f"Espacio original: {peso_original / 1024 / 1024:.1f} MB")
        print(f"Espacio nuevo:    {peso_nuevo / 1024 / 1024:.1f} MB")
        print(f"Ahorro:           {ahorro / 1024 / 1024:.1f} MB ({porcentaje:.0f}%)")

    print()
    print("Revisa que suenen bien y borra cuando quieras esa carpeta. Este script")
    print("nunca borra nada, eso lo haces tu cuando estes conforme.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 -m orquestador.comprimir_notas_voz <carpeta> [bitrate]")
        print("Ejemplo: python3 -m orquestador.comprimir_notas_voz ~/NotasDeVoz 160k")
        sys.exit(1)
    bitrate_arg = sys.argv[2] if len(sys.argv) > 2 else BITRATE_AAC_DEFAULT
    comprimir_carpeta(sys.argv[1], bitrate_arg)
