#!/bin/bash
# Corre un script de Python como lo corren los LaunchAgents del agente, y muestra
# lo que imprimio.
#
# Existe porque hay fallos que solo pasan dentro de launchd y desde la terminal no
# se ven. Dos casos reales: launchd arranca los procesos sin permiso para
# descargar archivos que OneDrive dejo solo en la nube (Errno 11, 30-09-2026), y
# la prueba del token de Claude del 02-10-2026. Las dos veces hubo que armar a
# mano un LaunchAgent temporal, y se armo cuatro veces en una sola sesion.
#
# Uso:
#   bash launchd/probar_en_launchd.sh <script.py> [segundos maximos, 120 por defecto]
#
# El script corre con el directorio de trabajo en la raiz del repositorio. Para
# importar el paquete, que empiece con:
#   import sys; sys.path.insert(0, ".")
# El LaunchAgent se retira al terminar. Sus archivos quedan en una carpeta
# temporal que macOS limpia solo.
set -euo pipefail

if [ $# -lt 1 ] || [ ! -f "$1" ]; then
    echo "Uso: bash launchd/probar_en_launchd.sh <script.py> [segundos maximos]"
    exit 2
fi

SCRIPT="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
LIMITE="${2:-120}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
ETIQUETA=local.agente-estudio.prueba
DOMINIO="gui/$(id -u)"
TMP="$(mktemp -d)"

# launchd lanza Python directo, como a los agentes (con /bin/sh de por medio
# aparecian avisos de permisos que los agentes reales no tienen). Un envoltorio
# corre el script y deja su codigo de salida en un archivo: es la forma
# confiable de saber que termino, porque launchd no avisa.
cat > "$TMP/correr.py" <<'FIN'
import runpy, sys, traceback
codigo = 0
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit as e:
    codigo = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
except BaseException:
    traceback.print_exc()
    codigo = 1
sys.stdout.flush()
open(sys.argv[2], "w").write(str(codigo))
FIN

cat > "$TMP/$ETIQUETA.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$ETIQUETA</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PYTHON</string>
		<string>$TMP/correr.py</string>
		<string>$SCRIPT</string>
		<string>$TMP/fin</string>
	</array>
	<key>WorkingDirectory</key><string>$RAIZ</string>
	<key>RunAtLoad</key><true/>
	<key>StandardOutPath</key><string>$TMP/salida</string>
	<key>StandardErrorPath</key><string>$TMP/salida</string>
</dict>
</plist>
EOF

launchctl bootout "$DOMINIO/$ETIQUETA" 2>/dev/null || true
launchctl bootstrap "$DOMINIO" "$TMP/$ETIQUETA.plist"

for _ in $(seq "$LIMITE"); do
    [ -f "$TMP/fin" ] && break
    sleep 1
done
launchctl bootout "$DOMINIO/$ETIQUETA" 2>/dev/null || true

cat "$TMP/salida" 2>/dev/null || true
if [ ! -f "$TMP/fin" ]; then
    echo "(no termino en $LIMITE segundos)"
    exit 124
fi
exit "$(cat "$TMP/fin")"
