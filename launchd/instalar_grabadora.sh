#!/bin/bash
# Instala el LaunchAgent de la grabadora de reuniones: el boton de la barra de
# menu queda abierto desde que inicias sesion, y si se cae vuelve solo.
#
# Requisito: haber compilado con bash boton_app/compilar_apps.sh.
#
# El .plist se arma aqui y no se versiona porque lleva rutas de este Mac.
#
# Para quitarlo:
#   launchctl bootout gui/$(id -u)/local.agente-estudio.grabadora
set -e

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
APP="$RAIZ/grabadora/Grabadora.app"
AGENTES="$HOME/Library/LaunchAgents"
ETIQUETA=local.agente-estudio.grabadora

if [ ! -x "$APP/Contents/MacOS/Grabadora" ]; then
	echo "Falta compilar la grabadora. Corre primero: bash boton_app/compilar_apps.sh"
	exit 1
fi
mkdir -p "$RAIZ/orquestador/logs" "$AGENTES"

cat > "$AGENTES/$ETIQUETA.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$ETIQUETA</string>
	<key>ProgramArguments</key>
	<array>
		<string>$APP/Contents/MacOS/Grabadora</string>
		<string>$RAIZ</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>30</integer>
	<key>StandardOutPath</key>
	<string>$RAIZ/orquestador/logs/grabadora.log</string>
	<key>StandardErrorPath</key>
	<string>$RAIZ/orquestador/logs/grabadora.log</string>
</dict>
</plist>
EOF
plutil -lint "$AGENTES/$ETIQUETA.plist" >/dev/null
launchctl bootout "gui/$(id -u)/$ETIQUETA" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENTES/$ETIQUETA.plist"

echo "Listo. Arriba a la derecha quedó un círculo: clic para grabar una reunión."
echo "La primera vez macOS te pide permiso de Grabación de pantalla y de Micrófono."
