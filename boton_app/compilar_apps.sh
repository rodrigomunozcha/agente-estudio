#!/bin/bash
# Compila las dos apps de doble clic a partir de su codigo fuente (.applescript).
#
# Las apps NO vienen incluidas en el repositorio a proposito, por dos razones:
#   1. macOS pone en cuarentena cualquier app descargada de internet, asi que
#      una app bajada de GitHub daria el error "no se puede abrir porque el
#      desarrollador no puede verificarse" y no arrancaria. Compilada aqui, en
#      tu propio Mac, no pasa por esa cuarentena.
#   2. Un .app es un binario que no puedes leer. Los .applescript de al lado si
#      se leen en cualquier editor: son 30 lineas y puedes revisar exactamente
#      que hacen antes de compilarlos.
#
# Uso: bash boton_app/compilar_apps.sh   (desde la raiz del proyecto)

set -e

cd "$(dirname "$0")"

echo "Compilando las apps en: $(pwd)"

rm -rf "Procesar Clases.app" "Configurar Sistema.app"
osacompile -o "Procesar Clases.app" ProcesarClases.applescript
osacompile -o "Configurar Sistema.app" ConfigurarSistema.applescript

# Icono de la barra de menu. Es opcional: si swiftc no esta (no estan las
# herramientas de linea de comandos de Xcode), el pipeline funciona igual,
# solo que sin senal visible mientras trabaja.
if command -v swiftc >/dev/null 2>&1; then
  echo "Compilando el icono de la barra de menu..."
  swiftc -O -o ../barra_menu/BarraEstado ../barra_menu/BarraEstado.swift

  # La grabadora va dentro de un .app y no como binario suelto: macOS niega el
  # microfono a un programa sin Info.plist que explique para que lo usa, y los
  # permisos de microfono y pantalla se recuerdan por el identificador del
  # bundle. La firma es ad hoc (-s -), sin cuenta de desarrollador. Por eso,
  # al recompilar, macOS puede volver a pedir los permisos una vez.
  echo "Compilando la grabadora de reuniones..."
  APP=../grabadora/Grabadora.app
  mkdir -p "$APP/Contents/MacOS"
  swiftc -O -o "$APP/Contents/MacOS/Grabadora" ../grabadora/Grabadora.swift
  cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key>
	<string>local.agente-estudio.grabadora</string>
	<key>CFBundleName</key>
	<string>Grabadora</string>
	<key>CFBundleExecutable</key>
	<string>Grabadora</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>LSUIElement</key>
	<true/>
	<key>NSMicrophoneUsageDescription</key>
	<string>Para grabar tu voz junto con la reunión y procesarla como apuntes.</string>
	<key>NSAudioCaptureUsageDescription</key>
	<string>Para grabar lo que dicen los demás en la reunión y procesarlo como apuntes.</string>
</dict>
</plist>
EOF
  codesign --force -s - "$APP"
  ICONO="  - barra_menu/BarraEstado            (icono de progreso en la barra superior)
  - grabadora/Grabadora.app           (boton para grabar reuniones on-line)"
else
  echo "Aviso: swiftc no esta disponible, se omiten el icono de la barra de menu"
  echo "       y la grabadora de reuniones. El pipeline funciona igual, solo sin"
  echo "       senal visible mientras trabaja y sin el boton para grabar reuniones."
  echo "       swiftc se instala con: xcode-select --install"
  ICONO="  (sin icono de barra ni grabadora: falta swiftc)"
fi

echo
echo "Listo. Se crearon:"
echo "  - boton_app/Procesar Clases.app     (procesa las grabaciones de Input)"
echo "  - boton_app/Configurar Sistema.app  (asistente de configuracion)"
echo "$ICONO"
echo
echo "Puedes arrastrar las dos apps al Dock para tenerlas a mano."
