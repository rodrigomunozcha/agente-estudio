#!/bin/bash
# Instala el vigilante de AirDrop: un LaunchAgent de macOS que corre
# orquestador.vigilar_descargas cada vez que cambia Descargas, y ademas cada
# 10 minutos por si launchd se pierde un aviso (su manual lo advierte).
#
# En espera no queda nada corriendo: launchd es parte de macOS y ya esta siempre
# activo. El script solo despierta, revisa y termina.
#
# El .plist se arma aqui y no se versiona porque lleva rutas propias de este Mac.
#
# La primera vez que llegue un audio, macOS puede preguntar si python3 puede
# acceder a la carpeta Descargas. Hay que responder que si. Si se rechazo por
# error: Configuracion del Sistema > Privacidad y seguridad > Archivos y carpetas.
#
# Para quitarlo:
#   launchctl bootout gui/$(id -u)/local.agente-estudio.airdrop
set -e

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
ETIQUETA=local.agente-estudio.airdrop
PLIST="$HOME/Library/LaunchAgents/$ETIQUETA.plist"

mkdir -p "$RAIZ/orquestador/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$ETIQUETA</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PYTHON</string>
		<string>-m</string>
		<string>orquestador.vigilar_descargas</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$RAIZ</string>
	<key>WatchPaths</key>
	<array>
		<string>$HOME/Downloads</string>
	</array>
	<key>StartInterval</key>
	<integer>600</integer>
	<key>StandardOutPath</key>
	<string>$RAIZ/orquestador/logs/vigilante.log</string>
	<key>StandardErrorPath</key>
	<string>$RAIZ/orquestador/logs/vigilante.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$ETIQUETA" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Listo. El vigilante de AirDrop quedo activo ($ETIQUETA)."
echo "Registro: $RAIZ/orquestador/logs/vigilante.log"
