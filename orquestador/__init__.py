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
import sys

if sys.platform == "darwin":
    try:
        # IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES=3, IOPOL_SCOPE_PROCESS=0, ..._ON=2
        ctypes.CDLL(None).setiopolicy_np(3, 0, 2)
    except Exception:
        pass
