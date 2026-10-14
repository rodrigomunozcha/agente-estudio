"""
Disparo sin clic: toma el audio que llega por AirDrop a Descargas y lo procesa.

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
"""
import shutil
import subprocess
import time
from pathlib import Path

from . import lock, procesar_input
from .config import cargar_config
from .deteccion import EXTENSIONES_AUDIO

DESCARGAS = Path.home() / "Downloads"
ESPERA_TAMANO_SEGUNDOS = 3


def llego_por_airdrop(ruta: Path) -> bool:
    # xattr de macOS y no os.getxattr, que en macOS no existe.
    r = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(ruta)],
                       capture_output=True, text=True)
    campos = r.stdout.strip().split(";")
    return r.returncode == 0 and len(campos) > 2 and campos[2] == "sharingd"


def audios_de_airdrop(carpeta: Path) -> list[Path]:
    return [p for p in sorted(carpeta.iterdir())
            if p.is_file() and p.suffix.lower() in EXTENSIONES_AUDIO and llego_por_airdrop(p)]


def estables(archivos: list[Path], espera: float = ESPERA_TAMANO_SEGUNDOS) -> list[Path]:
    antes = {p: p.stat().st_size for p in archivos}
    if archivos:
        time.sleep(espera)
    return [p for p in archivos if p.exists() and p.stat().st_size == antes[p]]


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
    nuevos = estables(audios_de_airdrop(DESCARGAS))
    if not nuevos:
        return
    mover_a_input(nuevos, Path(config["rutas"]["input"]))
    # Si ya hay un procesamiento corriendo, se espera a que termine: ese ya
    # listo Input antes de que llegara este audio y no lo va a ver. launchd no
    # lanza otra copia de este script mientras espera.
    lock.liberar(lock.adquirir_bloqueante())
    procesar_input.main()


if __name__ == "__main__":
    main()
