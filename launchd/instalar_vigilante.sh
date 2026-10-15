#!/bin/bash
# Instala el vigilante de grabaciones: un LaunchAgent de macOS que corre
# orquestador.vigilar_descargas cada vez que cambia Descargas (AirDrop), Input o la
# carpeta de grabaciones de OneDrive (rutas.grabaciones_onedrive de config.json, si
# esta), y ademas cada 10 minutos por si launchd se pierde un aviso (su manual lo
# advierte).
#
# Si cambias esas rutas en config.json, vuelve a correr este instalador: las carpetas
# que vigila quedan escritas en el .plist.
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

# Las rutas salen de config.json, que es de este Mac y no se versiona.
ruta() {
    "$PYTHON" -c 'import json, os, sys
r = json.load(open(sys.argv[1]))["rutas"].get(sys.argv[2])
print(os.path.expanduser(r) if r else "")' "$RAIZ/orquestador/config.json" "$1"
}
VIGILADAS=("$HOME/Downloads" "$(ruta input)")
NUBE="$(ruta grabaciones_onedrive)"
if [ -n "$NUBE" ]; then
    mkdir -p "$NUBE"
    VIGILADAS+=("$NUBE")
fi
RUTAS_XML=""
for r in "${VIGILADAS[@]}"; do
    RUTAS_XML+="		<string>$r</string>
"
done

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
$RUTAS_XML	</array>
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
echo "Listo. El vigilante de grabaciones quedo activo ($ETIQUETA). Vigila:"
printf '  %s\n' "${VIGILADAS[@]}"
echo "Registro: $RAIZ/orquestador/logs/vigilante.log"
