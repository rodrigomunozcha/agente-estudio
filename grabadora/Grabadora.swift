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
//     tema llega al analisis, que lo usa para titular la nota. Si el tema es un
//     ramo ("Termodinámica online"), entra como clase de ese ramo.
//   - en modo pantalla, ademas, el video con el audio mezclado en ~/Movies/Reuniones.
//   - las pistas sueltas, en la carpeta comun "archivos para borrar", donde el
//     estudiante las borra a mano (regla 2 del CLAUDE.md global). Al principio
//     iban a la carpeta temporal del sistema, pero la revision de codigo del
//     15-10-2026 noto que, si la mezcla fallaba, esa era la unica copia de la
//     reunion, y macOS la vacia solo.
//
// El pipeline lo lanza el vigilante (vigilar_descargas), que mira Input. La
// grabadora solo deja el archivo ahi. Hasta el 15-10-2026 lanzaba Python por su
// cuenta, porque el vigilante solo miraba Descargas.
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

// Mismo lugar que config.CARPETA_PARA_BORRAR, con la subcarpeta que dice de
// donde viene, como las demas del agente. Swift no puede importar el modulo.
let CARPETA_PISTAS = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Documents/Claude Code/archivos para borrar/Agente estudio - pistas de la grabadora")

// Si ScreenCaptureKit no avisa que cerro el video, la reunion sigue igual
// despues de este plazo. Ver detener().
let SEGUNDOS_PARA_CERRAR_VIDEO: TimeInterval = 10

// launchd no trae /opt/homebrew/bin en el PATH, por eso se busca a mano.
let FFMPEG = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    .first { FileManager.default.isExecutableFile(atPath: $0) }

// Cuanto espera el cuadro del tema antes de seguir sin nombre. Si el
// estudiante se fue, la reunion no puede quedar esperando para siempre.
let SEGUNDOS_PARA_EL_TEMA: TimeInterval = 120

// Al conectar unos AirPods, macOS avisa el cambio de microfono antes de que
// esten listos para grabar. Abrirlos de inmediato a veces falla.
let ESPERA_CAMBIO_MICROFONO: TimeInterval = 1
// Pocos a proposito: cada intento abre el microfono de los AirPods, y eso los
// hace cambiar de modo (pausa y salto de volumen). Con 8 intentos fallidos, el
// estudiante escucho el volumen subir y bajar 5 a 7 veces (15-10-2026).
let INTENTOS_CAMBIO_MICROFONO = 3

// Cada cuanto recuerda que la grabacion sigue en pausa. Lo mas probable es
// pausar en el recreo y olvidar reanudar: la clase siguiente se pierde entera.
let SEGUNDOS_PARA_RECORDAR_PAUSA: TimeInterval = 15 * 60

let PYTHON = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

// Cada cuanto se cierra el archivo de una pista y se abre el siguiente. AAC en CAF
// escribe su indice de paquetes recien al cerrar: un archivo abierto cuando la app se
// cae queda ilegible. El 20-10-2026 se perdieron asi 75 minutos de la llamada.
// En trozos, una caida pierde como mucho el trozo en curso.
let SEGUNDOS_POR_TROZO: TimeInterval = 5 * 60

// Los motores de audio que se dejan de usar no se destruyen. AVFAudio deja avisos de
// cambios del dispositivo pendientes para cada motor, y si llegan despues de
// destruirlo, la app se cae: paso el 20-10-2026, 4 minutos despues de cambiar a los
// AirPods y de vuelta (EXC_BAD_ACCESS en AVAudioIOUnit::IOUnitPropertyListener). Se
// cayo a mitad de una clase, launchd la reabrio vacia y nadie se entero.
// shortcut: crece con cada cambio de microfono mientras la app viva (pocos por
// reunion), si algun dia pesa, liberarlos al cerrar la app.
var motoresRetirados: [AVAudioEngine] = []

// Duracion de un audio segun ffprobe, o nil si no se puede leer.
func duracionAudio(_ url: URL) -> Double? {
    guard let ffmpeg = FFMPEG else { return nil }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: (ffmpeg as NSString).deletingLastPathComponent + "/ffprobe")
    p.arguments = ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", url.path]
    let tubo = Pipe()
    p.standardOutput = tubo
    p.standardError = FileHandle.nullDevice
    guard (try? p.run()) != nil else { return nil }
    let datos = tubo.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return Double(String(decoding: datos, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
}

func avisar(_ titulo: String, _ mensaje: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                   "-e", "end run", titulo, mensaje]
    try? p.run()
}

// Copia al iPhone por Telegram, con el mismo modulo del pipeline, que sabe el
// token (Llavero) y el chat. En un recreo uno no esta mirando el Mac. Sin bot
// o sin red, telegram.avisar no hace nada y queda el aviso de macOS.
func avisarAlTelefono(_ texto: String, raiz: URL) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: PYTHON)
    p.arguments = ["-c", "import sys; from orquestador.telegram import avisar; avisar(sys.argv[1])", texto]
    p.currentDirectoryURL = raiz
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
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

// MARK: - Cronometro

/// Segundos grabados desde el inicio, sin contar las pausas. Al pausar no se
/// cierra nada: cada pista deja de escribir y retoma en el mismo archivo, asi
/// que la pausa no existe dentro de los archivos. Por eso los tramos se ubican
/// en la mezcla con este cronometro y no con la hora, o quedarian corridos lo
/// que duro la pausa. Se eligio cortar la pausa entera en vez de marcarla: el
/// ruido del recreo solo le da a Whisper material para inventar frases.
final class Cronometro {
    let inicio: Date
    private(set) var pausadoDesde: Date?
    private var enPausas: TimeInterval = 0
    // Es lo unico que leen los hilos de audio, para saltarse la escritura. Una
    // carrera aqui solo mueve el corte un buffer (85 ms en el microfono), no
    // vale un candado. pausadoDesde y enPausas los usa solo el hilo principal.
    private(set) var pausado = false

    init(inicio: Date = Date()) { self.inicio = inicio }

    func pausar(_ ahora: Date = Date()) {
        guard pausadoDesde == nil else { return }
        pausadoDesde = ahora
        pausado = true
    }

    func reanudar(_ ahora: Date = Date()) {
        guard let desde = pausadoDesde else { return }
        enPausas += ahora.timeIntervalSince(desde)
        pausadoDesde = nil
        pausado = false
    }

    func segundo(_ ahora: Date = Date()) -> Double {
        ahora.timeIntervalSince(inicio) - enPausas - enEstaPausa(ahora)
    }

    func enEstaPausa(_ ahora: Date = Date()) -> Double {
        pausadoDesde.map { ahora.timeIntervalSince($0) } ?? 0
    }
}

// Se corre con `Grabadora --probar`, y compilar_apps.sh lo corre al compilar.
// precondition y no assert: con -O, assert desaparece y la prueba pasaria siempre.
func probarCronometro() {
    let t0 = Date(timeIntervalSince1970: 0)
    let c = Cronometro(inicio: t0)
    precondition(c.segundo(t0 + 60) == 60)
    c.pausar(t0 + 60)
    precondition(c.segundo(t0 + 600) == 60, "en pausa el cronometro no avanza")
    precondition(c.enEstaPausa(t0 + 600) == 540)
    c.pausar(t0 + 700)  // pausar dos veces no reinicia la pausa
    c.reanudar(t0 + 960)
    precondition(c.segundo(t0 + 1000) == 100, "descuenta los 900 s de pausa")
    c.reanudar(t0 + 1100)  // reanudar sin pausa no hace nada
    c.pausar(t0 + 1100)
    c.reanudar(t0 + 1200)
    precondition(c.segundo(t0 + 1300) == 300, "dos pausas se suman")
    print("Cronometro: OK")
}

// Dos trozos AAC con cambio de archivo en medio: los dos se leen enteros, y el
// registro guardado en disco vuelve igual. Es lo que permite rearmar una grabacion.
func probarTrozos() {
    let carpeta = FileManager.default.temporaryDirectory.appendingPathComponent("grabadora-prueba-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: carpeta, withIntermediateDirectories: true)
    let registro = RegistroPistas(carpeta: carpeta)
    let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false)!
    let segundo = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: 48000)!
    segundo.frameLength = 48000
    let escritor = Escritor()
    for (i, inicio) in [0.0, 1.0].enumerated() {
        let url = carpeta.appendingPathComponent("microfono-\(i + 1).caf")
        escritor.cambiar(a: try! AVAudioFile(forWriting: url, settings: ajustesAAC(mono),
                                             commonFormat: .pcmFormatFloat32, interleaved: false))
        registro.anotar(Pista(url: url, segundo: inicio, sistema: false))
        escritor.escribir(segundo)
    }
    escritor.cambiar(a: nil)
    for pista in registro.pistas {
        let leido = try! AVAudioFile(forReading: pista.url)
        precondition(leido.length > 40000, "el trozo \(pista.url.lastPathComponent) se lee entero")
    }
    let releidas = RegistroPistas.leer(carpeta)!
    precondition(releidas.map(\.segundo) == [0, 1] && releidas.allSatisfy { !$0.sistema },
                 "pistas.txt devuelve lo mismo que se anoto")
    precondition(RegistroPistas.leer(carpeta.appendingPathComponent("no-existe")) == nil)
    print("Trozos: OK")
}

// MARK: - Pistas en trozos

/// El archivo donde escribe una pista, cambiable mientras graba. Lo escribe el hilo
/// de audio y lo cambia el principal, por eso va con candado: el cambio dura lo que
/// asignar una variable, y el hilo de audio ya hace escritura de archivo igual.
final class Escritor {
    private let candado = NSLock()
    private var archivo: AVAudioFile?

    func escribir(_ buffer: AVAudioPCMBuffer) {
        candado.lock()
        defer { candado.unlock() }
        try? archivo?.write(from: buffer)
    }

    /// Sigue escribiendo en `nuevo` y cierra el anterior, que queda completo.
    func cambiar(a nuevo: AVAudioFile?) {
        candado.lock()
        let viejo = archivo
        archivo = nuevo
        candado.unlock()
        viejo?.close()
    }
}

struct Pista {
    let url: URL
    let segundo: Double
    let sistema: Bool
}

/// Cada archivo de la grabacion con el segundo en que empieza. Se guarda tambien en
/// pistas.txt, que es lo que permite rearmar una grabacion si la app se cae.
final class RegistroPistas {
    let carpeta: URL
    private(set) var pistas: [Pista] = []
    var archivo: URL { carpeta.appendingPathComponent("pistas.txt") }

    init(carpeta: URL) { self.carpeta = carpeta }

    func anotarSoloEnMemoria(_ pista: Pista) { pistas.append(pista) }

    func anotar(_ pista: Pista) {
        pistas.append(pista)
        let linea = "\(pista.sistema ? "sistema" : "microfono")\t\(pista.segundo)\t\(pista.url.lastPathComponent)\n"
        if let manija = try? FileHandle(forWritingTo: archivo) {
            manija.seekToEndOfFile()
            manija.write(linea.data(using: .utf8)!)
            try? manija.close()
        } else {
            try? linea.write(to: archivo, atomically: true, encoding: .utf8)
        }
    }

    /// Las pistas guardadas en una carpeta, o nil si no tiene pistas.txt.
    static func leer(_ carpeta: URL) -> [Pista]? {
        guard let texto = try? String(contentsOf: carpeta.appendingPathComponent("pistas.txt"), encoding: .utf8)
        else { return nil }
        return texto.split(separator: "\n").compactMap { linea in
            let partes = linea.split(separator: "\t")
            guard partes.count == 3, let segundo = Double(partes[1]) else { return nil }
            return Pista(url: carpeta.appendingPathComponent(String(partes[2])), segundo: segundo,
                         sistema: partes[0] == "sistema")
        }
    }
}

// MARK: - Audio del sistema

final class AudioSistema {
    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var agregado = AudioObjectID(kAudioObjectUnknown)
    private var proceso: AudioDeviceIOProcID?
    private let escritor = Escritor()
    private let registro: RegistroPistas
    private let cronometro: Cronometro
    private var formato: AVAudioFormat?
    private var trozos = 0

    init(registro: RegistroPistas, cronometro: Cronometro) {
        self.registro = registro
        self.cronometro = cronometro
    }

    func empezar() throws {
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

        self.formato = formato
        try rotar()
        let escritor = escritor, cronometro = cronometro
        try exigir(AudioDeviceCreateIOProcIDWithBlock(&proceso, agregado, nil) { _, entrada, _, _, _ in
            guard !cronometro.pausado,
                  let buffer = AVAudioPCMBuffer(pcmFormat: formato, bufferListNoCopy: entrada) else { return }
            escritor.escribir(buffer)
        }, "conectar la lectura")
        try exigir(AudioDeviceStart(agregado, proceso), "empezar")
    }

    /// Cierra el trozo en curso y sigue en uno nuevo (ver SEGUNDOS_POR_TROZO).
    func rotar() throws {
        guard let formato else { return }
        trozos += 1
        let url = registro.carpeta.appendingPathComponent("sistema-\(trozos).caf")
        let nuevo = try AVAudioFile(forWriting: url, settings: ajustesAAC(formato),
                                    commonFormat: formato.commonFormat, interleaved: formato.isInterleaved)
        escritor.cambiar(a: nuevo)
        registro.anotar(Pista(url: url, segundo: cronometro.segundo(), sistema: true))
    }

    func detener() {
        if let proceso {
            AudioDeviceStop(agregado, proceso)
            AudioDeviceDestroyIOProcID(agregado, proceso)
        }
        if agregado != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(agregado) }
        if tap != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tap) }
        escritor.cambiar(a: nil)
        proceso = nil
        formato = nil
        agregado = AudioObjectID(kAudioObjectUnknown)
        tap = AudioObjectID(kAudioObjectUnknown)
    }
}

// MARK: - Microfono

final class Microfono {
    private let carpeta: URL
    private let cronometro: Cronometro
    private var motor: AVAudioEngine?
    private let escritor = Escritor()
    private let registro: RegistroPistas
    private var mono: AVAudioFormat?
    private var archivos = 0

    init(registro: RegistroPistas, cronometro: Cronometro) {
        self.registro = registro
        self.carpeta = registro.carpeta
        self.cronometro = cronometro
    }

    private func nuevoArchivo(_ mono: AVAudioFormat) throws -> (URL, AVAudioFile) {
        let url = carpeta.appendingPathComponent("microfono-\(archivos + 1).caf")
        let archivo = try AVAudioFile(forWriting: url, settings: ajustesAAC(mono),
                                      commonFormat: .pcmFormatFloat32, interleaved: false)
        archivos += 1
        return (url, archivo)
    }

    /// Cierra el archivo en curso y sigue en otro, con el mismo motor: abrir un motor
    /// nuevo cada 5 minutos haria cambiar de modo a los AirPods (ver INTENTOS_CAMBIO_MICROFONO).
    func rotar() {
        guard motor != nil, let mono, let (url, archivo) = try? nuevoArchivo(mono) else { return }
        escritor.cambiar(a: archivo)
        registro.anotar(Pista(url: url, segundo: cronometro.segundo(), sistema: false))
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
        let url: URL, archivo: AVAudioFile
        do {
            (url, archivo) = try nuevoArchivo(mono)
        } catch {
            registrar("no se pudo crear el archivo \(archivos + 1) con \(formato): \(error)")
            return false
        }
        oyoAlgo = false
        let cronometro = cronometro, escritor = escritor
        entrada.installTap(onBus: 0, bufferSize: 4096, format: formato) { [weak self] buffer, _ in
            guard let datos = buffer.floatChannelData?[0],
                  let unCanal = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: buffer.frameLength) else { return }
            unCanal.frameLength = buffer.frameLength
            unCanal.floatChannelData![0].update(from: datos, count: Int(buffer.frameLength))
            // En pausa se sigue oyendo, solo no se escribe: asi el aviso de
            // "sin voz" sigue valiendo para un tramo que se abre en la pausa.
            if !cronometro.pausado { escritor.escribir(unCanal) }
            if self?.oyoAlgo == false, (0..<Int(buffer.frameLength)).contains(where: { datos[$0] != 0 }) {
                self?.oyoAlgo = true
            }
        }
        do {
            try motor.start()
        } catch {
            registrar("no partió el archivo \(archivos): \(error)")
            entrada.removeTap(onBus: 0)
            motoresRetirados.append(motor)
            return false
        }
        self.motor = motor
        self.mono = mono
        escritor.cambiar(a: archivo)
        registro.anotar(Pista(url: url, segundo: cronometro.segundo(), sistema: false))
        inicioTramo = Date()
        registrar("archivo \(archivos) con \(AVCaptureDevice.default(for: .audio)?.localizedName ?? "?"), \(formato)")
        return true
    }

    // Un microfono de verdad nunca entrega ceros exactos: siempre hay algo de
    // ruido. Ceros exactos son un permiso negado, un dispositivo sin microfono
    // o el del Mac con la tapa cerrada. Paso en la prueba del 14-10-2026: 53 s
    // de microfono en -91 dB sin ningun aviso, con el Mac cerrado y un parlante.
    // Lo usa el controlador para el icono y la notificacion (ver dibujar()).
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
        if let motor { motoresRetirados.append(motor) }  // nunca destruirlo, ver motoresRetirados
        escritor.cambiar(a: nil)
        motor = nil
        mono = nil
    }
}

// MARK: - Una grabacion en curso

final class Sesion {
    let carpeta: URL
    let cronometro: Cronometro
    let registro: RegistroPistas
    var inicio: Date { cronometro.inicio }
    // Cuantos recordatorios de la pausa en curso ya salieron.
    var recordatoriosPausa = 0
    // Segundos grabados al detener, sin las pausas: el largo de la mezcla.
    var duracion: Double = 0
    let conPantalla: Bool
    let sistema: AudioSistema
    let microfono: Microfono
    var sistemaOK = false
    var stream: SCStream?
    // Mientras graba, el Mac no entra en reposo por inactividad: en una reunion
    // larga uno escucha sin tocar el teclado. Zoom suele impedirlo por su
    // cuenta, Meet en el navegador no siempre. No evita el reposo de cerrar la
    // tapa sin pantalla externa ni corriente: eso lo decide macOS.
    var sinReposo: NSObjectProtocol?
    var video: URL { carpeta.appendingPathComponent("pantalla.mov") }
    // Se escribe al empezar a entregar. Una carpeta con pistas.txt y sin esta marca es
    // una grabacion que se corto: al abrirse, la app la rearma (ver recuperarCaidas).
    var marcaEntregada: URL { carpeta.appendingPathComponent("entregada") }
    static let sello: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH.mm.ss"
        return f
    }()

    init(conPantalla: Bool) throws {
        self.conPantalla = conPantalla
        cronometro = Cronometro()
        carpeta = CARPETA_PISTAS.appendingPathComponent("Grabadora \(Sesion.sello.string(from: cronometro.inicio))")
        try FileManager.default.createDirectory(at: carpeta, withIntermediateDirectories: true)
        registro = RegistroPistas(carpeta: carpeta)
        sistema = AudioSistema(registro: registro, cronometro: cronometro)
        microfono = Microfono(registro: registro, cronometro: cronometro)
    }

    /// Una grabacion que se corto, para entregarla con lo que se alcanzo a guardar.
    init?(recuperando carpeta: URL) {
        guard let inicio = Sesion.sello.date(from: carpeta.lastPathComponent.replacingOccurrences(of: "Grabadora ", with: "")),
              let pistas = RegistroPistas.leer(carpeta), !pistas.isEmpty else { return nil }
        self.carpeta = carpeta
        cronometro = Cronometro(inicio: inicio)
        conPantalla = FileManager.default.fileExists(atPath: carpeta.appendingPathComponent("pantalla.mov").path)
        registro = RegistroPistas(carpeta: carpeta)
        pistas.forEach(registro.anotarSoloEnMemoria)
        sistema = AudioSistema(registro: registro, cronometro: cronometro)
        microfono = Microfono(registro: registro, cronometro: cronometro)
        sistemaOK = pistas.contains { $0.sistema }
        // El largo de la mezcla: hasta donde llega la pista que termina mas tarde.
        duracion = pistas.compactMap { p in duracionAudio(p.url).map { p.segundo + $0 } }.max() ?? 0
    }
}

// MARK: - Controlador

final class Controlador: NSObject, NSApplicationDelegate, SCStreamDelegate, SCRecordingOutputDelegate {
    private let raiz: URL
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var sesion: Sesion?
    private var reloj: Timer?
    private var rotador: Timer?
    private var avisoSinVoz = false

    init(raiz: URL) {
        self.raiz = raiz
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        escucharCambiosDeMicrofono()
        dibujar()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.recuperarCaidas() }
    }

    // Si la app se cayo grabando, launchd la reabre vacia y nadie se entera: paso el
    // 20-10-2026. Al abrirse busca grabaciones sin entregar, avisa y las entrega con
    // lo que se alcanzo a guardar. Solo mira las de las ultimas 24 horas.
    private func recuperarCaidas() {
        guard sesion == nil, let carpetas = try? FileManager.default.contentsOfDirectory(
            at: CARPETA_PISTAS, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        let hora = DateFormatter()
        hora.dateFormat = "HH:mm"
        for carpeta in carpetas where !FileManager.default.fileExists(atPath: carpeta.appendingPathComponent("entregada").path) {
            let cambio = (try? carpeta.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            guard let cambio, Date().timeIntervalSince(cambio) < 24 * 3600,
                  let cortada = Sesion(recuperando: carpeta) else { continue }
            let texto = "La grabación de las \(hora.string(from: cortada.inicio)) se cortó: la grabadora se cerró sola. "
                + "Recuperé lo grabado hasta las \(hora.string(from: cambio)) y se procesa igual."
            registrar("recuperando \(carpeta.lastPathComponent)")
            avisar("Una grabación se cortó", texto)
            avisarAlTelefono("⚠️ " + texto, raiz: raiz)
            terminar(cortada)
            return  // una por vez: la siguiente se recupera la proxima vez que abra
        }
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
        DispatchQueue.main.asyncAfter(deadline: .now() + ESPERA_CAMBIO_MICROFONO) { [weak self] in
            self?.cambiarMicrofono(intento: intento + 1)
        }
    }

    // MARK: - Menu

    private func dibujar() {
        let menu = NSMenu()
        if let sesion {
            let s = Int(sesion.cronometro.segundo())
            if sesion.cronometro.pausado {
                recordarPausa(sesion)
                item.button?.image = nil
                item.button?.title = String(format: "❚❚ %d:%02d", s / 60, s % 60)
                item.button?.contentTintColor = .systemOrange
                menu.addItem(linea("En pausa: no se graba nada"))
                menu.addItem(linea("El Mac sigue despierto hasta que reanudes"))
                menu.addItem(.separator())
                menu.addItem(boton("Reanudar", #selector(reanudar)))
                menu.addItem(boton("Detener y procesar", #selector(detener)))
                item.menu = menu
                return
            }
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
            // ponytail: con pantalla no hay pausa, porque SCRecordingOutput no
            // tiene como pausar y el video quedaria mas largo que el audio. Si hace falta,
            // cortar del video los mismos intervalos al armarlo con ffmpeg.
            if !sesion.conPantalla { menu.addItem(boton("Pausar", #selector(pausar))) }
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

    // En pausa todo sigue abierto (tap, micrófono, permiso sin reposo) y solo
    // se deja de escribir. Así reanudar no rearma nada: si rearmar el tap
    // fallara, se perdería justo la clase que sigue, y nadie lo vería hasta
    // detener. No se probó que falle, se evitó el riesgo.
    @objc private func pausar() {
        guard let sesion else { return }
        sesion.cronometro.pausar()
        registrar("pausa en el segundo \(Int(sesion.cronometro.segundo()))")
        dibujar()
    }

    @objc private func reanudar() {
        guard let sesion else { return }
        sesion.cronometro.reanudar()
        sesion.recordatoriosPausa = 0
        registrar("sigue en el segundo \(Int(sesion.cronometro.segundo()))")
        dibujar()
    }

    // Uno cada 15 minutos de pausa, no uno solo: el primero puede pasar de largo.
    // Va en dibujar(), que corre cada segundo, para no sumar un segundo reloj.
    private func recordarPausa(_ sesion: Sesion) {
        let pausa = sesion.cronometro.enEstaPausa()
        let debidos = Int(pausa / SEGUNDOS_PARA_RECORDAR_PAUSA)
        guard debidos > sesion.recordatoriosPausa else { return }
        sesion.recordatoriosPausa = debidos
        let texto = "La grabadora lleva \(Int(pausa) / 60) minutos en pausa. Si la clase ya siguió, "
            + "reanúdala desde la barra de menú del Mac."
        avisar("Grabación en pausa", texto)
        avisarAlTelefono(texto, raiz: raiz)
    }

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
            try nueva.sistema.empezar()
            nueva.sistemaOK = true
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
        // Sin microfono, el aviso sale en el primer dibujar() (ver Microfono.sinVoz).

        nueva.sinReposo = ProcessInfo.processInfo.beginActivity(options: .idleSystemSleepDisabled,
                                                                reason: "Grabando una reunión")
        sesion = nueva
        reloj = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.dibujar() }
        rotador = Timer.scheduledTimer(withTimeInterval: SEGUNDOS_POR_TROZO, repeats: true) { _ in
            if nueva.sistemaOK { try? nueva.sistema.rotar() }
            nueva.microfono.rotar()
        }
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
        sesion.duracion = sesion.cronometro.segundo()
        sesion.microfono.detener()
        if let sinReposo = sesion.sinReposo { ProcessInfo.processInfo.endActivity(sinReposo) }
        sesion.sistema.detener()
        reloj?.invalidate()
        rotador?.invalidate()
        self.sesion = nil
        avisoSinVoz = false
        dibujar()
        guard let stream = sesion.stream else { return terminar(sesion) }
        // El video se cierra despues, en recordingOutputDidFinishRecording. Pero
        // si la pantalla se corto a mitad de camino, o stopCapture falla, ese
        // aviso puede no llegar nunca, y la reunion entera quedaba esperando sin
        // mezcla ni aviso (revision de codigo del 15-10-2026). Por eso hay plazo:
        // el audio va igual, con o sin video.
        pendiente = sesion
        Task { @MainActor in
            do { try await stream.stopCapture() } catch { self.terminarPendiente() }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + SEGUNDOS_PARA_CERRAR_VIDEO) { [weak self] in
            self?.terminarPendiente()
        }
    }

    // La sesion que espera que ScreenCaptureKit termine de cerrar el video.
    private var pendiente: Sesion?

    // Lo pueden llamar el aviso de video cerrado, el error de stopCapture y el
    // plazo. Solo el primero hace algo.
    private func terminarPendiente() {
        guard let sesion = pendiente else { return }
        pendiente = nil
        terminar(sesion)
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async { self.terminarPendiente() }
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
        alerta.informativeText = "Es opcional. Si fue una clase, escribe solo el nombre del ramo y se "
            + "procesa como clase, con su hoja. Si no escribes nada, en 2 minutos sigue sola."
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

    private func pistasGuardadas(_ sesion: Sesion) -> String {
        "Las pistas quedaron en \(sesion.carpeta.path). Son la única copia de la reunión: no las borres."
    }

    private func entregar(_ sesion: Sesion, tema: String) {
        try? Data().write(to: sesion.marcaEntregada)
        guard let ffmpeg = FFMPEG else {
            avisar("Falta ffmpeg", "Instálalo con: brew install ffmpeg. " + pistasGuardadas(sesion))
            return
        }
        // La carpeta Input sale de config.json, igual que para el pipeline: una
        // segunda copia de la ruta aqui terminaria apuntando a otro lado.
        let config = (try? Data(contentsOf: raiz.appendingPathComponent("orquestador/config.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        guard let rutaInput = (config?["rutas"] as? [String: Any])?["input"] as? String else {
            avisar("No encontré la carpeta Input", "Revisa orquestador/config.json. " + pistasGuardadas(sesion))
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
        var pistas = sesion.registro.pistas
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
            let nivelar = pistas[i].sistema ? "" : "dynaudnorm,"
            return "[\(i):a]\(nivelar)adelay=delays=\(Int(pistas[i].segundo * 1000)):all=1[p\(i)]"
        }
        // Tu voz va en el canal izquierdo y la llamada en el derecho, sin
        // mezclarlas: asi el pipeline transcribe cada una por su lado y marca
        // cada frase como "Tú" u "Otros" (transcripcion.transcribir_dos_pistas).
        // Hasta el 16-10-2026 iba todo mezclado en un canal y el analisis de una
        // reunion no podia saber quien dijo que. Sin una de las dos, va mono.
        // Los dos canales se rellenan con silencio (apad) y la mezcla se corta en
        // la duracion de la sesion (-t). Antes amerge cortaba al terminar el
        // canal mas corto: si el audio de la llamada se caia a mitad de camino,
        // se perdia tu voz desde ahi (revision de codigo del 16-10-2026).
        let sistema = pistas.indices.filter { pistas[$0].sistema }
        let microfonos = pistas.indices.filter { !pistas[$0].sistema }
        let dosPistas = !sistema.isEmpty && !microfonos.isEmpty
        var mezcla = retrasos.joined(separator: ";") + ";"
        if dosPistas {
            mezcla += microfonos.map { "[p\($0)]" }.joined()
                + (microfonos.count > 1 ? "amix=inputs=\(microfonos.count):duration=longest:normalize=0," : "")
                + "apad[tu];" + sistema.map { "[p\($0)]" }.joined()
                + (sistema.count > 1 ? "amix=inputs=\(sistema.count):duration=longest:normalize=0," : "")
                + "aformat=channel_layouts=mono,apad[otros];[tu][otros]amerge=inputs=2[a]"
        } else {
            mezcla += pistas.indices.map { "[p\($0)]" }.joined()
                + "amix=inputs=\(pistas.count):duration=longest:normalize=0[a]"
        }

        // Se escribe con extension .part y se renombra al final: deteccion solo
        // mira audios, y asi nunca ve un .m4a a medio escribir.
        let destino = libre(input, base, "m4a")
        let parcial = destino.appendingPathExtension("part")
        let entradas = pistas.flatMap { ["-i", $0.url.path] }
        let largo = dosPistas ? ["-t", String(format: "%.2f", max(sesion.duracion, 1))] : []
        guard correr(ffmpeg, ["-y"] + entradas + ["-filter_complex", mezcla, "-map", "[a]"] + largo
                             + ["-ac", dosPistas ? "2" : "1", "-c:a", "aac",
                                "-b:a", dosPistas ? "128k" : "96k", "-f", "ipod", parcial.path]),
              (try? FileManager.default.moveItem(at: parcial, to: destino)) != nil else {
            avisar("No se pudo mezclar el audio", pistasGuardadas(sesion))
            return
        }

        if sesion.conPantalla, FileManager.default.fileExists(atPath: sesion.video.path) {
            // ponytail: el video arranca una fraccion de segundo despues que el
            // audio, porque ScreenCaptureKit tarda en partir. Para mirar una
            // lamina no se nota. Si algun dia importa, retrasar el audio aqui.
            let peliculas = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Movies/Reuniones")
            try? FileManager.default.createDirectory(at: peliculas, withIntermediateDirectories: true)
            let video = libre(peliculas, base, "mov")
            // El audio del video va mono: con tu voz en un solo oido no se escucha bien.
            if !correr(ffmpeg, ["-y", "-i", sesion.video.path, "-i", destino.path, "-map", "0:v", "-map", "1:a",
                                "-c:v", "copy", "-c:a", "aac", "-ac", "1", video.path]) {
                avisar("No se pudo armar el video", "El video sin audio quedó en \(sesion.video.path).")
            }
        }

        // El vigilante ve el archivo nuevo en Input y lanza el procesamiento.
        avisar("Reunión grabada", "Quedó en Input y se procesa sola. Te aviso cuando estén las notas.")
    }
}

// MARK: - Arranque

let argumentos = CommandLine.arguments
if argumentos.dropFirst().first == "--probar" {
    probarCronometro()
    probarTrozos()
    exit(0)
}
guard argumentos.count > 1 else {
    FileHandle.standardError.write("Uso: Grabadora <raíz del proyecto>\n".data(using: .utf8)!)
    exit(1)
}

let app = NSApplication.shared
let controlador = Controlador(raiz: URL(fileURLWithPath: argumentos[1]))
app.delegate = controlador
app.run()
