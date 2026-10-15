"""
Permitir que los procesos lanzados por launchd descarguen archivos de OneDrive.

OneDrive deja archivos "solo en la nube" (macOS los marca dataless) cuando no se
usan hace tiempo. Desde la terminal, leer uno lo descarga solo. Desde launchd no:
macOS arranca esos procesos con la descarga apagada y la lectura falla con
"OSError: [Errno 11] Resource deadlock avoided". Paso el 30-09-2026: OneDrive
saco del disco el codigo de Transcriptotem y una clase real
fallo al transcribir. El bot fallo igual leyendo las pautas del semestre.

Medido en vivo con getiopolicy_np: la terminal da 2 (descarga encendida), un
LaunchAgent da 1 (apagada). Se enciende aca porque el vigilante, el bot y la
secretaria importan este paquete, y los procesos hijos (el CLI de Claude) heredan
la politica. Si falla, no se hace nada: el error de lectura sigue diciendo que pasa.
"""
import ctypes
import os
import sys

# Homebrew (ffmpeg y ffprobe, que mlx-whisper y la fecha de las grabaciones necesitan) no
# esta en el PATH minimo con el que macOS lanza apps y LaunchAgents. Sin esto, la
# transcripcion de audios reales falla en silencio (visto en vivo con el agente de
# launchd). Vivia en procesar_input.py y se movio aqui el 15-10-2026: verificar, corrido
# como lo corre launchd, decia que ffmpeg y node no estaban instalados, y el bot y la
# secretaria tampoco lo tenian.
if "/opt/homebrew/bin" not in os.environ.get("PATH", "").split(":"):
    os.environ["PATH"] = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:" + os.environ.get(
        "PATH", "/usr/bin:/bin:/usr/sbin:/sbin")

if sys.platform == "darwin":
    try:
        # IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES=3, IOPOL_SCOPE_PROCESS=0, ..._ON=2
        ctypes.CDLL(None).setiopolicy_np(3, 0, 2)
    except Exception:
        pass
