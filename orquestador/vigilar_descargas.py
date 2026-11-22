"""
Disparo sin clic: toma el audio que llega por AirDrop a Descargas, el que se deja en
Input y el que se guarda desde el telefono en una carpeta de OneDrive, y lo procesa.

Por que existe. La rutina era: AirDrop del iPhone al Mac, mover el audio a
Input y hacer clic en "Procesar Clases". Ahora basta el AirDrop.

Quien lo lanza. Un LaunchAgent (ver launchd/instalar_vigilante.sh) con dos
disparadores. WatchPaths sobre Descargas, para empezar apenas llega el audio.
Y StartInterval cada 10 minutos, porque el manual de launchd.plist advierte que
WatchPaths puede perder cambios. En espera no corre ningun proceso: launchd, que
es parte de macOS, despierta este script, que revisa y termina en menos de un
segundo si no hay nada.

Como reconoce un audio de AirDrop. macOS le pone el atributo
com.apple.quarantine con el agente "sharingd", que es el servicio de AirDrop
(verificado el 29-09-2026 en el audio de la clase del 28-09). Cualquier otra
descarga, aunque sea un .m4a, no se toca.

Un audio que todavia se esta recibiendo no se mueve: se mide su tamano dos
veces, con unos segundos de diferencia, y solo pasa si no cambio. El manual de
launchd tambien advierte que el aviso puede llegar con el archivo a medio
escribir. Despues, procesar_input hace su propia comprobacion de siempre.

Las otras dos entradas (issue #1, 14-10-2026):

- Input: un audio dejado ahi a mano se procesa sin abrir la app. Solo dispara una
  corrida si es nuevo o cambio desde la ultima (deteccion guarda los tamanos que vio):
  una clase que fallo al transcribir no se reintenta cada 10 minutos, con Whisper y un
  aviso de error cada vez. Esa se reintenta con la app, como antes.
- La carpeta de OneDrive de config.json (rutas.grabaciones_onedrive), para grabar
  lejos del Mac. La grabacion se copia, no se mueve: puede estar solo en la nube, y
  copiarla la descarga completa. La copia se escribe con un nombre que no es de audio y
  recien completa toma el suyo, asi nadie toma una a medias. El original pasa a la
  carpeta comun "archivos para borrar" (config.CARPETA_PARA_BORRAR): nunca se borra.

Las copias a medias que quedan sin original las rescata recuperar_parciales, en cada
pasada: sin eso, una copia completa a la que ya nadie puede volver a traer se queda en
Input para siempre (paso con la grabacion del 03-11-2026, cinco dias).
"""
import shutil
import subprocess
import time
from pathlib import Path

from . import deteccion, estado_vivo, lock, pregunta_ramo, procesar_input, telegram
from .config import CARPETA_PARA_BORRAR, cargar_config
from .deteccion import EXTENSIONES_AUDIO

DESCARGAS = Path.home() / "Downloads"
ESPERA_TAMANO_SEGUNDOS = 3
YA_TOMADAS = CARPETA_PARA_BORRAR / "Agente estudio - grabaciones ya tomadas de OneDrive"
PARCIALES_SIN_ORIGINAL = CARPETA_PARA_BORRAR / "Agente estudio - copias a medias de OneDrive"
# Una copia en curso lleva segundos; una abandonada, horas. Se mira el ctime y no el
# mtime: shutil.copy2 le deja al .parcial el mtime del original, que puede ser de hace
# dias, y entonces una copia recien empezada pareceria vieja.
HORAS_PARCIAL_ABANDONADA = 1


def llego_por_airdrop(ruta: Path) -> bool:
    # xattr de macOS y no os.getxattr, que en macOS no existe.
    r = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(ruta)],
                       capture_output=True, text=True)
    campos = r.stdout.strip().split(";")
    return r.returncode == 0 and len(campos) > 2 and campos[2] == "sharingd"


def audios_de_airdrop(carpeta: Path) -> list[Path]:
    return [p for p in sorted(carpeta.iterdir())
            if p.is_file() and p.suffix.lower() in EXTENSIONES_AUDIO and llego_por_airdrop(p)]


def estables(archivos: list[Path], espera: float | None = None) -> list[Path]:
    antes = {p: p.stat().st_size for p in archivos}
    if archivos:
        time.sleep(ESPERA_TAMANO_SEGUNDOS if espera is None else espera)
    return [p for p in archivos if p.exists() and p.stat().st_size == antes[p]]


def _libre(ruta: Path) -> Path:
    destino, n = ruta, 2
    while destino.exists():
        destino, n = ruta.with_name(f"{ruta.stem} ({n}){ruta.suffix}"), n + 1
    return destino


def audios_de_onedrive(config: dict) -> list[Path]:
    """Los audios de la carpeta de grabaciones de OneDrive, sin las ya tomadas."""
    carpeta = (config.get("rutas") or {}).get("grabaciones_onedrive")
    carpeta = Path(carpeta).expanduser() if carpeta else None
    if carpeta is None or not carpeta.is_dir():
        return []
    return [p for p in sorted(carpeta.iterdir())
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in EXTENSIONES_AUDIO]


def traer_de_onedrive(archivos: list[Path], input_dir: Path) -> list[Path]:
    """Copia cada grabacion a Input y aparta el original. Si una no se puede leer entera
    (sin red, OneDrive todavia bajandola), queda donde estaba para la proxima vez."""
    input_dir.mkdir(parents=True, exist_ok=True)
    traidas = []
    for p in archivos:
        destino = _libre(input_dir / p.name)
        parcial = destino.with_name(destino.name + ".parcial")  # no es audio: nadie la toma
        try:
            shutil.copy2(p, parcial)
            if parcial.stat().st_size != p.stat().st_size:
                raise OSError("la copia quedó incompleta")
        except OSError as e:
            # El motivo queda escrito: un error que puede costar una clase no se traga
            # (ver CODING_STANDARDS). El 03-11-2026 este except se comio el porque y la
            # copia quedo a medias sin que ninguna linea lo dijera. Sin aviso al
            # estudiante a proposito: mientras el original siga en OneDrive la proxima
            # pasada reintenta sola cada 10 minutos, y el vigia del bot avisa si algo
            # lleva dos horas atascado en Input. Lo que si necesita aviso es la copia que
            # se queda sin original, y eso lo hace recuperar_parciales.
            print(f"No se pudo traer «{p.name}» de OneDrive: {e}")
            continue  # la copia a medias queda como .parcial y la proxima se escribe encima
        parcial.rename(destino)
        YA_TOMADAS.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), _libre(YA_TOMADAS / p.name))
        traidas.append(destino)
    return traidas


def recuperar_parciales(input_dir: Path, config: dict) -> list[Path]:
    """Rescata las copias a medio traer de OneDrive que se quedaron sin original.

    traer_de_onedrive confiaba en que "la proxima se escribe encima", y eso solo vale
    mientras el original siga en la carpeta de la nube. La grabacion del 03-11-2026
    estuvo cinco dias sin procesarse: su .parcial estaba entero (mismo tamano y la misma
    duracion que el original, comprobado con ffprobe el 08-11-2026), pero el original ya
    no estaba en OneDrive, y un .parcial no es audio para deteccion, asi que nadie lo
    volvio a mirar. El vigia del bot lo aviso a las dos horas, que es todo lo que podia
    hacer: nada en el sistema sabia terminar el trabajo.

    Un .parcial con su original todavia en OneDrive no se toca, porque la pasada de
    traer_de_onedrive lo reescribe entero, y porque puede ser una copia corriendo en este
    momento. Uno que ffprobe no puede leer se aparta en "archivos para borrar", nunca se
    borra, y se avisa: ahi se perdio material y hay que dejar el audio de nuevo.
    """
    if not input_dir.is_dir():
        return []
    en_la_nube = {p.name for p in audios_de_onedrive(config)}
    recuperadas = []
    for parcial in sorted(input_dir.glob("*.parcial")):
        audio = parcial.with_name(parcial.name.removesuffix(".parcial"))
        if audio.name in en_la_nube:
            continue
        if time.time() - parcial.stat().st_ctime < HORAS_PARCIAL_ABANDONADA * 3600:
            continue
        if estado_vivo.duracion_audio_segundos(parcial) is None:
            PARCIALES_SIN_ORIGINAL.mkdir(parents=True, exist_ok=True)
            apartada = _libre(PARCIALES_SIN_ORIGINAL / parcial.name)
            shutil.move(str(parcial), apartada)
            print(f"Copia a medias que no se puede leer, apartada: {apartada}")
            telegram.avisar(
                f"⚠️ Una grabación quedó a medio copiar desde OneDrive y no se puede leer: "
                f"{audio.name}\n\nYa no está en OneDrive para traerla de nuevo, así que la "
                f"aparté en «archivos para borrar» (no la borré).\n"
                f"👉 Si todavía tienes el audio en el teléfono, déjalo otra vez en la carpeta "
                f"de OneDrive y lo proceso solo.")
            continue
        destino = _libre(audio)
        parcial.rename(destino)
        print(f"Copia a medias rescatada, estaba completa: ahora es {destino}")
        telegram.avisar(
            f"🎧 Rescaté una grabación que se quedó a medio traer desde OneDrive: "
            f"{destino.name}\n\nLa copia estaba completa, así que la proceso ahora y te "
            f"aviso cuando esté la hoja.")
        recuperadas.append(destino)
    return recuperadas


def nuevos_en_input(input_dir: Path) -> list[Path]:
    """Los audios de Input que la ultima corrida no vio, o vio con otro tamano, y que no
    esperan una respuesta por Telegram."""
    vistos = deteccion._cargar_json(deteccion.ESTADO_TAMANOS_PATH)
    return [a for a in deteccion.listar_audios(input_dir)
            if vistos.get(str(a)) != a.stat().st_size and not pregunta_ramo.esperando(a)]


def mover_a_input(archivos: list[Path], input_dir: Path) -> list[Path]:
    """Mueve, no copia: es lo mismo que hacia el estudiante a mano. Un nombre
    ya ocupado en Input no se pisa, queda como "nombre (2)"."""
    input_dir.mkdir(parents=True, exist_ok=True)
    movidos = []
    for p in archivos:
        destino, n = input_dir / p.name, 2
        while destino.exists():
            destino, n = input_dir / f"{p.stem} ({n}){p.suffix}", n + 1
        shutil.move(str(p), destino)
        movidos.append(destino)
    return movidos


def main() -> None:
    config = cargar_config()
    input_dir = Path(config["rutas"]["input"])
    airdrop, nube = audios_de_airdrop(DESCARGAS), audios_de_onedrive(config)
    listos = set(estables(airdrop + nube))  # una sola espera para las dos entradas
    recibidas = (mover_a_input([p for p in airdrop if p in listos], input_dir)
                 + traer_de_onedrive([p for p in nube if p in listos], input_dir))
    if recibidas:
        # Desde el iPhone no se veia cuando el Mac tomaba una grabacion: la carpeta de
        # OneDrive solo se vacia, y el primer aviso era el de lista, horas despues
        # (pedido del estudiante, 22-11-2026).
        nombres = ", ".join(f"«{p.stem}»" for p in recibidas)
        cuando = "esté lista" if len(recibidas) == 1 else "estén listas"
        telegram.avisar(f"📥 Recibí {nombres}. Te aviso cuando {cuando}.")
    recuperar_parciales(input_dir, config)
    if not nuevos_en_input(input_dir):
        return
    # Si ya hay un procesamiento corriendo, se espera a que termine: ese ya
    # listo Input antes de que llegara este audio y no lo va a ver. launchd no
    # lanza otra copia de este script mientras espera.
    lock.liberar(lock.adquirir_bloqueante())
    procesar_input.main()


if __name__ == "__main__":
    main()
