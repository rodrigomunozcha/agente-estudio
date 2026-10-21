#!/bin/bash
# Instala el LaunchAgent del control remoto: un servidor de Claude Code en la carpeta del
# agente, siempre encendido, para arreglar una falla desde la app de Claude en el iPhone
# (Code > Agente estudio) sin ir al Mac. Cada permiso se aprueba en el telefono.
#
# Por que. El estudiante procesa las clases lejos del Mac y cerca de Telegram. Toda
# falla le llega por Telegram, pero arreglarla lo obligaba a volver al Mac (inventario
# del 21-10-2026). Si internet se corta mas de unos 10 minutos el servidor se cierra:
# KeepAlive lo vuelve a levantar.
#
# Requisitos, una vez y por el estudiante: confiar en la carpeta (correr claude en ella)
# y responder "y" a "Enable Remote Control?". Ademas, la sesion normal de claude.ai: con
# el token de un año (setup-token) el control remoto no funciona.
#
# Para quitarlo:
#   launchctl bootout gui/$(id -u)/local.agente-estudio.remoto
set -e
source "$(dirname "$0")/comun.sh"

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
AGENTES="$HOME/Library/LaunchAgents"
ETIQUETA=local.agente-estudio.remoto
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
		<string>$RAIZ/node_modules/.bin/claude</string>
		<string>remote-control</string>
		<string>--name</string>
		<string>Agente estudio</string>
		<string>--spawn</string>
		<string>same-dir</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$RAIZ</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
	<key>StandardInPath</key>
	<string>/dev/null</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>30</integer>
	<key>StandardOutPath</key>
	<string>$RAIZ/orquestador/logs/remoto.log</string>
	<key>StandardErrorPath</key>
	<string>$RAIZ/orquestador/logs/remoto.log</string>
</dict>
</plist>
EOF
plutil -lint "$AGENTES/$ETIQUETA.plist" >/dev/null
reinstalar "$ETIQUETA" "$AGENTES/$ETIQUETA.plist"
echo "Listo. El control remoto queda encendido: app de Claude > Code > Agente estudio."
echo "Registro: $RAIZ/orquestador/logs/remoto.log"
