#!/bin/bash
# Instala dos LaunchAgents de macOS:
#   - el bot de Telegram (orquestador.bot), que queda corriendo siempre y se
#     reinicia solo si se cae. Mientras espera mensajes no usa CPU: queda
#     dormido en una conexion abierta con Telegram.
#   - la secretaria (orquestador.secretaria), todos los dias a las 9:00. Si
#     no hay nada que decir, no escribe.
#
# Requisitos: el token del bot en el Llavero (servicio agente-estudio-telegram)
# y el chat emparejado (python3 -m orquestador.bot --emparejar <id>).
#
# Los .plist se arman aqui y no se versionan porque llevan rutas de este Mac.
#
# Para quitarlos:
#   launchctl bootout gui/$(id -u)/local.agente-estudio.bot
#   launchctl bootout gui/$(id -u)/local.agente-estudio.secretaria
set -e

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
AGENTES="$HOME/Library/LaunchAgents"
mkdir -p "$RAIZ/orquestador/logs" "$AGENTES"

escribir_plist() {  # etiqueta, modulo, llaves extra, registro
	cat > "$AGENTES/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$1</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PYTHON</string>
		<string>-m</string>
		<string>$2</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$RAIZ</string>
$3
	<key>StandardOutPath</key>
	<string>$RAIZ/orquestador/logs/$4</string>
	<key>StandardErrorPath</key>
	<string>$RAIZ/orquestador/logs/$4</string>
</dict>
</plist>
EOF
	plutil -lint "$AGENTES/$1.plist" >/dev/null
	launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
	launchctl bootstrap "gui/$(id -u)" "$AGENTES/$1.plist"
}

escribir_plist local.agente-estudio.bot orquestador.bot "	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>30</integer>" bot.log

escribir_plist local.agente-estudio.secretaria orquestador.secretaria "	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>9</integer>
		<key>Minute</key>
		<integer>0</integer>
	</dict>" secretaria.log

echo "Listo. El bot quedo corriendo y la secretaria escribe a las 9:00 si hay algo."
echo "Registros: $RAIZ/orquestador/logs/bot.log y secretaria.log"
