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
"""
import shutil
import subprocess
import time
from pathlib import Path

from . import deteccion, lock, pregunta_ramo, procesar_input
from .config import CARPETA_PARA_BORRAR, cargar_config
from .deteccion import EXTENSIONES_AUDIO

DESCARGAS = Path.home() / "Downloads"
ESPERA_TAMANO_SEGUNDOS = 3
YA_TOMADAS = CARPETA_PARA_BORRAR / "Agente estudio - grabaciones ya tomadas de OneDrive"


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
        except OSError:
            continue  # la copia a medias queda como .parcial y la proxima se escribe encima
        parcial.rename(destino)
        YA_TOMADAS.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), _libre(YA_TOMADAS / p.name))
        traidas.append(destino)
    return traidas


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
    mover_a_input([p for p in airdrop if p in listos], input_dir)
    traer_de_onedrive([p for p in nube if p in listos], input_dir)
    if not nuevos_en_input(input_dir):
        return
    # Si ya hay un procesamiento corriendo, se espera a que termine: ese ya
    # listo Input antes de que llegara este audio y no lo va a ver. launchd no
    # lanza otra copia de este script mientras espera.
    lock.liberar(lock.adquirir_bloqueante())
    procesar_input.main()


if __name__ == "__main__":
    main()
