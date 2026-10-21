# Lo comparten los instaladores de launchd. Se carga con: source "$(dirname "$0")/comun.sh"

# Saca un LaunchAgent y lo vuelve a cargar. Entre medio espera a que launchd lo suelte del
# todo: el 21-10-2026, cargarlo apenas sacado fallo con "Bootstrap failed: 5: Input/output
# error", y como el script corta en el primer error, el bot quedo detenido sin avisar.
reinstalar() {  # etiqueta, ruta del plist
	launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
	for _ in $(seq 20); do
		launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1 || break
		sleep 0.5
	done
	launchctl bootstrap "gui/$(id -u)" "$2"
}
