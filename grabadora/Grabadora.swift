// Boton de la barra de menu para grabar una reunion on-line tomada desde el
// Mac. Un clic antes de entrar, otro al salir, y el audio sigue solo el mismo
// camino que una clase que llega por AirDrop.
//
// Por que existe. El 14-10-2026 hubo una reunion on-line y no hubo forma de
// grabarla: el iPhone, que es la grabadora de las clases, no estaba a mano, y
// los apuntes de los accionables quedaron dependiendo de la memoria.
//
// Tres fuentes, cada una por su lado:
//
//   - El audio del sistema (lo que dicen los demas) sale de un tap global de
//     Core Audio (macOS 14.4+). Toma el audio de las apps antes de que macOS lo
//     mande a una salida, asi que da igual si suena por AirPods, por el
//     parlante del Mac o por uno externo.
//   - El microfono (tu voz) va con AVAudioEngine, en tramos. Cuando macOS
//     cambia el microfono activo, por ejemplo al ponerte los AirPods a mitad
//     de la reunion, se cierra el tramo y se abre otro con el microfono nuevo.
//     Cada tramo recuerda en que segundo empezo, y ffmpeg los pone en su lugar
//     al mezclar. Lo que se pierde es lo que dure el cambio, cerca de un segundo.
//   - La pantalla, solo en ese modo, con ScreenCaptureKit, sin audio.
//
// Por que no todo con ScreenCaptureKit, que tambien graba audio y microfono.
// Se hizo asi primero y tenia dos problemas. Fija el microfono al empezar, asi
// que al cambiar a los AirPods seguia grabando el del Mac. Y desde macOS 15
// pide cada cierto tiempo volver a autorizar la grabacion de pantalla, aviso
// que el estudiante no quiere ver. El tap de Core Audio pide otro permiso,
// "Grabacion de audio del sistema", que no tiene ese recordatorio. El modo
// pantalla lo sigue teniendo, porque grabar la pantalla exige ScreenCaptureKit.
//
// Por que no BlackHole. Es un driver de audio virtual que obliga a armar un
// dispositivo agregado en Audio MIDI. Si esa configuracion se desarma, no
// graba y nadie se entera hasta despues de la reunion.
//
// Que queda:
//   - Input/Reunión Online DD.MM.YY - <tema>.m4a, con todo mezclado. El prefijo
//     hace que ramo_por_nombre lo reconozca como REUNIÓN sin preguntar, y el
//     tema llega al analisis, que lo usa para titular la nota.
//   - en modo pantalla, ademas, el video con el audio mezclado en ~/Movies/Reuniones.
//   - las pistas sueltas quedan en la carpeta temporal del sistema, que macOS
//     limpia sola. No se borran desde aqui: la regla es no borrar archivos del
//     estudiante, ni siquiera desde un programa.
//
// Los demas participantes no reciben ningun aviso: Zoom, Meet y Teams solo
// avisan cuando se usa su propio boton de grabar.
//
// Compilar con: bash boton_app/compilar_apps.sh (arma Grabadora.app)
// Dejarla abierta al iniciar sesion: bash launchd/instalar_grabadora.sh

import AVFoundation
import Cocoa
import CoreAudio
import ScreenCaptureKit

let PYTHON = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

// launchd no trae /opt/homebrew/bin en el PATH, por eso se busca a mano.
let FFMPEG = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    .first { FileManager.default.isExecutableFile(atPath: $0) }

// Cuanto espera el cuadro del tema antes de seguir sin nombre. Si el
// estudiante se fue, la reunion no puede quedar esperando para siempre.
let SEGUNDOS_PARA_EL_TEMA: TimeInterval = 120

// Al conectar unos AirPods, macOS avisa el cambio de microfono antes de que
// esten listos para grabar. Abrirlos al tiro a veces falla.
let ESPERA_CAMBIO_MICROFONO: TimeInterval = 1
// Pocos a proposito: cada intento abre el microfono de los AirPods, y eso los
// hace cambiar de modo (pausa y salto de volumen). Con 8 intentos fallidos, el
// estudiante escucho el volumen subir y bajar 5 a 7 veces (15-10-2026).
let INTENTOS_CAMBIO_MICROFONO = 3

func avisar(_ titulo: String, _ mensaje: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                   "-e", "end run", titulo, mensaje]
    try? p.run()
}

func correr(_ ejecutable: String, _ argumentos: [String]) -> Bool {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: ejecutable)
    p.arguments = argumentos
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    guard (try? p.run()) != nil else { return false }
    p.waitUntilExit()
    return p.terminationStatus == 0
}

// Sin tasa de bits fija: el codificador elige la que sirve para cada formato.
// Con 128 kbps fijos, el microfono de los AirPods (24 kHz, mono) no se podia
// abrir: AAC no admite esa tasa ahi y AVAudioFile fallaba. Prueba del
// 15-10-2026, visto en el registro del sistema (setBitRate, error 5602266).
func ajustesAAC(_ formato: AVAudioFormat) -> [String: Any] {
    [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: formato.sampleRate,
     AVNumberOfChannelsKey: formato.channelCount]
}

// A stderr y sin buffer: con el LaunchAgent termina en orquestador/logs/grabadora.log.
func registrar(_ texto: String) {
    FileHandle.standardError.write("\(Date()) Grabadora: \(texto)\n".data(using: .utf8)!)
}

struct ErrorAudio: Error { let paso: String }

func exigir(_ estado: OSStatus, _ paso: String) throws {
    if estado != noErr { throw ErrorAudio(paso: "\(paso) (\(estado))") }
}

// MARK: - Audio del sistema

final class AudioSistema {
    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var agregado = AudioObjectID(kAudioObjectUnknown)
    private var proceso: AudioDeviceIOProcID?
    private var archivo: AVAudioFile?

    func empezar(en url: URL) throws {
        let descripcion = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        descripcion.isPrivate = true
        descripcion.muteBehavior = .unmuted
        try exigir(AudioHardwareCreateProcessTap(descripcion, &tap), "crear el tap")

        // Un dispositivo agregado que solo tiene el tap, sin salida fisica: si
        // dependiera de los AirPods, se caeria al sacarlos.
        let config: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Grabadora de reuniones",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: descripcion.uuid.uuidString,
                                               kAudioSubTapDriftCompensationKey: true]],
        ]
        try exigir(AudioHardwareCreateAggregateDevice(config as CFDictionary, &agregado), "crear el dispositivo")

        var asbd = AudioStreamBasicDescription()
        var tamano = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        var direccion = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat,
                                                   mScope: kAudioObjectPropertyScopeGlobal,
                                                   mElement: kAudioObjectPropertyElementMain)
        try exigir(AudioObjectGetPropertyData(tap, &direccion, 0, nil, &tamano, &asbd), "leer el formato")
        guard let formato = AVAudioFormat(streamDescription: &asbd) else { throw ErrorAudio(paso: "formato") }

        let archivo = try AVAudioFile(forWriting: url, settings: ajustesAAC(formato),
                                      commonFormat: formato.commonFormat, interleaved: formato.isInterleaved)
        self.archivo = archivo
        try exigir(AudioDeviceCreateIOProcIDWithBlock(&proceso, agregado, nil) { _, entrada, _, _, _ in
            guard let buffer = AVAudioPCMBuffer(pcmFormat: formato, bufferListNoCopy: entrada) else { return }
            try? archivo.write(from: buffer)
        }, "conectar la lectura")
        try exigir(AudioDeviceStart(agregado, proceso), "empezar")
    }

    func detener() {
        if let proceso {
            AudioDeviceStop(agregado, proceso)
            AudioDeviceDestroyIOProcID(agregado, proceso)
        }
        if agregado != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(agregado) }
        if tap != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tap) }
        archivo?.close()
        proceso = nil
        archivo = nil
        agregado = AudioObjectID(kAudioObjectUnknown)
        tap = AudioObjectID(kAudioObjectUnknown)
    }
}

// MARK: - Microfono

final class Microfono {
    private let carpeta: URL
    private let inicio: Date
    private var motor: AVAudioEngine?
    private var archivo: AVAudioFile?
    private(set) var tramos: [(url: URL, segundo: Double)] = []

    init(carpeta: URL, inicio: Date) {
        self.carpeta = carpeta
        self.inicio = inicio
    }

    /// Cierra el tramo en curso y abre otro con el microfono activo ahora.
    /// Un AVAudioEngine nuevo toma el microfono por defecto del momento, por
    /// eso se crea uno por tramo en vez de reconfigurar el anterior.
    @discardableResult
    func nuevoTramo() -> Bool {
        detener()
        let motor = AVAudioEngine()
        let entrada = motor.inputNode
        let formato = entrada.outputFormat(forBus: 0)
        let hardware = entrada.inputFormat(forBus: 0)
        // Precaucion: si el formato del motor no calza con el del dispositivo,
        // installTap lanza una excepcion de Objective-C que Swift no puede
        // atrapar. Se sospecho de esto el 15-10-2026, pero la falla de ese dia
        // era la tasa de bits (ver ajustesAAC). Si no calzan, se reintenta.
        guard formato.channelCount > 0, hardware.channelCount > 0,
              formato.sampleRate == hardware.sampleRate,
              formato.commonFormat == .pcmFormatFloat32, !formato.isInterleaved else {
            registrar("micrófono todavía no listo: \(formato) / \(hardware)")
            return false
        }
        // Siempre se escribe en mono, con el primer canal. Con la tapa abierta y
        // Meet usando el microfono, el del Mac llego con 3 canales (el arreglo de
        // microfonos). AAC no acepta 3 canales sin un mapa de canales: el archivo
        // se creaba, cada escritura fallaba en silencio y quedaba vacio. Prueba
        // del 15-10-2026 en Meet. Para una voz basta un canal.
        let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: formato.sampleRate,
                                 channels: 1, interleaved: false)!
        let url = carpeta.appendingPathComponent("microfono-\(tramos.count + 1).caf")
        let archivo: AVAudioFile
        do {
            archivo = try AVAudioFile(forWriting: url, settings: ajustesAAC(mono),
                                      commonFormat: .pcmFormatFloat32, interleaved: false)
        } catch {
            registrar("no se pudo crear el archivo del tramo \(tramos.count + 1) con \(formato): \(error)")
            return false
        }
        oyoAlgo = false
        entrada.installTap(onBus: 0, bufferSize: 4096, format: formato) { [weak self] buffer, _ in
            guard let datos = buffer.floatChannelData?[0],
                  let unCanal = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: buffer.frameLength) else { return }
            unCanal.frameLength = buffer.frameLength
            unCanal.floatChannelData![0].update(from: datos, count: Int(buffer.frameLength))
            try? archivo.write(from: unCanal)
            if self?.oyoAlgo == false, (0..<Int(buffer.frameLength)).contains(where: { datos[$0] != 0 }) {
                self?.oyoAlgo = true
            }
        }
        do {
            try motor.start()
        } catch {
            registrar("no partió el tramo \(tramos.count + 1): \(error)")
            entrada.removeTap(onBus: 0)
            return false
        }
        self.motor = motor
        self.archivo = archivo
        inicioTramo = Date()
        tramos.append((url, Date().timeIntervalSince(inicio)))
        registrar("tramo \(tramos.count) con \(AVCaptureDevice.default(for: .audio)?.localizedName ?? "?"), \(formato)")
        return true
    }

    // Un microfono de verdad nunca entrega ceros exactos: siempre hay algo de
    // ruido. Ceros exactos son un permiso negado, un dispositivo sin microfono
    // o el del Mac con la tapa cerrada. Paso en la prueba del 14-10-2026: 53 s
    // de microfono en -91 dB sin ningun aviso, con el Mac cerrado y un parlante.
    // Se muestra en el icono y no como notificacion, a pedido del estudiante:
    // en una reunion el aviso tiene que ser discreto.
    var sinVoz: Bool {
        motor == nil || (Date().timeIntervalSince(inicioTramo) > 5 && !oyoAlgo)
    }

    // Lo escribe el hilo de audio y lo lee el principal. Una carrera aqui solo
    // puede atrasar el aviso un segundo, no vale un candado.
    private var oyoAlgo = false
    private var inicioTramo = Date()

    func detener() {
        motor?.inputNode.removeTap(onBus: 0)
        motor?.stop()
        archivo?.close()
        motor = nil
        archivo = nil
    }
}

// MARK: - Una grabacion en curso

final class Sesion {
    let carpeta: URL
    let inicio = Date()
    let conPantalla: Bool
    let sistema = AudioSistema()
    let microfono: Microfono
    var sistemaOK = false
    var segundoSistema: Double = 0
    var stream: SCStream?
    // Mientras graba, el Mac no entra en reposo por inactividad: en una reunion
    // larga uno escucha sin tocar el teclado. Zoom suele impedirlo por su
    // cuenta, Meet en el navegador no siempre. No evita el reposo de cerrar la
    // tapa sin pantalla externa ni corriente: eso lo decide macOS.
    var sinReposo: NSObjectProtocol?
    var video: URL { carpeta.appendingPathComponent("pantalla.mov") }
    var audioSistema: URL { carpeta.appendingPathComponent("sistema.caf") }

    init(conPantalla: Bool) throws {
        self.conPantalla = conPantalla
        let sello = DateFormatter()
        sello.dateFormat = "yyyy-MM-dd HH.mm.ss"
        carpeta = FileManager.default.temporaryDirectory
            .appendingPathComponent("Grabadora \(sello.string(from: inicio))")
        try FileManager.default.createDirectory(at: carpeta, withIntermediateDirectories: true)
        microfono = Microfono(carpeta: carpeta, inicio: inicio)
    }
}

// MARK: - Controlador

final class Controlador: NSObject, NSApplicationDelegate, SCStreamDelegate, SCRecordingOutputDelegate {
    private let raiz: URL
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var sesion: Sesion?
    private var reloj: Timer?
    private var avisoSinVoz = false

    init(raiz: URL) {
        self.raiz = raiz
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        escucharCambiosDeMicrofono()
        dibujar()
    }

    private func escucharCambiosDeMicrofono() {
        var direccion = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice,
                                                   mScope: kAudioObjectPropertyScopeGlobal,
                                                   mElement: kAudioObjectPropertyElementMain)
        AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &direccion, .main) { [weak self] _, _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + ESPERA_CAMBIO_MICROFONO) {
                self?.cambiarMicrofono(intento: 1)
            }
        }
    }

    // Reintenta cada segundo por si el dispositivo nuevo tarda en quedar listo.
    // Si se agotan los intentos, lo muestra el icono (ver Microfono.sinVoz).
    // Ojo: el 15-10-2026 se creyo que el tramo con AirPods fallaba por falta
    // de espera y se subieron los intentos a 8. No era eso, era la tasa de bits
    // (ver ajustesAAC), y los 8 intentos fallidos solo sumaron saltos de volumen.
    // Que AVAudioEngine siga solo al microfono nuevo se vio en el registro del
    // sistema (SelectDevice), pero igual se detiene al cambiar de formato, por
    // eso se abre un tramo nuevo.
    private func cambiarMicrofono(intento: Int) {
        guard let sesion, !sesion.microfono.nuevoTramo(), intento < INTENTOS_CAMBIO_MICROFONO else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.cambiarMicrofono(intento: intento + 1)
        }
    }

    // MARK: - Menu

    private func dibujar() {
        let menu = NSMenu()
        if let sesion {
            let s = Int(Date().timeIntervalSince(sesion.inicio))
            let sinVoz = sesion.microfono.sinVoz
            // Sin tu voz, aparece un microfono tachado junto al contador y una
            // notificacion, una sola vez por cada vez que se pierde. Al principio
            // era solo el icono, para que fuera discreto, pero en la prueba del
            // 15-10-2026 la reunion estaba en pantalla completa, la barra de menu
            // no se veia y el aviso paso de largo. La notificacion si aparece
            // sobre la pantalla completa.
            if sinVoz && !avisoSinVoz {
                avisar("Tu voz no se está grabando", "Lo de los demás sí. Abre la tapa o ponte los AirPods.")
            }
            avisoSinVoz = sinVoz
            item.button?.image = sinVoz ? NSImage(systemSymbolName: "mic.slash",
                                                  accessibilityDescription: "Sin tu voz") : nil
            item.button?.imagePosition = .imageTrailing
            item.button?.title = String(format: "● %d:%02d", s / 60, s % 60)
            item.button?.contentTintColor = .systemRed
            menu.addItem(linea(sesion.conPantalla ? "Grabando pantalla y audio" : "Grabando solo audio"))
            if sinVoz {
                menu.addItem(linea("Tu voz no se está grabando, lo de los demás sí"))
                menu.addItem(linea("Abre la tapa del Mac, ponte los AirPods o revisa"))
                menu.addItem(linea("Ajustes > Privacidad > Micrófono"))
            }
            menu.addItem(.separator())
            menu.addItem(boton("Detener y procesar", #selector(detener)))
            menu.addItem(linea("Al detener, la reunión se procesa sola"))
        } else {
            item.button?.title = ""
            item.button?.contentTintColor = nil
            item.button?.image = NSImage(systemSymbolName: "record.circle",
                                         accessibilityDescription: "Grabar reunión")
            menu.addItem(boton("Grabar solo audio", #selector(grabarAudio)))
            menu.addItem(boton("Grabar pantalla y audio", #selector(grabarPantalla)))
            menu.addItem(.separator())
            menu.addItem(linea("Graba a todos y a ti, suene donde suene"))
        }
        item.menu = menu
    }

    private func boton(_ titulo: String, _ accion: Selector) -> NSMenuItem {
        let entrada = NSMenuItem(title: titulo, action: accion, keyEquivalent: "")
        entrada.target = self
        return entrada
    }

    private func linea(_ texto: String) -> NSMenuItem {
        let entrada = NSMenuItem(title: texto, action: nil, keyEquivalent: "")
        entrada.isEnabled = false
        return entrada
    }

    @objc private func grabarAudio() { Task { await empezar(conPantalla: false) } }
    @objc private func grabarPantalla() { Task { await empezar(conPantalla: true) } }

    // MARK: - Grabar

    @MainActor
    private func empezar(conPantalla: Bool) async {
        guard sesion == nil else { return }
        // El permiso se pide antes de crear la sesion, que es la que marca el
        // segundo cero. En la prueba del 14-10-2026 se pedia despues: el
        // estudiante tardo 13 s en contestar el dialogo, el sistema conto esos
        // 13 s como parte de su pista y el microfono quedo corrido 13 s.
        // Sin permiso de microfono se graba igual a los demas: es lo que mas importa.
        let conMicrofono = await AVCaptureDevice.requestAccess(for: .audio)
        guard let nueva = try? Sesion(conPantalla: conPantalla) else { return }
        do {
            try nueva.sistema.empezar(en: nueva.audioSistema)
            nueva.sistemaOK = true
            nueva.segundoSistema = Date().timeIntervalSince(nueva.inicio)
        } catch {
            registrar("no se pudo grabar el audio del sistema: \(error)")
            nueva.sistema.detener()
        }
        let microfonoOK = conMicrofono && nueva.microfono.nuevoTramo()

        if conPantalla {
            do {
                nueva.stream = try await empezarPantalla(guardandoEn: nueva.video)
            } catch {
                avisar("No se pudo grabar la pantalla", "Sigue solo el audio. Revisa el permiso en Ajustes > Privacidad.")
            }
        }

        guard nueva.sistemaOK || microfonoOK else {
            nueva.microfono.detener()
            avisar("No se pudo empezar a grabar",
                   "Revisa en Ajustes > Privacidad que Grabadora tenga Micrófono y Grabación de audio del sistema.")
            return
        }
        if !nueva.sistemaOK { avisar("Solo se graba tu micrófono", "Falta el permiso de Grabación de audio del sistema.") }
        // Sin microfono no salta nada: lo muestra el icono (ver Microfono.sinVoz).

        nueva.sinReposo = ProcessInfo.processInfo.beginActivity(options: .idleSystemSleepDisabled,
                                                                reason: "Grabando una reunión")
        sesion = nueva
        reloj = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.dibujar() }
        dibujar()
    }

    private func empezarPantalla(guardandoEn url: URL) async throws -> SCStream {
        let contenido = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let pantalla = contenido.displays.first(where: { $0.displayID == CGMainDisplayID() })
                ?? contenido.displays.first else { throw ErrorAudio(paso: "sin pantalla") }
        let cfg = SCStreamConfiguration()
        cfg.width = pantalla.width * 2  // retina: sin esto el texto de una lamina sale borroso
        cfg.height = pantalla.height * 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 30)
        let salida = SCRecordingOutputConfiguration()
        salida.outputURL = url
        salida.outputFileType = .mov
        let stream = SCStream(filter: SCContentFilter(display: pantalla, excludingApplications: [], exceptingWindows: []),
                              configuration: cfg, delegate: self)
        try stream.addRecordingOutput(SCRecordingOutput(configuration: salida, delegate: self))
        try await stream.startCapture()
        return stream
    }

    @objc private func detener() {
        guard let sesion else { return }
        sesion.microfono.detener()
        if let sinReposo = sesion.sinReposo { ProcessInfo.processInfo.endActivity(sinReposo) }
        sesion.sistema.detener()
        reloj?.invalidate()
        self.sesion = nil
        dibujar()
        if let stream = sesion.stream {
            // El video se cierra despues, en recordingOutputDidFinishRecording.
            pendiente = sesion
            Task { try? await stream.stopCapture() }
        } else {
            terminar(sesion)
        }
    }

    // La sesion que espera que ScreenCaptureKit termine de cerrar el video.
    private var pendiente: Sesion?

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async {
            guard let sesion = self.pendiente else { return }
            self.pendiente = nil
            self.terminar(sesion)
        }
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        avisar("El video falló", "El audio se procesa igual.")
        recordingOutputDidFinishRecording(recordingOutput)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        avisar("La grabación de pantalla se cortó", "El audio se sigue grabando.")
    }

    // MARK: - Entregar al pipeline

    private func terminar(_ sesion: Sesion) {
        let tema = preguntarTema()
        DispatchQueue.global().async { self.entregar(sesion, tema: tema) }
    }

    private func preguntarTema() -> String {
        let alerta = NSAlert()
        alerta.messageText = "¿De qué fue la reunión?"
        alerta.informativeText = "Es opcional. Sirve para reconocerla después y para titular la nota. "
            + "Si no escribes nada, en 2 minutos sigue sola."
        let campo = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        campo.placeholderString = "Por ejemplo: avance del trabajo grupal"
        alerta.accessoryView = campo
        alerta.addButton(withTitle: "Procesar")
        alerta.window.initialFirstResponder = campo
        let espera = Timer(timeInterval: SEGUNDOS_PARA_EL_TEMA, repeats: false) { _ in NSApp.abortModal() }
        RunLoop.main.add(espera, forMode: .modalPanel)
        NSApp.activate(ignoringOtherApps: true)
        alerta.runModal()
        espera.invalidate()
        // Sin barras ni dos puntos, que macOS no admite en un nombre de archivo.
        let limpio = campo.stringValue.components(separatedBy: CharacterSet(charactersIn: "/:\\\n\t"))
            .joined(separator: " ").trimmingCharacters(in: .whitespaces)
        return String(limpio.prefix(60))
    }

    private func libre(_ carpeta: URL, _ base: String, _ ext: String) -> URL {
        var destino = carpeta.appendingPathComponent("\(base).\(ext)")
        var n = 2
        while FileManager.default.fileExists(atPath: destino.path) {
            // ponytail: dos reuniones del mismo dia que esperan juntas en Input se
            // agrupan como partes de una sola (deteccion agrupa por fecha). Casi no
            // pasa porque cada una se procesa al detenerla. Si molesta, que
            // deteccion separe por hora.
            destino = carpeta.appendingPathComponent("\(base) (\(n)).\(ext)")
            n += 1
        }
        return destino
    }

    private func entregar(_ sesion: Sesion, tema: String) {
        guard let ffmpeg = FFMPEG else {
            avisar("Falta ffmpeg", "Las pistas quedaron en \(sesion.carpeta.path). Instálalo con: brew install ffmpeg")
            return
        }
        // La carpeta Input sale de config.json, igual que para el pipeline: una
        // segunda copia de la ruta aqui terminaria apuntando a otro lado.
        let config = (try? Data(contentsOf: raiz.appendingPathComponent("orquestador/config.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        guard let rutaInput = (config?["rutas"] as? [String: Any])?["input"] as? String else {
            avisar("No encontré la carpeta Input", "Revisa orquestador/config.json. Las pistas quedaron en \(sesion.carpeta.path).")
            return
        }
        let input = URL(fileURLWithPath: rutaInput)
        try? FileManager.default.createDirectory(at: input, withIntermediateDirectories: true)

        let fecha = DateFormatter()
        fecha.dateFormat = "dd.MM.yy"
        // La fecha va antes del tema: deteccion toma la primera fecha que
        // encuentra en el nombre, y el tema podria traer otra.
        let base = "Reunión Online \(fecha.string(from: sesion.inicio))" + (tema.isEmpty ? "" : " - \(tema)")

        // Cada pista entra con su retraso: el sistema y
        // cada tramo del microfono, cada uno desde el segundo en que empezo.
        var pistas = sesion.microfono.tramos
        if sesion.sistemaOK { pistas.insert((sesion.audioSistema, sesion.segundoSistema), at: 0) }
        // Solo entran las pistas que ffprobe puede leer. Una sola pista danada
        // hacia fallar la mezcla entera, y la reunion completa quedaba sin
        // procesar: paso en la prueba del 15-10-2026 en Meet, con tres tramos
        // vacios entre seis. Perder un tramo es mucho mejor que perder la reunion.
        let ffprobe = (ffmpeg as NSString).deletingLastPathComponent + "/ffprobe"
        pistas = pistas.filter { pista in
            let legible = correr(ffprobe, ["-v", "error", "-i", pista.url.path])
            if !legible { registrar("pista ilegible, queda fuera de la mezcla: \(pista.url.lastPathComponent)") }
            return legible
        }
        guard !pistas.isEmpty else {
            avisar("No quedó nada grabado", "Revisa los permisos de Micrófono y Grabación de audio del sistema.")
            return
        }
        // Los tramos del microfono pasan por dynaudnorm, que sube lo bajo. El
        // primer canal del arreglo de microfonos del Mac llega muy bajo (picos de
        // -39 dB contra -24 dB de los AirPods) y en la mezcla Whisper se saltaba
        // frases enteras. Prueba del 15-10-2026 en Meet: con la nivelacion
        // aparecieron frases que antes faltaban. El audio del sistema no se
        // nivela: ya viene parejo, y nivelarlo sube el ruido de sus silencios.
        let retrasos = pistas.indices.map { i in
            let nivelar = pistas[i].url == sesion.audioSistema ? "" : "dynaudnorm,"
            return "[\(i):a]\(nivelar)adelay=delays=\(Int(pistas[i].segundo * 1000)):all=1[p\(i)]"
        }
        let mezcla = retrasos.joined(separator: ";") + ";"
            + pistas.indices.map { "[p\($0)]" }.joined()
            + "amix=inputs=\(pistas.count):duration=longest:normalize=0[a]"

        // Se escribe con extension .part y se renombra al final: deteccion solo
        // mira audios, y asi nunca ve un .m4a a medio escribir.
        let destino = libre(input, base, "m4a")
        let parcial = destino.appendingPathExtension("part")
        let entradas = pistas.flatMap { ["-i", $0.url.path] }
        guard correr(ffmpeg, ["-y"] + entradas + ["-filter_complex", mezcla, "-map", "[a]",
                                                   "-ac", "1", "-c:a", "aac", "-b:a", "96k", "-f", "ipod", parcial.path]),
              (try? FileManager.default.moveItem(at: parcial, to: destino)) != nil else {
            avisar("No se pudo mezclar el audio", "Las pistas quedaron en \(sesion.carpeta.path).")
            return
        }

        if sesion.conPantalla, FileManager.default.fileExists(atPath: sesion.video.path) {
            // ponytail: el video arranca una fraccion de segundo despues que el
            // audio, porque ScreenCaptureKit tarda en partir. Para mirar una
            // lamina no se nota. Si algun dia importa, retrasar el audio aqui.
            let peliculas = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Movies/Reuniones")
            try? FileManager.default.createDirectory(at: peliculas, withIntermediateDirectories: true)
            let video = libre(peliculas, base, "mov")
            if !correr(ffmpeg, ["-y", "-i", sesion.video.path, "-i", destino.path,
                                "-map", "0:v", "-map", "1:a", "-c", "copy", video.path]) {
                avisar("No se pudo armar el video", "El video sin audio quedó en \(sesion.video.path).")
            }
        }

        avisar("Reunión grabada", "Empieza el procesamiento. Te aviso cuando estén las notas.")
        // Lo mismo que hace vigilar_descargas al recibir un AirDrop: espera si
        // ya hay una corrida y despues procesa Input.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: PYTHON)
        p.arguments = ["-c", "from orquestador import lock, procesar_input; "
                           + "lock.liberar(lock.adquirir_bloqueante()); procesar_input.main()"]
        p.currentDirectoryURL = raiz
        try? p.run()
    }
}

// MARK: - Arranque

let argumentos = CommandLine.arguments
guard argumentos.count > 1 else {
    FileHandle.standardError.write("Uso: Grabadora <raíz del proyecto>\n".data(using: .utf8)!)
    exit(1)
}

let app = NSApplication.shared
let controlador = Controlador(raiz: URL(fileURLWithPath: argumentos[1]))
app.delegate = controlador
app.run()
