"""
Etapa 6 (parte 1): archiva el audio original de una clase ya procesada, en
Procesados/[Ramo]/, con el nombre compartido de la clase (ver nombres.py).
Si la clase tiene varias partes, cada archivo conserva su propia extension
y se numera para no pisarse entre si.

Una vez movido, se intenta comprimir a AAC en el mismo lugar (ver
comprimir_notas_voz.comprimir_en_sitio). La clase ya paso por Whisper, que
igual reduce todo a 16 kHz mono antes de transcribir (el porque, con las
funciones exactas que lo hacen, esta en el encabezado de
comprimir_notas_voz.py), asi que conservar el audio original sin comprimir en
Procesados/ solo gasta espacio sin proteger nada. Si la compresion falla o
ffmpeg no esta, el audio se archiva igual sin comprimir: no vale la pena
perder una clase por esto.

El original que se reemplaza NUNCA se borra: comprimir_en_sitio lo mueve a
"Grabaciones ya procesadas y por borrar/[Ramo]/", para que el estudiante lo
borre el mismo cuando quiera. Este pipeline no borra archivos por su cuenta.

Etapa 6 (parte 2), publicar_en_onedrive: cada clase vive en un solo lugar, la
carpeta de su ramo en OneDrive, en "Clases/", con el audio comprimido, la
transcripcion y la hoja juntos y con el mismo nombre (issue #1). El audio se
mueve, para que el archivo pesado no exista dos veces. La transcripcion y la hoja
se copian: Procesados/ y Output/ quedan como sala de trabajo del agente, porque
la numeracion de las clases y la renumeracion de las hojas las leen alla.
"""
import shutil
from pathlib import Path

from .comprimir_notas_voz import comprimir_en_sitio
from .ensayo import es_ensayo
from .nombres import nombre_base
from .onedrive import carpeta_del_ramo

CLASES = "Clases"


def archivar_audio(trabajo: dict, titulo: str, config: dict, bitacora=None) -> list[Path]:
    ramo = trabajo["ramo"]
    procesados_dir = Path(config["rutas"]["procesados"]) / ramo
    procesados_dir.mkdir(parents=True, exist_ok=True)

    base = nombre_base(trabajo["fecha"], titulo, ramo)
    archivos = [Path(a) for a in trabajo["archivos"]]
    # En un ensayo el audio "original" suele ser material real ya archivado:
    # moverlo lo sacaria de su lugar de verdad. Se copia, y se toleran los que
    # ya no esten donde decia la metadata (ver ensayo.py).
    ensayo = es_ensayo(config)

    destinos = []
    for i, origen in enumerate(archivos, start=1):
        sufijo = f" (parte {i})" if len(archivos) > 1 else ""
        destino = procesados_dir / f"{base}{sufijo}{origen.suffix}"
        if ensayo:
            if not origen.is_file():
                continue
            shutil.copy2(str(origen), str(destino))
        else:
            # Se anota ANTES de mover: si el corte llega justo despues del
            # movimiento pero antes de anotarlo, el audio quedaria fuera de
            # su sitio sin nada que lo devuelva.
            if bitacora is not None:
                bitacora.audio_movido(origen, destino)
            shutil.move(str(origen), str(destino))
            comprimir_en_sitio(destino)
        destinos.append(destino)

    # La transcripcion, junto a su audio y con su mismo nombre: la de
    # transcripciones_pendientes/ lleva un nombre interno que no dice nada
    # (pedido del estudiante, 09-10-2026). Se copia y no se mueve, porque esa
    # sigue siendo la que usan el revisor y la regeneracion de la hoja.
    texto = Path(trabajo.get("archivo_texto") or "")
    if texto.is_file():
        destino_txt = procesados_dir / f"{base}.txt"
        shutil.copy2(str(texto), str(destino_txt))
        if bitacora is not None:
            bitacora.archivo_creado(destino_txt)
        destinos.append(destino_txt)
    return destinos


def _libre(ruta: Path) -> Path:
    """Nunca pisar: mover encima de otro archivo lo reemplaza sin aviso, y eso es
    borrarlo. Si el nombre esta ocupado, "nombre (2)"."""
    destino, n = ruta, 2
    while destino.exists():
        destino, n = ruta.with_name(f"{ruta.stem} ({n}){ruta.suffix}"), n + 1
    return destino


def publicar_en_onedrive(archivados: list[Path], ruta_hoja: Path, ramo: str, config: dict,
                         bitacora=None) -> Path | None:
    """
    Deja la clase en "<carpeta del ramo>/Clases/": mueve el audio y copia la
    transcripcion y la hoja, todos con su nombre compartido. Devuelve la carpeta, o
    None si no se pudo (el ramo no declara carpeta en config.json, OneDrive no esta,
    o es un ensayo, que no puede dejar clases de mentira en la carpeta real). En ese
    caso la clase queda entera en Procesados y Output, como antes: quien llama avisa.

    Va dentro de la seccion critica de finalizar_clase, y cada cambio queda en la
    bitacora: detener la corrida devuelve el audio y saca las copias.
    """
    if es_ensayo(config):
        return None
    carpeta = carpeta_del_ramo(ramo, config)
    if carpeta is None or not carpeta.is_dir():
        return None
    return reunir_en(carpeta / CLASES, archivados, [ruta_hoja], bitacora)


def reunir_en(carpeta: Path, archivados: list[Path], copias: list[Path], bitacora=None) -> Path | None:
    """
    Junta en una carpeta lo que archivar_audio dejo en Procesados (el audio se mueve,
    el .txt se copia) y las copias que se pidan (la hoja). La usan las clases y las
    reuniones. Nunca pisa, y cada cambio queda en la bitacora. None si algo fallo:
    lo que falte sigue en la sala de trabajo.
    """
    try:
        if not carpeta.is_dir():
            carpeta.mkdir(parents=True)
            if bitacora is not None:
                bitacora.carpeta_creada(carpeta)
        for origen in archivados:
            destino = _libre(carpeta / origen.name)
            if origen.suffix.lower() == ".txt":
                shutil.copy2(origen, destino)
                if bitacora is not None:
                    bitacora.archivo_creado(destino)
            else:
                if bitacora is not None:
                    bitacora.audio_movido(origen, destino)  # antes de mover, como archivar_audio
                shutil.move(str(origen), str(destino))
        for copia in copias:
            if copia.is_file():
                destino = _libre(carpeta / copia.name)
                shutil.copy2(copia, destino)
                if bitacora is not None:
                    bitacora.archivo_creado(destino)
    except OSError:
        return None
    return carpeta
