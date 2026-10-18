"""
Pruebas del orquestador que no cuestan tokens ni tocan nada real.

Cubren la logica determinista (fechas, nombres, la hoja de repaso) y, sobre
todo, las garantias de aislamiento del modo ensayo: que un ensayo no pueda
escribir en el vault real, en config.json ni en la carpeta de intermedios.
Esa ultima garantia existe porque ya fallo una vez: un ensayo
dejaba <slug>_skill.json en la carpeta real y eso habria hecho que la
siguiente corrida de verdad se saltara la clase en silencio.

Correr con:
    python3 tests/test_orquestador.py
"""
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from orquestador import carpetas, deteccion, ensayo, nombres
from orquestador import telegram as _telegram

# Las pruebas nunca escriben al Telegram real del estudiante: sin token, cada
# aviso que el pipeline copia al iPhone se omite. Las pruebas del cliente ponen
# uno falso por su cuenta.
_telegram.token = lambda: None
from orquestador.config import PENDIENTES_DIR_POR_DEFECTO, dir_pendientes, usar_dir_pendientes

fallos: list[str] = []


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    print(("  OK   " if cond else "  FALLA") + f"  {nombre}" + (f"  <- {detalle}" if not cond and detalle else ""))
    if not cond:
        fallos.append(nombre)


def probar_nombres() -> None:
    print("\n== nombres ==")
    a = nombres.slug_pendiente("1970-01-20|a.m4a")
    b = nombres.slug_pendiente("1970-01-20|b.m4a")
    check("dos clases con la misma fecha corrupta no colisionan", a != b, f"{a} == {b}")
    check("el slug conserva la fecha legible", a.startswith("1970-01-20"))
    sucio = 'a/b:c*d?e"f<g>h|i'
    check("sanitiza caracteres ilegales de archivo",
          nombres.sanitizar_nombre_archivo(sucio) == "a-b-c-d-e-f-g-h-i")
    check("un titulo con .. no puede escapar de la carpeta",
          "/" not in nombres.sanitizar_nombre_archivo("../../etc/passwd"))
    check("el nombre empieza por la fecha con cero, y termina en el ramo legible",
          nombres.nombre_base("2026-08-05", "Tema", "HISTORIA DEL ARTE") == "05.08.26 - Tema - Historia del Arte")
    check("una sigla corta queda en mayusculas y un articulo no",
          nombres.ramo_legible("MIC - MEDICIÓN DE COMPETENCIAS") == "MIC - Medición de Competencias"
          and nombres.ramo_legible("EL MERCADO") == "El Mercado")
    check("y de ese nombre se recupera la fecha",
          nombres.fecha_del_nombre("05.08.26 - Tema - Historia del Arte.m4a") == "2026-08-05")


def probar_deteccion() -> None:
    print("\n== deteccion de fecha y ramo ==")
    check("rescata la fecha DD.MM.YY del nombre del archivo",
          deteccion._extraer_fecha_de_nombre("CAB7 10.04.25 Performance") == date(2025, 4, 10))
    check("ignora un nombre sin fecha",
          deteccion._extraer_fecha_de_nombre("grabacion final.m4a") is None)
    check("rechaza una fecha imposible", deteccion._extraer_fecha_de_nombre("x 32.13.25 y") is None)

    cfg = {"semestre": {"fecha_inicio": "2026-08-03"},
           "ramos": {"lunes": {"nombre": "R", "perfil_whisper": "es-chile"}}}
    check("una fecha anterior al semestre no asigna ramo (bug de 1970)",
          deteccion.resolver_ramo(date(1970, 1, 20), cfg) is None)
    check("un lunes dentro del semestre si asigna ramo",
          (deteccion.resolver_ramo(date(2026, 8, 3), cfg) or {}).get("nombre") == "R")
    check("un sabado no asigna ramo", deteccion.resolver_ramo(date(2026, 8, 8), cfg) is None)
    check("la semana de semestre se calcula bien",
          deteccion.calcular_semana_semestre(date(2026, 8, 10), "2026-08-03") == 2)


def probar_aislamiento_del_ensayo() -> None:
    """El bloque que mas importa: que un ensayo no pueda tocar nada real."""
    print("\n== aislamiento del modo ensayo ==")
    config_real = {
        "rutas": {"input": "/real/input", "output": "/real/output",
                  "procesados": "/real/procesados",
                  "vault_obsidian": "/real/vault", "transcriptotem": "/real/tt"},
        "semestre": {"fecha_inicio": "2026-08-03"},
        "ramos": {}, "carpetas_ramo": {"RAMO VIEJO": "/real/vault/RAMO VIEJO"},
    }
    pendientes_antes = sorted(p.name for p in PENDIENTES_DIR_POR_DEFECTO.glob("*")) \
        if PENDIENTES_DIR_POR_DEFECTO.exists() else []

    cfg, sandbox = ensayo.preparar(config_real)
    try:
        for clave in ("input", "output", "procesados", "vault_obsidian"):
            check(f"la ruta '{clave}' apunta al sandbox", str(sandbox) in cfg["rutas"][clave])
        check("el ensayo queda marcado", ensayo.es_ensayo(cfg))
        check("una config normal no se confunde con un ensayo", not ensayo.es_ensayo(config_real))
        check("el cache de carpetas reales se descarta", cfg["carpetas_ramo"] == {})
        check("la config original no se muta",
              config_real["rutas"]["vault_obsidian"] == "/real/vault")
        check("los intermedios se redirigen al sandbox", str(sandbox) in str(dir_pendientes()))

        carpeta, _ = carpetas.resolver_carpeta_ramo("RAMO NUEVO", cfg["rutas"]["vault_obsidian"], cfg)
        check("una carpeta de ramo nueva cae dentro del sandbox", str(sandbox) in str(carpeta))
    finally:
        usar_dir_pendientes(None)
        shutil.rmtree(sandbox, ignore_errors=True)

    check("los intermedios vuelven a la carpeta real al terminar",
          dir_pendientes() == PENDIENTES_DIR_POR_DEFECTO)
    pendientes_despues = sorted(p.name for p in PENDIENTES_DIR_POR_DEFECTO.glob("*")) \
        if PENDIENTES_DIR_POR_DEFECTO.exists() else []
    check("el ensayo no dejo archivos en la carpeta real",
          pendientes_antes == pendientes_despues)


def probar_archivado_no_destructivo() -> None:
    print("\n== el ensayo no mueve el audio original ==")
    from orquestador.archivado import archivar_audio

    tmp = Path(tempfile.mkdtemp())
    try:
        origen = tmp / "clase original.m4a"
        origen.write_bytes(b"audio")
        trabajo = {"ramo": "RAMO", "numero_clase": 1, "fecha": "2026-08-05",
                   "archivos": [str(origen)]}

        cfg_ensayo = {"rutas": {"procesados": str(tmp / "dest")}, ensayo.CLAVE: True}
        archivar_audio(trabajo, "Titulo", cfg_ensayo)
        check("en ensayo el audio original sigue en su lugar", origen.is_file())
        check("en ensayo si queda una copia archivada",
              len(list((tmp / "dest").rglob("*.m4a"))) == 1)

        cfg_real = {"rutas": {"procesados": str(tmp / "dest_real")}}
        archivar_audio(trabajo, "Titulo", cfg_real)
        check("fuera de ensayo el audio si se mueve", not origen.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probar_el_nombre_del_archivo_manda_sobre_el_dia() -> None:
    """
    El caso real que esto arregla: el 26-08-2026 (miercoles) se subieron dos
    grabaciones, la clase de BIOLOGIA CELULAR y una reunion informativa
    de un ramo anexo. Como el dia tenia ramo asignado, las dos se archivaron
    como esa clase, sin preguntar, y chocaron de numero.

    Los nombres de archivo de aqui son los reales que pasaron por el sistema,
    con las faltas de ortografia incluidas. Son la calibracion del umbral: si
    alguien lo mueve, esto tiene que seguir pasando.
    """
    print("\n== el nombre del archivo manda sobre el dia de la semana ==")
    from orquestador import ramo_por_nombre as rpn

    config = {
        "ramos": {
            "lunes": {"nombre": "HISTORIA CONTEMPORÁNEA", "perfil_whisper": "es-chile"},
            "martes": {"nombre": "SISTEMAS Y ESTRUCTURA DIGITAL", "perfil_whisper": "es-chile"},
            "miercoles": {"nombre": "BIOLOGÍA CELULAR", "perfil_whisper": "es-chile"},
            "jueves": {"nombre": "TERMODINÁMICA", "perfil_whisper": "es-chile"},
            "viernes": {"nombre": "TALLER DE ANÁLISIS NUMÉRICO III", "perfil_whisper": "es-chile"},
        },
        "semestre": {"fecha_inicio": "2026-08-03"},
    }

    reconoce = [
        ("Historia contemporánea 24.08.26.m4a", "HISTORIA CONTEMPORÁNEA"),
        ("Termodinámica 13.08.26.m4a", "TERMODINÁMICA"),
        ("Biología celular 26.08.26.m4a", "BIOLOGÍA CELULAR"),
        # Con la falta de ortografia del estudiante y sin la palabra "Taller".
        ("Analicis Numerico III 21.08.26.m4a", "TALLER DE ANÁLISIS NUMÉRICO III"),
        # "Sistema" en singular.
        ("Sistema y estructura digital 18.8.26.m4a", "SISTEMAS Y ESTRUCTURA DIGITAL"),
        # Otros formatos de fecha que el estudiante usa.
        ("Historia contemporánea 3-8-26.m4a", "HISTORIA CONTEMPORÁNEA"),
        # Sufijo de parte que agrega el propio sistema al cortar un audio largo.
        ("Historia contemporánea 10.08.26 - parte 03.m4a", "HISTORIA CONTEMPORÁNEA"),
    ]
    for nombre, esperado in reconoce:
        estado, info = rpn.resolver([nombre], config)
        check(f"'{nombre[:34]}' -> {esperado[:24]}",
              estado == rpn.RECONOCIDO and info["nombre"] == esperado,
              f"dio {estado} / {info}")

    # El caso que motivo todo: palabras reales que no son ningun ramo del
    # horario. No puede caer al dia de la semana.
    pregunta = [
        "Evaluación conocimientos intermedios 26.08.26.m4a",
        "Reunión de coordinación 30.08.m4a",
        "Conversación con el ayudante.m4a",
    ]
    for nombre in pregunta:
        estado, info = rpn.resolver([nombre], config)
        check(f"'{nombre[:34]}' -> pregunta",
              estado == rpn.NO_CALZA and info is None, f"dio {estado} / {info}")

    # Un nombre sin contenido no es evidencia de nada: ahi si manda el dia.
    for nombre in ["Nota de voz 3.m4a", "Grabación 12.m4a", "audio.m4a", "Clase.m4a"]:
        estado, _ = rpn.resolver([nombre], config)
        check(f"'{nombre}' -> sin senal, decide el dia", estado == rpn.SIN_SENAL,
              f"dio {estado}")

    # Un ramo agregado a mano tiene que reconocerse igual que los del horario.
    con_adicional = dict(config)
    con_adicional["ramos_adicionales"] = {
        "EIC - EVALUACIÓN INTERMEDIA DE CONOCIMIENTOS": {"perfil_whisper": "es-chile"}
    }
    estado, info = rpn.resolver(["Evaluación conocimientos intermedios 26.08.26.m4a"], con_adicional)
    check("una vez creado el ramo, la siguiente reunion se reconoce sola",
          estado == rpn.RECONOCIDO and info["nombre"].startswith("EIC"),
          f"dio {estado} / {info}")

    # Lo que deja la grabadora de la barra de menu: el tema lo escribe el
    # estudiante y no puede hacer que se pregunte. Vale aunque REUNIÓN no este
    # en ramos_adicionales, como en un clon nuevo.
    con_reunion = dict(config, ramos_adicionales={"REUNIÓN": {"perfil_whisper": "es-chile"}})
    for nombre in ["Reunión Online 14.10.26.m4a",
                   "Reunión Online 14.10.26 - Termodinámica con el grupo.m4a",
                   "Reunión Online 14.10.26 - entrega final (2).m4a"]:
        estado, info = rpn.resolver([nombre], con_reunion)
        check(f"'{nombre[:34]}' -> REUNIÓN",
              estado == rpn.RECONOCIDO and info["nombre"] == "REUNIÓN", f"dio {estado} / {info}")
    # Una clase online grabada con la grabadora: el tema es el ramo (15-10-2026).
    for nombre in ["Reunión Online 15.10.26 - Termodinámica online.m4a",
                   "Reunión Online 15.10.26 - clase de termodinamica.m4a"]:
        estado, info = rpn.resolver([nombre], con_reunion)
        check(f"'{nombre[26:]}' -> clase del ramo",
              estado == rpn.RECONOCIDO and info["nombre"] == "TERMODINÁMICA", f"dio {estado} / {info}")
    estado, info = rpn.resolver(["Reunión Online 14.10.26 - avance.m4a"], config)
    check("sin REUNIÓN en la configuracion, el prefijo igual da REUNIÓN, con el perfil por defecto",
          estado == rpn.RECONOCIDO and info == {"nombre": "REUNIÓN", "perfil_whisper": None},
          f"dio {estado} / {info}")

    # Partes de ramos distintos agrupadas: ya fusiono dos clases una vez.
    estado, _ = rpn.resolver(
        ["Biología celular 19.08.26.m4a", "Termodinámica 20.08.26.m4a"], config)
    check("dos ramos distintos en un mismo trabajo -> pregunta", estado == rpn.NO_CALZA,
          f"dio {estado}")

    # Y el efecto completo sobre la deteccion: el mismo miercoles, dos
    # grabaciones, cada una a su lugar.
    miercoles = date(2026, 8, 26)
    clase = deteccion.resolver_ramo_de_grabacion(
        ["Biología celular 26.08.26.m4a"], miercoles, config)
    reunion = deteccion.resolver_ramo_de_grabacion(
        ["Evaluación conocimientos intermedios 26.08.26.m4a"], miercoles, config)
    check("el miercoles, la clase real sigue siendo la clase",
          clase and clase["nombre"] == "BIOLOGÍA CELULAR", f"dio {clase}")
    check("el mismo miercoles, la reunion no se archiva sola",
          reunion is None, f"dio {reunion}")

    # Sin nombre util, el comportamiento de siempre: manda el dia.
    por_dia = deteccion.resolver_ramo_de_grabacion(["Nota de voz 3.m4a"], miercoles, config)
    check("sin nombre util se mantiene el comportamiento de siempre",
          por_dia and por_dia["nombre"] == "BIOLOGÍA CELULAR", f"dio {por_dia}")


def probar_decisiones_de_pantalla_se_aplican() -> None:
    """
    Que lo elegido en la pantalla llegue intacto al trabajo, sin transcribir
    nada ni abrir ventanas. Cubre en particular la numeracion: un ramo elegido
    a mano no puede numerarse por semana de semestre, que ya dio "Clase -2949"
    en vivo cuando la fecha caia fuera del calendario.
    """
    print("\n== lo elegido en la pantalla se aplica al trabajo ==")
    from orquestador import transcripcion

    with tempfile.TemporaryDirectory() as tmp:
        config = {
            "rutas": {"procesados": tmp},
            "ramos": {"miercoles": {"nombre": "BIOLOGÍA CELULAR", "perfil_whisper": "es-chile"}},
            "ramos_adicionales": {},
        }
        guardados = []
        original_guardar = transcripcion.guardar_config
        transcripcion.guardar_config = lambda c: guardados.append(c)
        try:
            # Omitir no toca nada.
            t = {"clave": "k", "fecha": "2026-08-26", "reconocido": False, "ramo": None}
            r = transcripcion._aplicar_decision_de_pantalla(t, {"que_hacer": "omitir"}, config)
            check("omitir -> el trabajo no sigue", r == "omitir")

            # Solo transcribir deja el texto sin archivar bajo ningun ramo.
            t = {"clave": "k", "fecha": "2026-08-26", "reconocido": True,
                 "ramo": "BIOLOGÍA CELULAR"}
            r = transcripcion._aplicar_decision_de_pantalla(
                t, {"que_hacer": "solo_transcribir"}, config)
            check("solo transcribir -> sigue pero sin ramo",
                  r == "seguir" and not t["reconocido"] and t["ramo"] is None)

            # Un ramo nuevo se guarda para que la proxima vez se reconozca solo.
            t = {"clave": "k", "fecha": "2026-08-26", "reconocido": False, "ramo": None}
            r = transcripcion._aplicar_decision_de_pantalla(
                t, {"que_hacer": "procesar", "ramo": "EIC", "ramo_nuevo": True}, config)
            check("un ramo nuevo queda guardado en config",
                  "EIC" in config.get("ramos_adicionales", {}) and guardados)
            check("y el trabajo queda con ese ramo",
                  t["reconocido"] and t["ramo"] == "EIC")
            check("numerado por orden cronologico, no por semana de semestre",
                  t["numeracion"] == "orden" and t["numero_clase"] == 1,
                  f"dio {t.get('numeracion')} / {t.get('numero_clase')}")

            # Confirmar lo que ya estaba detectado no renumera.
            t = {"clave": "k", "fecha": "2026-08-26", "reconocido": True,
                 "ramo": "BIOLOGÍA CELULAR", "numero_clase": 4,
                 "numeracion": "calendario"}
            transcripcion._aplicar_decision_de_pantalla(
                t, {"que_hacer": "procesar", "ramo": "BIOLOGÍA CELULAR"}, config)
            check("confirmar lo detectado conserva la numeracion por calendario",
                  t["numero_clase"] == 4 and t["numeracion"] == "calendario",
                  f"dio {t.get('numeracion')} / {t.get('numero_clase')}")
        finally:
            transcripcion.guardar_config = original_guardar


def probar_configurar_sistema_conserva_lo_que_no_pregunta() -> None:
    """
    El asistente de configuracion solo pregunta rutas, semestre y horario, y
    guardaba solo eso. Volver a pasar por el, que es lo normal al cambiar de
    semestre, borraba los ramos agregados a mano con su idioma y su
    vocabulario para Whisper.
    """
    print("\n== configurar sistema no borra los ramos agregados ==")
    import builtins
    import contextlib
    import copy
    import io

    from orquestador import setup

    tmp = Path(tempfile.mkdtemp())
    existente = {
        "rutas": {n: str(tmp / n) for n in
                  ("input", "output", "procesados", "vault_obsidian", "transcriptotem")},
        "semestre": {"fecha_inicio": "2026-08-03"},
        "ramos": {d: {"nombre": f"RAMO {d}", "perfil_whisper": "es-chile"}
                  for d in ("lunes", "martes", "miercoles", "jueves", "viernes")},
        "ramos_adicionales": {"RAMO EN INGLES": {"perfil_whisper": "en", "contexto": "jerga"}},
        "carpetas_ramo": {"RAMO lunes": str(tmp / "vault_obsidian" / "RAMO lunes")},
    }
    guardado: dict = {}
    originales = (setup.CONFIG_PATH, setup.cargar_config, setup.guardar_config, builtins.input)
    try:
        # Nada de esto toca el config.json real: se lee una copia en memoria y
        # lo que se guardaria queda en `guardado`.
        setup.CONFIG_PATH = tmp / "config.json"
        setup.CONFIG_PATH.write_text("{}", encoding="utf-8")
        setup.cargar_config = lambda: copy.deepcopy(existente)
        setup.guardar_config = guardado.update
        builtins.input = lambda *_: ""  # Enter en cada pregunta: acepta lo que ya habia
        with contextlib.redirect_stdout(io.StringIO()):
            setup.main()
    finally:
        setup.CONFIG_PATH, setup.cargar_config, setup.guardar_config, builtins.input = originales
        shutil.rmtree(tmp, ignore_errors=True)

    check("conserva los ramos agregados a mano",
          guardado.get("ramos_adicionales") == existente["ramos_adicionales"],
          str(guardado.get("ramos_adicionales")))
    check("conserva las carpetas de ramo ya ubicadas",
          guardado.get("carpetas_ramo") == existente["carpetas_ramo"])
    check("igual guarda lo que pregunta",
          guardado.get("semestre") == {"fecha_inicio": "2026-08-03"}
          and guardado.get("ramos", {}).get("lunes", {}).get("nombre") == "RAMO lunes")


def probar_lo_que_no_es_clase_no_se_pisa_ni_queda_en_input() -> None:
    """
    Dos grabaciones que no eran clase, del mismo dia, escribian el mismo .txt y
    la segunda borraba la transcripcion de la primera. Ademas el audio se
    quedaba en Input: ningun clic lo volvia a tomar, pero si era lo unico ahi,
    cada clic avisaba "Todavia copiando".
    """
    print("\n== lo que no es clase no se pisa ni se queda en Input ==")
    from orquestador import estado_vivo, transcripcion
    from orquestador.bitacora import Bitacora

    base = Path(tempfile.mkdtemp())
    # La seccion critica escribe en el estado que lee el icono de la barra: que
    # escriba en uno de prueba y no en el real.
    original_estado = estado_vivo.ESTADO_PATH
    estado_vivo.ESTADO_PATH = base / "estado_actual.json"
    try:
        entrada = base / "Input"
        entrada.mkdir()
        config = {"rutas": {"output": str(base / "Output"), "procesados": str(base / "Procesados")}}
        apartados = base / "Procesados" / "_Sin clasificar"
        b = Bitacora(base / "bitacora.json")
        for nombre in ("Reunion.m4a", "Charla.m4a"):
            audio = entrada / nombre
            audio.write_bytes(nombre.encode())
            t = {"fecha": "2026-09-15", "dia_semana": "martes", "archivos": [str(audio)]}
            transcripcion._guardar_sin_clasificar(t, f"texto de {nombre}", config["rutas"]["output"])
            transcripcion._sacar_de_input(t, config, "_Sin clasificar", b)

        check("dos del mismo dia dejan dos transcripciones",
              len(list((base / "Output" / "Sin clasificar").glob("*.txt"))) == 2)
        check("Input queda vacio", not any(entrada.iterdir()))
        check("los audios quedan en Procesados/_Sin clasificar", len(list(apartados.iterdir())) == 2)

        otro = entrada / "Reunion.m4a"
        otro.write_bytes(b"otra reunion")
        transcripcion._sacar_de_input({"fecha": "2026-09-15", "archivos": [str(otro)]},
                                      config, "_Sin clasificar")
        check("un nombre repetido se numera en vez de reemplazar al anterior",
              len(list(apartados.iterdir())) == 3)

        b.deshacer()
        check("detener la corrida devuelve los audios a Input",
              sorted(p.name for p in entrada.iterdir()) == ["Charla.m4a", "Reunion.m4a"]
              and (entrada / "Reunion.m4a").read_bytes() == b"Reunion.m4a")
    finally:
        estado_vivo.ESTADO_PATH = original_estado
        shutil.rmtree(base, ignore_errors=True)


def probar_bitacora_deshace_todo() -> None:
    """
    El aborto promete dejar el disco como estaba. Si el deshacer falla, esa
    promesa se rompe justo cuando el estudiante ya decidio cancelar, o sea en
    el peor momento para descubrirlo.
    """
    print("\n== abortar deja todo como estaba ==")
    from orquestador import bitacora
    from orquestador.bitacora import Bitacora

    base = Path(tempfile.mkdtemp())
    original_para_revisar = bitacora.CARPETA_PARA_REVISAR
    bitacora.CARPETA_PARA_REVISAR = base / "Para revisar y borrar"
    try:
        # Estado inicial: una carpeta de ramo con una nota que ya existia.
        vault = base / "vault" / "RAMO"
        vault.mkdir(parents=True)
        indice = vault / "indice.md"
        indice.write_text("contenido original", encoding="utf-8")

        entrada = base / "Input"
        entrada.mkdir()
        audio = entrada / "clase.m4a"
        audio.write_bytes(b"audio")

        b = Bitacora(base / "bitacora.json")

        # Lo que hace una corrida.
        b.fotografiar_carpeta(vault)
        (vault / "Fuente - nueva.md").write_text("nota nueva", encoding="utf-8")
        (vault / "Aprendizaje - nueva.md").write_text("otra nota", encoding="utf-8")
        indice.write_text("contenido MODIFICADO por la skill", encoding="utf-8")

        carpeta_nueva = base / "vault" / "RAMO RECIEN CREADO"
        carpeta_nueva.mkdir()
        b.carpeta_creada(carpeta_nueva)

        hoja = base / "salida.html"
        hoja.write_bytes(b"<html></html>")
        b.archivo_creado(hoja)

        destino = base / "Procesados" / "clase archivada.m4a"
        destino.parent.mkdir(parents=True)
        b.audio_movido(audio, destino)
        shutil.move(str(audio), str(destino))

        check("durante la corrida el audio ya no esta en Input", not audio.exists())

        # Abortar.
        revertido = b.deshacer()

        check("las notas nuevas salieron del vault",
              not (vault / "Fuente - nueva.md").exists()
              and not (vault / "Aprendizaje - nueva.md").exists())
        check("y quedaron apartadas, no borradas",
              len(list(bitacora.CARPETA_PARA_REVISAR.rglob("*.md"))) == 2)
        check("la nota que ya existia sigue ahi", indice.is_file())
        check("y con su contenido original",
              indice.read_text(encoding="utf-8") == "contenido original",
              indice.read_text(encoding="utf-8"))
        check("la carpeta creada se elimino", not carpeta_nueva.exists())
        check("la hoja se borro", not hoja.exists())
        check("el audio volvio a Input", audio.is_file() and not destino.exists())
        check("el audio conserva su contenido", audio.read_bytes() == b"audio")
        check("se informa lo que se revirtio", len(revertido) >= 4, str(revertido))
        check("la bitacora se limpia al terminar", not (base / "bitacora.json").exists())
    finally:
        bitacora.CARPETA_PARA_REVISAR = original_para_revisar
        shutil.rmtree(base, ignore_errors=True)


def probar_bitacora_no_borra_lo_ajeno() -> None:
    print("\n== abortar no toca lo que no puso el sistema ==")
    from orquestador import bitacora
    from orquestador.bitacora import Bitacora

    base = Path(tempfile.mkdtemp())
    original_para_revisar = bitacora.CARPETA_PARA_REVISAR
    bitacora.CARPETA_PARA_REVISAR = base / "Para revisar y borrar"
    try:
        carpeta = base / "RAMO"
        carpeta.mkdir()
        b = Bitacora(base / "b.json")
        b.carpeta_creada(carpeta)
        ajeno = carpeta / "algo mio.md"
        ajeno.write_text("no es del sistema", encoding="utf-8")

        b.deshacer()
        check("una carpeta con algo dentro no se borra", carpeta.is_dir())
        check("el archivo ajeno sigue intacto", ajeno.is_file())

        # Una carpeta de ramo que ya existia. Lo que aparece en ella durante la
        # corrida puede ser del estudiante, guardado desde Obsidian mientras el
        # pipeline trabajaba: deshacer lo aparta, nunca lo borra.
        ramo = base / "RAMO EXISTENTE"
        ramo.mkdir()
        (ramo / "vieja.md").write_text("ya estaba", encoding="utf-8")
        b = Bitacora(base / "b2.json")
        b.fotografiar_carpeta(ramo)
        (ramo / "imagen mia.png").write_bytes(b"png")

        b.deshacer()
        apartada = list(bitacora.CARPETA_PARA_REVISAR.rglob("imagen mia.png"))
        check("lo que aparecio durante la corrida no se borra",
              len(apartada) == 1 and apartada[0].read_bytes() == b"png")
        check("pero sale de la carpeta del ramo", not (ramo / "imagen mia.png").exists())
        check("y lo que ya estaba sigue ahi",
              (ramo / "vieja.md").read_text(encoding="utf-8") == "ya estaba")
    finally:
        bitacora.CARPETA_PARA_REVISAR = original_para_revisar
        shutil.rmtree(base, ignore_errors=True)


def probar_seccion_critica() -> None:
    """Durante el movimiento del audio el aborto se encola, no se ignora."""
    print("\n== el tramo delicado no se puede cortar por la mitad ==")
    from orquestador import cancelacion

    llego_al_final = False
    try:
        with cancelacion.seccion_critica():
            check("mientras dura, el estado dice que no se puede interrumpir",
                  cancelacion._seccion_critica.locked())
            # Simula el pedido de aborto llegando justo aqui.
            cancelacion._al_recibir_senal(15, None)
            llego_al_final = True
    except cancelacion.Abortado:
        check("el aborto pedido adentro se aplica al salir, no se pierde", True)
    else:
        check("el aborto pedido adentro se aplica al salir, no se pierde", False)

    check("el tramo se completo antes de abortar", llego_al_final)
    check("el candado queda libre despues", not cancelacion._seccion_critica.locked())


def probar_detener_durante_la_transcripcion_detiene_de_verdad() -> None:
    """
    Abortado heredaba de Exception, y el except que protege la transcripcion la
    atrapaba: apretar "Detener" mientras Whisper trabajaba mostraba "Fallo al
    transcribir: Abortado" y la corrida seguia con la grabacion siguiente. Al
    arreglarlo aparecio otro hueco: deshacer borraba la transcripcion de una
    grabacion anterior pero la dejaba marcada como tomada, y ningun clic la
    volvia a procesar.
    """
    print("\n== detener durante la transcripcion detiene de verdad ==")
    from orquestador import cancelacion, transcripcion
    from orquestador.bitacora import Bitacora

    base = Path(tempfile.mkdtemp())
    emitidos = base / "trabajos_emitidos.json"
    emitidos.write_text("{}", encoding="utf-8")
    trabajos = [
        {"clave": f"2026-09-15|{n}", "fecha": "2026-09-15", "dia_semana": "martes",
         "archivos": [str(base / n)], "reconocido": True, "ramo": "R",
         "perfil_whisper": "es-chile", "numero_clase": 1, "numeracion": "calendario"}
        for n in ("primera.m4a", "segunda.m4a")
    ]
    avisos = []

    def transcribir(trabajo, config):
        if trabajo["archivos"][0].endswith("segunda.m4a"):
            raise cancelacion.Abortado()  # "Detener" llega mientras Whisper trabaja
        return "texto de la primera"

    originales = (transcripcion.construir_trabajos, transcripcion.transcribir_trabajo,
                  transcripcion.notificar_error, deteccion.TRABAJOS_EMITIDOS_PATH)
    transcripcion.construir_trabajos = lambda c: [dict(t) for t in trabajos]
    transcripcion.transcribir_trabajo = transcribir
    transcripcion.notificar_error = lambda contexto, detalle: avisos.append(detalle)
    deteccion.TRABAJOS_EMITIDOS_PATH = emitidos
    usar_dir_pendientes(base / "pendientes")
    b = Bitacora(base / "bitacora.json")
    detuvo = False
    try:
        transcripcion.procesar_pendientes(
            {"rutas": {"procesados": str(base), "output": str(base)},
             "ramos": {}, "ramos_adicionales": {}},
            bitacora=b,
            decidir=lambda ts, c: {t["clave"]: {"que_hacer": "procesar", "ramo": "R"} for t in ts},
        )
    except cancelacion.Abortado:
        detuvo = True
        b.deshacer()
    finally:
        (transcripcion.construir_trabajos, transcripcion.transcribir_trabajo,
         transcripcion.notificar_error, deteccion.TRABAJOS_EMITIDOS_PATH) = originales
        usar_dir_pendientes(None)
        if not detuvo:
            b.limpiar()

    try:
        check("detener durante la transcripcion detiene la corrida", detuvo)
        check("y no se informa como un fallo de transcripcion", not avisos, str(avisos))
        check("deshacer quita la marca de la grabacion ya transcrita",
              emitidos.read_text(encoding="utf-8").strip() == "{}",
              emitidos.read_text(encoding="utf-8"))
        check("y tambien su transcripcion", not any((base / "pendientes").glob("*")))

        # Un segundo "Detener" mientras se deshace no corta el deshacer a medias.
        class BitacoraQueRecibeOtroDetener:
            def deshacer(self):
                cancelacion._al_recibir_senal(15, None)
                return ["todo revertido"]

        original_bitacora = cancelacion._bitacora
        cancelacion._bitacora = BitacoraQueRecibeOtroDetener()
        try:
            check("un segundo Detener no interrumpe el deshacer",
                  cancelacion.deshacer_todo() == ["todo revertido"])
        except cancelacion.Abortado:
            check("un segundo Detener no interrumpe el deshacer", False)
        finally:
            cancelacion._bitacora = original_bitacora
            cancelacion._deshaciendo = False
    finally:
        shutil.rmtree(base, ignore_errors=True)


def probar_audio_largo_se_corta_solo() -> None:
    """
    Una clase real de 1 h 56 min hizo que Whisper devolviera cero caracteres sin
    lanzar ningun error, y el pipeline siguio hasta entregarle un archivo vacio
    al modelo. Ahora los audios largos se cortan antes de transcribir, y una
    transcripcion vacia detiene la corrida en vez de gastar una llamada.
    """
    print("\n== audios largos y transcripciones vacias ==")
    import subprocess as sp

    from orquestador import transcripcion as tr
    from orquestador.estado_vivo import duracion_audio_segundos

    check("el umbral deja pasar una clase corta sin cortarla",
          tr.UMBRAL_PARTIR_SEGUNDOS > 30 * 60)
    check("el trozo no supera lo ya probado", tr.DURACION_TROZO_SEGUNDOS <= 20 * 60)

    tmp = Path(tempfile.mkdtemp())
    try:
        audio = tmp / "largo.m4a"
        sp.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=50",
                "-c:a", "aac", str(audio), "-y"], check=True, capture_output=True)

        original = tr.DURACION_TROZO_SEGUNDOS
        tr.DURACION_TROZO_SEGUNDOS = 20
        try:
            trozos = tr._partir_audio(str(audio), tmp / "trozos")
            check("un audio largo se parte en varios trozos", len(trozos) == 3, str(len(trozos)))
            check("los trozos quedan en orden",
                  [x.name for x in trozos] == sorted(x.name for x in trozos))
            total = sum(duracion_audio_segundos(x) or 0 for x in trozos)
            check("no se pierde audio al cortar", abs(total - 50) < 2, f"{total:.1f}s de 50s")

            # Con un silencio cerca del corte, el corte cae ahi y no a mitad de palabra.
            con_pausa = tmp / "con_pausa.m4a"
            sp.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=17",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=31",
                    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-c:a", "aac", str(con_pausa), "-y"],
                   check=True, capture_output=True)
            trozos = tr._partir_audio(str(con_pausa), tmp / "trozos_pausa")
            primero = duracion_audio_segundos(trozos[0]) or 0
            check("el primer corte cae en el silencio (17 a 19 s), no en el segundo 20",
                  17 <= primero <= 19.2, f"{primero:.1f}s")
        finally:
            tr.DURACION_TROZO_SEGUNDOS = original

        check("sin silencio cerca, se corta donde tocaba",
              tr.elegir_cortes([5.0, 95.0], 120, 40, 10) == [40, 80])
        check("con silencio cerca, el corte se corre hasta el",
              tr.elegir_cortes([37.0, 84.0, 89.0], 120, 40, 10) == [37.0, 84.0])

        try:
            tr._verificar_parte("", str(audio), 900)
            check("una transcripcion vacia detiene la corrida", False)
        except ValueError as e:
            check("una transcripcion vacia detiene la corrida", True)
            check("el mensaje avisa que el audio no se perdio", "sigue donde estaba" in str(e))
            check("y dice que se puede volver a procesar", "volver a procesar" in str(e))

        try:
            tr._verificar_parte("palabra " * 300, str(audio), 900)
            check("una transcripcion normal pasa sin molestar", True)
        except ValueError:
            check("una transcripcion normal pasa sin molestar", False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probar_el_borrado_no_alcanza_tus_carpetas() -> None:
    """
    Cortar el audio en trozos implica borrarlos despues, y ese es el unico
    borrado que el sistema hace solo. Se prueba que NO PUEDA tocar otra cosa,
    no que "normalmente no la toque": perder grabaciones o notas de clase no
    tiene vuelta atras.
    """
    print("\n== el borrado de trozos no puede alcanzar nada mas ==")
    import tempfile as tf

    from orquestador import transcripcion as tr

    # 1. Solo borra carpetas creadas por el sistema, dentro del area temporal.
    propia = Path(tf.mkdtemp(prefix=tr.PREFIJO_TROZOS))
    (propia / "trozo.m4a").write_bytes(b"x")
    tr._borrar_carpeta_de_trozos(propia)
    check("borra su propia carpeta temporal", not propia.exists())

    # 2. Una carpeta temporal ajena (otro prefijo) no se toca.
    ajena = Path(tf.mkdtemp(prefix="algo_del_usuario_"))
    try:
        (ajena / "importante.md").write_text("no borrar", encoding="utf-8")
        tr._borrar_carpeta_de_trozos(ajena)
        check("no toca una carpeta temporal con otro nombre", ajena.exists())
    finally:
        shutil.rmtree(ajena, ignore_errors=True)

    # 3. Aunque el nombre calce, fuera del area temporal no se borra.
    fuera = Path(tf.mkdtemp()) / f"{tr.PREFIJO_TROZOS}falsa"
    fuera.mkdir(parents=True)
    simulada = fuera.parent / "Procesados"
    simulada.mkdir()
    (simulada / "clase.m4a").write_bytes(b"grabacion real")
    try:
        for objetivo in (Path.home() / "Documents", simulada, Path("/")):
            tr._borrar_carpeta_de_trozos(objetivo)
        check("no borra Documents, ni Procesados, ni la raiz",
              (Path.home() / "Documents").exists() and (simulada / "clase.m4a").exists())
    finally:
        shutil.rmtree(fuera.parent, ignore_errors=True)

    # 4. La ruta del audio original nunca entra en la carpeta de trozos.
    import inspect
    fuente = inspect.getsource(tr._transcribir_archivo)
    check("los trozos se crean en una carpeta temporal recien hecha",
          "tempfile.mkdtemp(prefix=PREFIJO_TROZOS)" in fuente)
    # Se mira el codigo, no la documentacion: el docstring justamente explica
    # que no se usa glob, y buscar la palabra ahi daba un falso positivo.
    cuerpo = inspect.getsource(tr._borrar_carpeta_de_trozos)
    cuerpo = cuerpo.split('"""')[-1]
    check("no se borra por patron ni comodin",
          "glob" not in cuerpo and "*" not in cuerpo, cuerpo.strip()[:60])
    check("se comprueba el area temporal antes de borrar",
          "gettempdir" in cuerpo and "is_relative_to" in cuerpo)


def probar_dos_clases_no_se_fusionan() -> None:
    """
    Dos clases reales terminaron en un mismo documento: "Biologia
    celular 19.08.26" y "Termodinamica 20.08.26" se copiaron a Input la
    misma noche, las dos quedaron con mtime del 20, se agruparon como una sola
    clase de dos partes y mezclaron dos ramos distintos. El nombre tenia la
    fecha correcta y se estaba ignorando.
    """
    print("\n== dos clases distintas no se fusionan ==")
    import os
    from datetime import timedelta

    hoy = date.today()
    ayer = hoy - timedelta(days=1)

    tmp = Path(tempfile.mkdtemp())
    try:
        def crear(nombre: str, mtime_dia: date) -> Path:
            f = tmp / nombre
            f.write_bytes(b"audio")
            ts = __import__("time").mktime(mtime_dia.timetuple())
            os.utime(f, (ts, ts))
            return f

        # El caso real: dos clases de dias distintos, copiadas el mismo dia.
        a = crear(f"Biología celular {ayer:%d.%m.%y}.m4a", hoy)
        b = crear(f"Termodinámica {hoy:%d.%m.%y}.m4a", hoy)

        check("la fecha del nombre gana sobre la del archivo",
              deteccion._resolver_fecha_archivo(a) == ayer,
              str(deteccion._resolver_fecha_archivo(a)))
        grupos = deteccion.agrupar_por_fecha([a, b])
        check("quedan como dos clases separadas", len(grupos) == 2, str(list(grupos)))
        check("ninguna queda con dos audios",
              all(len(v) == 1 for v in grupos.values()))

        # Con guiones pasa lo mismo: el estudiante escribe la fecha de las dos formas.
        a2 = crear(f"Biología celular {ayer.day}-{ayer.month}-{ayer:%y}.m4a", hoy)
        check("una fecha con guiones en el nombre tambien gana sobre la del archivo",
              deteccion._resolver_fecha_archivo(a2) == ayer,
              str(deteccion._resolver_fecha_archivo(a2)))

        # Sin fecha en el nombre se sigue usando la del archivo.
        c = crear("grabacion sin fecha.m4a", ayer)
        check("sin fecha en el nombre manda el archivo",
              deteccion._resolver_fecha_archivo(c) == ayer)

        # Una fecha del futuro en el nombre no se cree.
        futuro = hoy + timedelta(days=30)
        d = crear(f"clase {futuro:%d.%m.%y}.m4a", hoy)
        check("una fecha futura en el nombre se descarta",
              deteccion._resolver_fecha_archivo(d) == hoy)

        # Un numero que parece fecha pero es de hace decadas tampoco.
        e = crear("Capitulo 1.2.99 repaso.m4a", ayer)
        check("un numero viejo que parece fecha se descarta",
              deteccion._resolver_fecha_archivo(e) == ayer)

        # Una clase partida de verdad por el estudiante si debe agruparse.
        f1 = crear(f"Historia {ayer:%d.%m.%y} parte A.m4a", hoy)
        f2 = crear(f"Historia {ayer:%d.%m.%y} parte B.m4a", hoy)
        g = deteccion.agrupar_por_fecha([f1, f2])
        check("dos archivos de la MISMA fecha si se agrupan juntos",
              len(g) == 1 and len(list(g.values())[0]) == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probar_las_notificaciones_no_se_pierden() -> None:
    """
    Una actualizacion de Homebrew dejo terminal-notifier sin poder entregar
    avisos, y el codigo no miraba el resultado: el estudiante habria dejado de
    recibir notificaciones sin enterarse. Ahora un fallo cae a la via nativa.
    """
    print("\n== las notificaciones no se pierden en silencio ==")
    from orquestador import notificaciones as n

    original_tn, original_as = n._con_terminal_notifier, n._con_applescript
    # Cada aviso se anota en Estado.txt. Sin esto la prueba escribia tres lineas
    # de relleno ("t - s - m") en el Estado.txt real cada vez que se ejecutaba.
    original_estado = n.ESTADO_PATH
    tmp_estado = Path(tempfile.mkdtemp())
    n.ESTADO_PATH = tmp_estado / "Estado.txt"
    try:
        # Cuando la via preferida funciona, no se usa el respaldo.
        usados = []
        n._con_terminal_notifier = lambda *a, **k: usados.append("tn") or True
        n._con_applescript = lambda *a, **k: usados.append("as")
        n._notificar("t", "s", "m")
        check("si terminal-notifier entrega, no se duplica el aviso", usados == ["tn"], str(usados))

        # Cuando falla, el aviso igual llega.
        usados.clear()
        n._con_terminal_notifier = lambda *a, **k: usados.append("tn") or False
        n._notificar("t", "s", "m")
        check("si terminal-notifier falla, se usa la via nativa",
              usados == ["tn", "as"], str(usados))

        # Y si las dos fallan, no revienta el procesamiento.
        def explota(*a, **k):
            raise RuntimeError("sin notificaciones")
        n._con_terminal_notifier = lambda *a, **k: False
        n._con_applescript = explota
        try:
            n._notificar("t", "s", "m")
            check("un aviso que no se puede entregar no tumba la clase", True)
        except Exception as e:
            check("un aviso que no se puede entregar no tumba la clase", False,
                  f"propago {type(e).__name__}")
    finally:
        n._con_terminal_notifier, n._con_applescript = original_tn, original_as
        n.ESTADO_PATH = original_estado
        shutil.rmtree(tmp_estado, ignore_errors=True)

    # Pase lo que pase, queda registrado en Estado.txt.
    import inspect
    check("todo aviso queda anotado antes de intentar entregarlo",
          "_anotar_estado" in inspect.getsource(n._notificar))
    check("el texto se escapa antes de pasarlo a AppleScript",
          "_escapar_applescript" in inspect.getsource(n._con_applescript))


def probar_error_de_sesion_se_explica() -> None:
    """
    La sesion de Claude Code caduco y el estudiante leyo "La skill termino con
    error: success", que no dice nada. La explicacion venia en el campo
    `result` del SDK y no se estaba mirando, asi que no habia forma de saber
    que lo que tocaba era volver a iniciar sesion.
    """
    print("\n== un error del modelo dice que paso de verdad ==")
    from orquestador.skill_runner import _es_sesion_caducada, describir_error_sdk

    class Falso:
        subtype = "success"
        is_error = True
        result = "Failed to authenticate: OAuth session expired and could not be refreshed"
        api_error_status = None
        terminal_reason = None
        errors = None

    texto = describir_error_sdk(Falso())
    check("el mensaje incluye la causa real", "OAuth session expired" in texto)
    check("y dice que hay que iniciar sesion de nuevo", "iniciar sesión" in texto.lower())
    check("y nombra el comando exacto", "node_modules/.bin/claude" in texto)
    check("y avisa que la transcripcion no se perdio", "transcripción" in texto.lower())
    check("el subtype enganoso ya no va solo", texto.strip() != "subtype=success")

    check("reconoce una sesion caducada", _es_sesion_caducada(Falso.result))
    check("no confunde otro error con sesion caducada",
          not _es_sesion_caducada("API Error: 429 rate limit exceeded"))

    # Un error distinto se sigue describiendo, sin inventar el diagnostico.
    class Otro(Falso):
        result = "API Error: 529 overloaded"
    texto_otro = describir_error_sdk(Otro())
    check("otro error se muestra tal cual", "529" in texto_otro)
    check("y no sugiere iniciar sesion sin motivo",
          "node_modules/.bin/claude" not in texto_otro)


def probar_titulo_nunca_falta() -> None:
    """
    El titulo era el unico campo que finalizar_clase leia sin respaldo, y una
    linea RESULTADO_ORQUESTADOR sin el costaba la clase entera con las notas ya
    escritas. Peor: el _skill.json quedaba guardado, asi que el reintento
    fallaba en el mismo punto para siempre.
    """
    print("\n== el titulo que falta no puede costar la clase ==")
    from orquestador.skill_runner import normalizar_resultado

    r = normalizar_resultado({"fuente": "a.md"}, "TERMODINAMICA")
    check("sin titulo, usa el ramo", r["titulo"] == "TERMODINAMICA")
    check("sin conceptos, deja la lista vacia", r["conceptos_repetidos"] == [])

    check("un titulo en blanco cuenta como ausente",
          normalizar_resultado({"titulo": "   "}, "R")["titulo"] == "R")
    check("un titulo que no es texto cuenta como ausente",
          normalizar_resultado({"titulo": 5}, "R")["titulo"] == "R")
    check("un titulo bueno no se toca",
          normalizar_resultado({"titulo": "Oferta y demanda"}, "R")["titulo"]
          == "Oferta y demanda")

    check("conceptos mal formados pasan a lista vacia",
          normalizar_resultado({"titulo": "T", "conceptos_repetidos": "no es lista"},
                               "R")["conceptos_repetidos"] == [])
    check("conceptos buenos no se tocan",
          normalizar_resultado({"titulo": "T", "conceptos_repetidos": ["a", "b"]},
                               "R")["conceptos_repetidos"] == ["a", "b"])

    # Un respaldo vacio dejaria el archivo terminado en " - ".
    sin_ramo = normalizar_resultado({}, "  ")["titulo"]
    check("sin ramo tampoco queda en blanco", sin_ramo.strip() != "")
    nombre = nombres.nombre_base("2026-08-13", sin_ramo, "  ")
    check("el nombre de archivo no queda colgando de un guion",
          not nombre.rstrip().endswith("-"), nombre)


def probar_error_del_sdk_se_explica() -> None:
    """
    Un fallo HTTP de la API llega con subtype "success", asi que el mensaje que
    veia el estudiante era "La skill termino con error: success". Paso en vivo y
    costo reprocesar una clase entera.
    """
    print("\n== un error del SDK tiene que decir que paso ==")
    from orquestador.skill_runner import describir_error_sdk

    class _Falso:
        def __init__(self, **kw):
            self.subtype = "success"
            self.api_error_status = None
            self.terminal_reason = None
            self.errors = None
            self.__dict__.update(kw)

    texto = describir_error_sdk(_Falso(api_error_status=529))
    check("nombra el codigo HTTP cuando lo hay", "529" in texto, texto)
    check("y no se queda solo en el subtype engañoso", texto != "subtype=success", texto)

    texto = describir_error_sdk(_Falso(terminal_reason="max_turns"))
    check("dice por que termino la corrida", "max_turns" in texto, texto)

    texto = describir_error_sdk(_Falso(errors=["se cayo la conexion"]))
    check("incluye los errores que traiga", "se cayo la conexion" in texto, texto)

    # Un ResultMessage viejo puede no traer los campos nuevos del SDK.
    class _Viejo:
        subtype = "error_during_execution"

    check("no revienta si el SDK no trae los campos nuevos",
          "error_during_execution" in describir_error_sdk(_Viejo()))


def _falsear_query(mensajes):
    """
    Sustituto de claude_agent_sdk.query: emite mensajes ya preparados y, como el
    SDK real (0.2.128), despues de un ResultMessage con error levanta una
    excepcion que no dice nada. El falso anterior no la levantaba, y por eso las
    pruebas pasaban mientras una clase real fallaba con "error result: success"
    (01-10-2026).
    """
    from claude_agent_sdk import ResultMessage

    async def _query(*a, **kw):
        for m in mensajes:
            yield m
        ultimo = mensajes[-1] if mensajes else None
        if isinstance(ultimo, ResultMessage) and ultimo.is_error:
            raise Exception(f"Claude Code returned an error result: {ultimo.subtype}")
    return _query


def _mensajes_sdk(texto: str, is_error: bool, **extra):
    """Lo que emitiria el SDK en una corrida: la respuesta y el resultado."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    return [
        AssistantMessage(content=[TextBlock(text=texto)], model="falso"),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=is_error,
            num_turns=3,
            session_id="sesion-1",
            usage={},
            total_cost_usd=0.0,
            **extra,
        ),
    ]


def probar_resultado_mal_escrito_se_pide_de_nuevo() -> None:
    """
    La clase del 15-10-2026 se dio por fallida con las tres notas escritas: a la
    linea RESULTADO_ORQUESTADOR le faltaba una llave de cierre. Con la linea
    mal escrita se le pide a la misma sesion que la repita, sin rehacer la clase.
    """
    print("\n== un resultado con JSON roto se pide de nuevo a la misma sesion ==")
    import asyncio

    from orquestador import skill_runner

    ROTA = 'RESULTADO_ORQUESTADOR: {"titulo": "Tema", "llamados": {"avisos": []}'
    BUENA = 'RESULTADO_ORQUESTADOR: {"titulo": "Tema", "llamados": {"avisos": []}}'
    llamadas = []

    def falsa(respuestas):
        async def _query(*, prompt, options):
            llamadas.append(options)
            for m in _mensajes_sdk(respuestas[len(llamadas) - 1], False):
                yield m
        return _query

    originales = (skill_runner.query, skill_runner.registrar_uso)
    tmp = Path(tempfile.mkdtemp(prefix="pendientes_falsos_"))
    usar_dir_pendientes(tmp)
    skill_runner.registrar_uso = lambda *a, **kw: None
    try:
        skill_runner.query = falsa([ROTA, BUENA])
        r = asyncio.run(skill_runner.aplicar_skill(
            "t.txt", "TERMODINAMICA", "/tmp/vault", "slug1", "/tmp/vault/E"))
        check("la clase se salva con la linea repetida", r["titulo"] == "Tema", str(r))
        check("la segunda llamada retoma la sesion de la primera",
              len(llamadas) == 2 and llamadas[1].resume == "sesion-1", str(len(llamadas)))
        check("y no puede escribir en el vault",
              "Write" in llamadas[1].disallowed_tools and "Edit" in llamadas[1].disallowed_tools)

        # Si la skill no emitio la linea, no hay nada que repetir: falla como antes.
        llamadas.clear()
        skill_runner.query = falsa(["no reporte nada"])
        try:
            asyncio.run(skill_runner.aplicar_skill(
                "t.txt", "TERMODINAMICA", "/tmp/vault", "slug2", "/tmp/vault/E"))
            check("sin linea no se reintenta", False, "no revento")
        except ValueError:
            check("sin linea no se reintenta", len(llamadas) == 1, str(len(llamadas)))
    finally:
        skill_runner.query, skill_runner.registrar_uso = originales
        usar_dir_pendientes(None)
        shutil.rmtree(tmp, ignore_errors=True)


def probar_error_tardio_no_tira_el_trabajo() -> None:
    """
    Un 429 o un 529 al final de una corrida ya terminada no puede costar la
    clase. Paso en vivo dos veces: en una clase real (13 turnos perdidos y
    reprocesada entera) y en el ensayo, con las tres notas ya escritas.
    """
    print("\n== un error de la API al final no puede tirar el trabajo hecho ==")
    import asyncio

    from orquestador import skill_runner

    LINEA = ('RESULTADO_ORQUESTADOR: {"titulo": "Tema real", "fuente": "f.md", '
             '"aprendizaje": "a.md", "conceptos_repetidos": ["x"]}')

    avisos = []
    originales = (skill_runner.query, skill_runner.registrar_uso,
                  skill_runner.notificar_aviso)
    tmp = Path(tempfile.mkdtemp(prefix="pendientes_falsos_"))
    usar_dir_pendientes(tmp)
    skill_runner.registrar_uso = lambda *a, **kw: None
    skill_runner.notificar_aviso = lambda t, c: avisos.append((t, c))
    try:
        # 1. La skill alcanzo a reportar su trabajo y despues fallo la API.
        skill_runner.query = _falsear_query(
            _mensajes_sdk(LINEA, True, api_error_status=529)
        )
        r = asyncio.run(skill_runner.aplicar_skill(
            "t.txt", "TERMODINAMICA", "/tmp/vault", "slug1", "/tmp/vault/E"))
        check("la clase se salva pese al error de la API", r["titulo"] == "Tema real")
        check("el _skill.json queda guardado", (tmp / "slug1_skill.json").is_file())
        check("te avisa de que la API fallo", len(avisos) == 1, str(avisos))
        check("el aviso nombra el codigo HTTP", "529" in avisos[0][1], str(avisos))
        # Sin la terminal abierta, "2026-08-13_3e73c56e" no le dice nada a nadie.
        check("y nombra el ramo, no el slug interno",
              "TERMODINAMICA" in avisos[0][1] and "slug1" not in avisos[0][1], str(avisos))

        # 2. Sin error de la API no hay aviso que moleste.
        avisos.clear()
        skill_runner.query = _falsear_query(_mensajes_sdk(LINEA, False))
        asyncio.run(skill_runner.aplicar_skill(
            "t.txt", "TERMODINAMICA", "/tmp/vault", "slug2", "/tmp/vault/E"))
        check("una corrida limpia no dispara ningun aviso", avisos == [], str(avisos))

        # 3. Si ademas no hay nada aprovechable, revienta, pero explicando.
        skill_runner.query = _falsear_query(
            _mensajes_sdk("no reporte nada", True, api_error_status=429)
        )
        try:
            asyncio.run(skill_runner.aplicar_skill(
                "t.txt", "TERMODINAMICA", "/tmp/vault", "slug3", "/tmp/vault/E"))
            check("sin resultado utilizable si falla", False, "no revento")
        except ValueError as e:
            check("sin resultado utilizable si falla", True)
            check("y el error dice el codigo HTTP, no 'success'", "429" in str(e), str(e))

        # 3b. El SDK actual, despues del resultado con error, levanta una excepcion que
        # no dice nada. Tiene que llegar el motivo real (01-10-2026).
        skill_runner.query = _falsear_query(
            _mensajes_sdk("nada", True, result="Failed to authenticate: OAuth session expired"))
        try:
            asyncio.run(skill_runner.aplicar_skill(
                "t.txt", "TERMODINAMICA", "/tmp/vault", "slug3b", "/tmp/vault/E"))
            check("con la sesion vencida, el error dice que hay que iniciar sesion", False, "no revento")
        except ValueError as e:
            check("con la sesion vencida, el error dice que hay que iniciar sesion",
                  "caducó" in str(e) and "error result" not in str(e), str(e))

        # 4. La correccion tambien conserva su trabajo si la API falla al final.
        avisos.clear()
        corregida = ('RESULTADO_ORQUESTADOR: {"titulo": "Tema corregido", '
                     '"fuente": "f.md", "conceptos_repetidos": ["x"]}')
        skill_runner.query = _falsear_query(
            _mensajes_sdk(corregida, True, api_error_status=529)
        )
        previo = {"titulo": "Tema real", "session_id": "sesion-1"}
        r = asyncio.run(skill_runner.corregir_con_revision(
            previo, [{"que": "algo"}], "/tmp/vault", "slug4"))
        check("la correccion no se pierde por un error tardio",
              r["titulo"] == "Tema corregido", str(r))
        check("y queda marcada como corregida",
              r.get("corregido_tras_revision") is True, str(r))

        # 5. Si la correccion no reporta nada, se vuelve a lo anterior y avisa.
        avisos.clear()
        skill_runner.query = _falsear_query(
            _mensajes_sdk("no reporte nada", True, api_error_status=529)
        )
        r = asyncio.run(skill_runner.corregir_con_revision(
            previo, [{"que": "algo"}], "/tmp/vault", "slug5"))
        check("sin correccion confirmada se conserva lo anterior",
              r["titulo"] == "Tema real", str(r))
        check("y te avisa de que el documento va sin corregir",
              len(avisos) == 1 and "529" in avisos[0][1], str(avisos))
        check("ese aviso tambien nombra la clase, no el slug",
              "Tema real" in avisos[0][1] and "slug5" not in avisos[0][1], str(avisos))
    finally:
        (skill_runner.query, skill_runner.registrar_uso,
         skill_runner.notificar_aviso) = originales
        usar_dir_pendientes(None)
        shutil.rmtree(tmp, ignore_errors=True)


def probar_material_del_profesor() -> None:
    """
    La skill y el revisor reciben el programa del ramo y la lamina de la clase,
    si se encuentra. La lamina se elige por parecido con la transcripcion, y en
    la duda no va ninguna: una lamina de otra clase confunde mas que ayudar.
    """
    print("\n== material del profesor: programa y lamina de la clase ==")
    from orquestador import material_ramo as m, revisor, skill_runner

    laminas = {
        "clase-entalpia.pdf": "entalpia calor reaccion exotermica endotermica calorimetro capacidad",
        "clase-entropia.pdf": "entropia desorden segunda reversible irreversible carnot",
        "clase-gases.pdf": "presion volumen temperatura ideales boyle charles",
    }
    clase = "hoy vemos entalpia, el calor de una reaccion exotermica y el calorimetro con su capacidad"
    check("elige la lamina que calza con la clase", m.elegir_lamina(clase, laminas) == "clase-entalpia.pdf")
    check("en la duda no elige ninguna",
          m.elegir_lamina("hoy vemos entalpia y entropia, calor y desorden", laminas) is None)
    check("sin laminas no elige nada", m.elegir_lamina(clase, {}) is None)

    tmp = Path(tempfile.mkdtemp(prefix="material_"))
    ramo_dir = tmp / "Termodinamica"
    (ramo_dir / "PPTs").mkdir(parents=True)
    (ramo_dir / "Programa-Termodinamica.pdf").write_text("x")
    # En la raiz puede haber de todo: nunca se lee, aunque calce con la clase.
    (ramo_dir / "mi contrato entalpia calor reaccion.pdf").write_text("x")
    for nombre in laminas:
        (ramo_dir / "PPTs" / nombre).write_text("x")
    (tmp / "clase.txt").write_text(clase, encoding="utf-8")
    original = m._texto_pdf
    leidos = []
    m._texto_pdf = lambda ruta: (leidos.append(ruta.name) or
                                 ("Unidad 1: entalpia y entropia" if "Programa" in ruta.name else laminas[ruta.name]))
    usar_dir_pendientes(tmp)
    try:
        config = {"carpetas_onedrive": {"TERMODINÁMICA": str(ramo_dir)}}
        ruta = m.preparar("TERMODINÁMICA", config, str(tmp / "clase.txt"), "slug1")
        texto = ruta.read_text(encoding="utf-8") if ruta else ""
        check("sin programa declarado en config.json no entra ninguno", "Unidad 1" not in texto, texto[:200])
        check("la lamina elegida entra igual", "calorimetro" in texto and "carnot" not in texto, texto[:200])
        check("la raiz del ramo no se abre", not any("contrato" in n for n in leidos), str(leidos))
        config["programas"] = {"TERMODINÁMICA": "Programa-Termodinamica.pdf"}
        texto = m.preparar("TERMODINÁMICA", config, str(tmp / "clase.txt"), "slug1").read_text(encoding="utf-8")
        check("con el programa declarado, entra", "Unidad 1" in texto and "calorimetro" in texto, texto[:200])
        check("sin carpeta del ramo no hay material", m.preparar("OTRO", config, str(tmp / "clase.txt"), "s2") is None)
    finally:
        m._texto_pdf = original
        usar_dir_pendientes(None)
        shutil.rmtree(tmp, ignore_errors=True)

    linea = m.linea_para_prompt("/x/slug1_material.txt")
    check("la skill recibe la ruta y la regla del aporte externo",
          "/x/slug1_material.txt" in linea and "aporte externo" in linea)
    prompt = revisor._construir_prompt("t.txt", "f.md", "a.md", "R", [], "/x/slug1_material.txt")
    check("el revisor tambien lo recibe", "/x/slug1_material.txt" in prompt)
    check("y sin material el revisor no lo menciona",
          "Material" not in revisor._construir_prompt("t.txt", "f.md", "a.md", "R", []))
    check("el puntaje no descuenta nombres que se reconocen por el contexto",
          "Fourrier" in revisor.SYSTEM_PROMPT)
    check("la skill sigue recibiendo su prompt", "Ramo: R" in skill_runner._construir_prompt("t", "R", "c", linea))


def probar_laminas_en_la_hoja_de_evaluacion() -> None:
    """
    La hoja de una evaluacion se rehace cuando el profesor ya subio su material.
    Cada lamina, ayudantia o script de R se asigna a su clase (por la fecha del
    nombre, o si no por parecido con la nota) y entran solo los de las clases de
    esa evaluacion. Para un control entran ademas los controles anteriores. Solo
    carpetas de la lista blanca: nunca la raiz ni otra carpeta del ramo.
    """
    print("\n== material del profesor en la hoja de evaluacion ==")
    from orquestador import material_ramo as m

    clases = {"2026-08-06": "introduccion temperatura presion volumen gases ideales",
              "2026-10-01": "entalpia calorimetro reaccion exotermica capacidad",
              "2026-10-15": "entropia desorden segunda reversible irreversible carnot"}
    textos = {"clase-entalpia.pdf": "entalpia calorimetro reaccion endotermica capacidad",
              "clase-entropia.pdf": "entropia desorden reversible carnot",
              "Ejercicio 15-10.pdf": "entalpia calorimetro reaccion",
              "2026.08.06_intro.pdf": "entropia desorden reversible",
              "Clase-3-4.pdf": "temperatura presion volumen gases ideales",
              "nada que ver.pdf": "futbol cocina musica"}
    asignadas = m.asignar_a_clases(textos, clases)
    check("una lamina va a la clase con que mas se parece", asignadas["clase-entalpia.pdf"] == "2026-10-01")
    check("la fecha del nombre manda sobre el parecido (dd-mm sin año)",
          asignadas["Ejercicio 15-10.pdf"] == "2026-10-15", str(asignadas))
    check("y tambien con el año adelante", asignadas["2026.08.06_intro.pdf"] == "2026-08-06", str(asignadas))
    check("'3-4' no es una fecha de clase: decide el parecido", asignadas["Clase-3-4.pdf"] == "2026-08-06")
    check("lo que no se parece a ninguna clase no se asigna", asignadas["nada que ver.pdf"] is None)

    tmp = Path(tempfile.mkdtemp(prefix="laminas_"))
    ramo = tmp / "Ramo"
    for carpeta in ("PPTs", "Ayudantias", "Código R", "Evaluaciones", "Profe", "Lecturas"):
        (ramo / carpeta).mkdir(parents=True)
    contenido = {"PPTs/clase-entalpia.pdf": textos["clase-entalpia.pdf"],
                 "PPTs/copia-entalpia.pdf": textos["clase-entalpia.pdf"],
                 "PPTs/clase-intro.pdf": clases["2026-08-06"],
                 "PPTs/escaneada 01-10.pdf": "",
                 "Ayudantias/Ejercicio 15-10.pdf": textos["Ejercicio 15-10.pdf"],
                 "Código R/entropia.R": "# entropia desorden reversible carnot",
                 "Código R/notas.txt": textos["clase-entalpia.pdf"],
                 "Evaluaciones/Pauta-C1.R": "# pauta del control 1",
                 "Evaluaciones/PAUTA-SOLEMNE-1.pdf": "pauta de solemne",
                 "Profe/perfil.pdf": textos["clase-entalpia.pdf"],
                 "Lecturas/libro.pdf": textos["clase-entalpia.pdf"],
                 "suelta-entalpia.pdf": textos["clase-entalpia.pdf"]}
    for rel, texto in contenido.items():
        (ramo / rel).write_text(texto)
    leidos = []
    original = m._texto_pdf
    m._texto_pdf = lambda ruta: leidos.append(ruta.name) or contenido[str(ruta.relative_to(ramo))]
    try:
        config = {"carpetas_onedrive": {"RAMO": str(ramo)}}
        material = m.material_de_la_evaluacion("RAMO", config, clases, {"2026-10-01", "2026-10-15"})
        nombres = sorted(x["archivo"] for x in material)
        check("entran la lamina, la ayudantia y el script de las clases de la evaluacion, una vez cada uno",
              nombres == ["Ejercicio 15-10.pdf", "clase-entalpia.pdf", "entropia.R", "escaneada 01-10.pdf"],
              str(nombres))
        check("con su categoria", {x["categoria"] for x in material} == {"lámina", "ayudantía", "código R"})
        check("una escaneada que su fecha ubica entra avisando que no se pudo leer",
              [x["sin_texto"] for x in material if x["archivo"] == "escaneada 01-10.pdf"] == [True])
        check("una lamina de otra evaluacion no entra", "clase-intro.pdf" not in nombres)
        check("ni Profe, ni Lecturas, ni la raiz se abren",
              not {"perfil.pdf", "libro.pdf", "suelta-entalpia.pdf"} & set(leidos), str(leidos))
        check("en Código R solo entra el código", "notas.txt" not in nombres)
        check("sin control, los controles anteriores no entran", "Pauta-C1.R" not in nombres)
        control = m.material_de_la_evaluacion("RAMO", config, clases, {"2026-10-15"}, es_control=True)
        check("para un control entran los controles anteriores, no las solemnes",
              "Pauta-C1.R" in [x["archivo"] for x in control]
              and "PAUTA-SOLEMNE-1.pdf" not in [x["archivo"] for x in control], str(control))
    finally:
        m._texto_pdf = original
        shutil.rmtree(tmp, ignore_errors=True)


def probar_hoja_de_un_control() -> None:
    """
    Un control no esta en el calendario de evaluaciones como las solemnes: se
    anota como pendiente cuando el profesor lo anuncia. Su hoja junta las clases
    desde la ultima evaluacion hasta el dia anterior al control.
    """
    print("\n== hoja de un control ==")
    from orquestador import hoja_evaluacion as he

    evs = [{"nombre": "S1", "fecha": "2026-09-25"}, {"nombre": "S2", "fecha": "2026-11-13"}]
    fechas = ["2026-08-14", "2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"]
    check("entran las clases desde la S1 hasta el dia anterior al control",
          he.clases_del_control(evs, fechas, "2026-10-16") == {"2026-10-02", "2026-10-09"})
    datos = {"pendientes": [{"ramo": "R", "fecha": "2026-10-16", "que": "Control corto de 15 preguntas",
                             "textual": "un control cortito de 15 preguntas", "clase": "2026-10-09"},
                            {"ramo": "R", "fecha": "2026-10-16", "que": "Leer el capítulo 3"}]}
    ev = he.evaluacion_control(datos, "R", "2026-10-16")
    check("el control trae lo que dijo el profesor al anunciarlo",
          ev["fuente"] == "clase" and "15 preguntas" in ev["textual"] and ev["tipo"] == "prueba", str(ev))
    check("sin anuncio, igual se arma con su fecha",
          he.evaluacion_control({"pendientes": []}, "R", "2026-10-16")["fecha"] == "2026-10-16")
    check("el archivo lleva la fecha, para no pisar el control siguiente",
          he.nombre_archivo("TERMODINAMICA", "Control", "2026-10-16").startswith("Control 16.10.26 - "))


def probar_tu_y_otros_en_la_grabadora() -> None:
    """
    La grabadora del Mac deja tu microfono en el canal izquierdo y la llamada en
    el derecho. Se transcriben por separado y cada turno queda marcado como "Tú"
    u "Otros". Si tu microfono no grabo nada, sale como antes, sin marcas.
    """
    print("\n== Tú y Otros en lo que graba la grabadora ==")
    import subprocess as sp

    from orquestador import transcripcion as tr

    largo = " Esto se explica con calma y con un ejemplo completo para que se entienda bien en la prueba."
    otros = [(0.0, "Hoy vemos los supuestos." + largo), (4.0, "Empecemos por la normalidad."),
             (9.0, "Sí, claro."), (20.0, "Buena pregunta, la respuesta es no." + largo)]
    tu = [(4.5, "Empecemos por la normalidad."), (10.0, "Sí, claro que sí."),
          (12.0, "Profe, ¿y si la muestra es chica?")]
    texto = tr.mezclar_por_tiempo(tu, otros)
    check("cada turno lleva quien habla, en orden",
          [x[:12] for x in texto.splitlines()[1:]] == ["Otros: Hoy v", "Empecemos po", "Sí, claro.",
                                                       "Tú: Sí, clar", "Profe, ¿y si", "Otros: Buena"], texto)
    check("lo que tu micrófono repite de los parlantes no se duplica", texto.count("normalidad") == 1)
    check("una respuesta corta tuya parecida a la de ellos no se borra", "Tú: Sí, claro que sí." in texto)
    check("la primera línea explica las marcas", "Tú" in texto.splitlines()[0] and "Otros" in texto.splitlines()[0])

    # Transcriptotem deja dos "Gracias." de una racha de cinco: entran dos, no cinco.
    segmentos = [(0.0, 1.0, "Gracias.")] * 5 + [(6.0, 7.0, "Hola")]
    check("al emparejar con el texto limpio se respeta cuántas veces quedó cada frase",
          [f for _, _, f in tr.emparejar(segmentos, "Gracias.\nGracias.\nHola", 10.0)]
          == ["Gracias.", "Gracias.", "Hola"])
    check("y los tiempos se corren al inicio del trozo", tr.emparejar(segmentos, "Hola", 10.0) == [(16.0, 17.0, "Hola")])
    check("si no se capturaron tiempos, el texto entra igual",
          tr.emparejar([], "Uno\nDos", 5.0) == [(5.0, 5.0, "Uno"), (5.0, 5.0, "Dos")])

    tmp = Path(tempfile.mkdtemp())
    original = tr._transcribir_con_tiempos
    try:
        estereo = tmp / "Reunión Online 16.10.26 - prueba.m4a"
        sp.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=500:duration=6",
                "-filter_complex", "[0][1]amerge=inputs=2", "-c:a", "aac", str(estereo), "-y"],
               check=True, capture_output=True)
        mudo = tmp / "Reunión Online 16.10.26 - sin voz.m4a"
        sp.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=6",
                "-f", "lavfi", "-i", "sine=frequency=500:duration=6",
                "-filter_complex", "[0][1]amerge=inputs=2", "-c:a", "aac", str(mudo), "-y"],
               check=True, capture_output=True)
        check("un estéreo de la grabadora se reconoce", tr.es_de_dos_pistas(str(estereo)))
        check("uno del iPhone no", not tr.es_de_dos_pistas(str(tmp / "Clase 16.10.26.m4a")))

        respuestas = {"otros": [(t / 5, t / 5 + 0.5, f) for t, f in otros],
                      "tu": [(1.0, 3.0, "Profe, ¿y si la muestra es chica?")]}
        tr._transcribir_con_tiempos = lambda t, ruta, *a: respuestas[Path(ruta).stem.split()[-1]]
        texto = tr.transcribir_dos_pistas(None, str(estereo), "es-chile", "", 6, "x")
        check("con voz en tu micrófono, sale con marcas", "Tú: Profe" in texto, texto[:200])
        texto = tr.transcribir_dos_pistas(None, str(mudo), "es-chile", "", 6, "x")
        check("sin voz en tu micrófono, sale como antes, sin marcas",
              "Tú" not in texto and "Hoy vemos los supuestos." in texto, texto[:200])

        # Una entrevista donde hablas tu y la llamada calla: no se pierde la grabacion.
        respuestas = {"otros": [], "tu": [(1.0, 5.0, "Mi experiencia fue en una clínica." + largo * 3)]}
        texto = tr.transcribir_dos_pistas(None, str(estereo), "es-chile", "", 6, "x")
        check("si la llamada calla y tú hablas, la grabación no se pierde", "Tú: Mi experiencia" in texto)
        respuestas = {"otros": [], "tu": []}
        try:
            tr.transcribir_dos_pistas(None, str(estereo), "es-chile", "", 6, "x")
            check("si no hay nada en ninguno de los dos, se detiene como una clase vacía", False)
        except ValueError:
            check("si no hay nada en ninguno de los dos, se detiene como una clase vacía", True)

        check("una frase tuya que cae en silencio no entra",
              tr.frases_con_voz([(1.0, 3.0, "Hola"), (10.0, 11.0, "Gracias.")], [(0.5, 4.0)])
              == [(1.0, "Hola")])
    finally:
        tr._transcribir_con_tiempos = original
        shutil.rmtree(tmp, ignore_errors=True)


def probar_revision_fallida_no_dice_aprobado() -> None:
    """
    El revisor es la unica barrera antes de que el material entre al vault. Una
    revision que no llego a correr quedaba guardada como "aprobado", o sea con
    cara de comprobada y limpia, y el campo que guardaba la verdad no lo leia
    nadie.
    """
    print("\n== una revision que no corrio no puede decir 'aprobado' ==")
    import asyncio

    from orquestador import revisor as rv
    from orquestador import skill_runner as sr

    originales = (sr.query, rv.registrar_uso)
    tmp = Path(tempfile.mkdtemp(prefix="revision_falsa_"))
    usar_dir_pendientes(tmp)
    rv.registrar_uso = lambda *a, **kw: None
    try:
        previo = {"fuente": "f.md", "aprendizaje": "a.md"}

        # El revisor no alcanzo a emitir su linea.
        sr.query = _falsear_query(_mensajes_sdk("no alcance", True, api_error_status=529))
        r = asyncio.run(rv.revisar("t.txt", previo, "TERMODINAMICA", "/tmp/v", "s1"))
        check("el veredicto no es 'aprobado'", r["veredicto"] != "aprobado", str(r))
        check("dice explicitamente que no se reviso",
              r["veredicto"] == "no_revisado", str(r))
        check("y guarda el motivo con el codigo HTTP",
              "529" in r["revision_fallida"], str(r))
        # finalizar_clase decide corregir comparando contra "corregir": un
        # veredicto nuevo no puede disparar una correccion por accidente.
        check("no dispara correccion", r["veredicto"] != "corregir")
        check("y no aporta hallazgos falsos", r["hallazgos"] == [])

        # Una revision que si corrio sigue funcionando igual que siempre.
        buena = ('RESULTADO_REVISION: {"veredicto": "aprobado", "hallazgos": []}')
        sr.query = _falsear_query(_mensajes_sdk(buena, False))
        r = asyncio.run(rv.revisar("t.txt", previo, "TERMODINAMICA", "/tmp/v", "s2"))
        check("una revision real si puede aprobar", r["veredicto"] == "aprobado", str(r))
        check("y no queda marcada como fallida",
              not r.get("revision_fallida"), str(r))
    finally:
        (sr.query, rv.registrar_uso) = originales
        usar_dir_pendientes(None)
        shutil.rmtree(tmp, ignore_errors=True)


def probar_una_sola_comprobacion_de_vault() -> None:
    """
    La comprobacion que impide que una ruta colada en la transcripcion meta
    cualquier archivo del disco en la hoja estuvo duplicada en dos modulos
    (finalizar_clase y el antiguo regenerar.py, retirado el 14-09-2026). Queda
    una sola implementacion, y hoja_html la importa en vez de copiarla.
    """
    print("\n== la frontera del vault se comprueba en un solo lugar ==")
    import inspect

    from orquestador import finalizar_clase as fc
    from orquestador import hoja_html

    vault = Path(tempfile.mkdtemp(prefix="notas_falsas_"))
    try:
        (vault / "buena.md").write_text("contenido de la nota", encoding="utf-8")
        fuera = Path(tempfile.mkdtemp(prefix="fuera_")) / "secreto.md"
        fuera.parent.mkdir(parents=True, exist_ok=True)
        fuera.write_text("esto no puede terminar en la hoja", encoding="utf-8")

        leer = fc._leer_nota
        check("lee una nota de dentro del vault",
              leer(str(vault / "buena.md"), str(vault)) == "contenido de la nota")
        check("no lee nada de fuera del vault", leer(str(fuera), str(vault)) == "")
        check("una ruta vacia no revienta", leer(None, str(vault)) == "")
        check("un archivo que no existe da vacio",
              leer(str(vault / "no_existe.md"), str(vault)) == "")
        fuente_hoja = inspect.getsource(hoja_html)
        check("hoja_html usa esa misma comprobacion y no una copia",
              "def _leer_nota" not in fuente_hoja
              and "from .finalizar_clase import _leer_nota" in fuente_hoja)

        shutil.rmtree(fuera.parent, ignore_errors=True)
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def _gate_deja_pasar(gate, ruta, clave: str = "file_path") -> bool:
    """Corre el hook y dice si autorizo la ruta. Devolver {} es autorizar."""
    import asyncio

    respuesta = asyncio.run(gate({"tool_input": {clave: str(ruta)}}, "id-1", None))
    return respuesta == {}


def _gate_motivo(gate, ruta) -> str:
    import asyncio

    respuesta = asyncio.run(gate({"tool_input": {"file_path": str(ruta)}}, "id-1", None))
    return respuesta["hookSpecificOutput"]["permissionDecisionReason"]


def probar_gate_de_rutas() -> None:
    """
    El gate es el unico freno real de la corrida automatizada: corre con
    permission_mode "bypassPermissions", asi que sin el Write podria escribir en
    cualquier parte del disco. Nunca habia tenido pruebas.
    """
    print("\n== el gate de rutas de la corrida automatizada ==")
    from orquestador.skill_runner import construir_gate_de_rutas

    # El prefijo evita a proposito la palabra "vault": el mensaje de denegacion
    # repite la ruta denegada, y con ella dentro no se podria comprobar que el
    # mensaje no nombre un vault entre las carpetas que si autorizo.
    vault = Path(tempfile.mkdtemp(prefix="notas_falsas_"))
    try:
        gate = construir_gate_de_rutas(RAIZ, vault)

        check("deja leer la transcripcion, que vive en el proyecto",
              _gate_deja_pasar(gate, RAIZ / "orquestador" / "transcripciones_pendientes" / "x.txt"))
        check("deja escribir la nota, que va en el vault",
              _gate_deja_pasar(gate, vault / "TERMODINAMICA" / "Clase.md"))
        check("deniega cualquier otra parte del disco",
              not _gate_deja_pasar(gate, "/etc/passwd"))
        check("deniega una carpeta vecina del proyecto",
              not _gate_deja_pasar(gate, RAIZ.parent / "Otra cosa" / "a.md"))
        check("deniega el home entero",
              not _gate_deja_pasar(gate, "~/nota.md"))

        # Glob y Grep no usan "file_path" sino "path": si el gate mirara solo
        # una de las dos claves, la otra pasaria sin control.
        check("tambien gatea la clave 'path' de Glob y Grep",
              not _gate_deja_pasar(gate, "/etc", clave="path"))

        # Una ruta con .. se resuelve antes de comparar, si no el gate se
        # esquivaria escribiendo hacia arriba desde una carpeta autorizada.
        check("un .. no escapa de una raiz autorizada",
              not _gate_deja_pasar(gate, RAIZ / ".." / ".." / "etc" / "passwd"))

        # Sin ruta no hay nada que validar (ej. una herramienta sin archivo).
        import asyncio
        check("una llamada sin ruta no se bloquea",
              asyncio.run(gate({"tool_input": {}}, "id-1", None)) == {})

        # Una etapa que solo necesita el proyecto puede correr con una sola raiz.
        # Hoy ninguna lo hace (la unica era la extraccion de llamados del antiguo
        # regenerar.py, retirado el 14-09-2026), pero el gate lo sigue aceptando
        # y el mensaje de denegacion no puede nombrar un vault que no autorizo.
        solo_proyecto = construir_gate_de_rutas(RAIZ)
        check("con una sola raiz, el vault queda fuera",
              not _gate_deja_pasar(solo_proyecto, vault / "nota.md"))
        motivo = _gate_motivo(solo_proyecto, vault / "nota.md")
        check("y el mensaje no inventa un vault que no autorizo",
              "vault" not in motivo.lower(), motivo)
        check("el mensaje dice cual es la raiz permitida", str(RAIZ) in motivo, motivo)

        # Los intermedios del ensayo viven en el temp del sistema, fuera de las
        # raices. Hoy no los lee el modelo (la metadata trae la ruta real del
        # proyecto, ver finalizar_clase), pero si algun dia se movieran ahi,
        # esta prueba lo dice en vez de fallar en vivo a mitad de una clase.
        sandbox = Path(tempfile.mkdtemp(prefix="ensayo_falso_"))
        try:
            gate_ensayo = construir_gate_de_rutas(RAIZ, sandbox / "vault")
            check("un intermedio del sandbox de ensayo no esta autorizado",
                  not _gate_deja_pasar(gate_ensayo, sandbox / "pendientes" / "x.txt"))
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

        # Un gate sin raices denegaria todo y la corrida fallaria en el primer
        # Read, lejos de la llamada que se equivoco.
        try:
            construir_gate_de_rutas()
            check("un gate sin raices se rechaza al construirlo", False, "no reventó")
        except ValueError:
            check("un gate sin raices se rechaza al construirlo", True)
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def probar_puntaje_de_la_transcripcion_en_la_hoja() -> None:
    """
    El 15-10-2026 la misma clase grabada por el Mac y por el iPhone salio con
    transcripciones de calidad muy distinta, y nada en la hoja lo decia. El
    revisor la califica y la hoja lo muestra arriba, como guia.
    """
    print("\n== el puntaje de la transcripcion llega a la hoja ==")
    from orquestador import hoja_html as h, revisor

    linea = ('RESULTADO_REVISION: {"veredicto": "aprobado", "hallazgos": [], '
             '"transcripcion": {"puntaje": 64, "motivo": "deforma nombres <tecnicos>"}}')
    calidad = revisor.calidad_transcripcion(revisor._parsear_revision(linea))
    check("el puntaje sale de la revision", calidad == {"puntaje": 64, "motivo": "deforma nombres <tecnicos>"},
          str(calidad))
    for malo in ({"puntaje": 150, "motivo": "x"}, {"puntaje": 0, "motivo": "x"}, {"puntaje": "siete", "motivo": "x"}, None, "8"):
        check(f"un puntaje invalido ({malo}) no se muestra",
              revisor.calidad_transcripcion({"transcripcion": malo}) is None)

    trabajo = {"ramo": "X", "numero_clase": 1, "fecha": "2026-08-27"}
    doc = h.armar_documento(trabajo, "T", "<p>c</p>", "", calidad=calidad)
    check("la hoja muestra el puntaje como guia", "64/100" in doc and "guía" in doc)
    check("y el motivo va escapado", "&lt;tecnicos&gt;" in doc and "<tecnicos>" not in doc)
    check("un puntaje con decimales se redondea, no se pierde",
          revisor.calidad_transcripcion({"transcripcion": {"puntaje": 72.6, "motivo": ""}}) == {"puntaje": 73, "motivo": ""})
    doc = h.armar_documento(trabajo, "T", "<p>c</p>", "")
    check("sin revision dice que no se pudo medir, no inventa un puntaje",
          "no se pudo medir" in doc and "/100" not in doc)


def probar_hoja_se_sanea_y_queda_autocontenida() -> None:
    """
    La hoja se abre en el navegador y la escribe un modelo que leyo texto
    derivado de una grabacion. Lo que se prueba es que ningun resto de ese texto
    pueda ejecutar algo ni traer recursos de afuera, y que el diseño pedido
    (oscuro fijo, impresion en blanco y negro) no dependa del modelo.
    """
    print("\n== la hoja se sanea y queda autocontenida ==")
    import re

    from orquestador import hoja_html as h

    sucio = (
        '<section class="bloque" onclick="x()"><h2><span>Tema</span></h2>'
        "<script>alert(1)</script><style>body{display:none}</style>"
        '<div class="t form" style="background:url(http://a.b/c.png)"><h3>A1 &amp; B</h3>'
        '<p>ver <a href="https://a.b">esto</a><img src="x.png" onerror="x()">'
        '<iframe src="//a.b"></iframe></p>'
        '<svg viewBox="0 0 10 10" onload="x()"><defs><marker id="fl" refX="9">'
        '<path d="M0 0L9 5z"/></marker></defs>'
        '<line x1="0" y1="0" x2="9" y2="9" stroke="currentColor" marker-end="url(#fl)"/>'
        '<text x="1" y="1" fill="url(http://a.b)">P</text></svg>'
        '<math display="block"><mfrac><mi>a</mi><mi>b</mi></mfrac></math><mi>suelto</mi>'
        "</div></section>"
    )
    limpio = h.sanear_html(sucio)
    check("elimina script y su contenido", "<script" not in limpio and "alert" not in limpio)
    check("elimina style y su contenido", "<style" not in limpio and "display:none" not in limpio)
    check("elimina los atributos de evento", not re.search(r"\son\w+=", limpio), limpio)
    check("elimina el atributo style", 'style="' not in limpio)
    check("no deja nada que apunte afuera",
          "http" not in limpio and "//a.b" not in limpio and "<a " not in limpio
          and "<img" not in limpio and "<iframe" not in limpio, limpio)
    check("conserva el texto de lo que desenvuelve", "esto" in limpio and "suelto" in limpio)
    check("conserva el SVG con su viewBox bien escrito", '<svg viewBox="0 0 10 10">' in limpio)
    check("conserva la flecha local del grafico", 'marker-end="url(#fl)"' in limpio)
    check("conserva el MathML", "<mfrac><mi>a</mi><mi>b</mi></mfrac>" in limpio)
    check("no acepta MathML fuera de <math>", "<mi>suelto" not in limpio)
    check("el texto queda escapado", "A1 &amp; B" in limpio)

    trabajo = {"ramo": "TERMODINÁMICA", "numero_clase": 4, "fecha": "2026-08-27"}
    doc = h.armar_documento(trabajo, "Intervalos", limpio, "<div>tarjeta</div>", "nota.md")
    check("la cabecera sale de los datos del trabajo, no del modelo",
          "TERMODINÁMICA &middot; Clase 04 &middot; jueves 27 de agosto de 2026" in doc)
    check("siempre en modo oscuro, sin seguir la configuracion del sistema",
          "prefers-color-scheme" not in doc and "color-scheme:dark" in doc)
    check("trae la version de impresion que ahorra tinta", "@media print" in doc)
    check("ningun recurso externo en todo el documento",
          not re.search(r'(?:src|href)=["\']?(?:https?:)?//', doc)
          and "<script" not in doc and "<link" not in doc)
    check("la leyenda explica las tres marcas",
          all(m in doc for m in ("<b>CL</b>", "<b>?</b>", "<b>+</b>")))


def probar_hoja_no_niega_anuncios_que_la_nota_trae() -> None:
    """
    El .docx decia "el profesor no anuncio fechas ni evaluacion" siempre que
    los llamados llegaban en null, aunque la nota de aprendizaje si los traia.
    Paso en cuatro clases reales, entre ellas Sistemas del 01-09-2026, donde
    negaba el anuncio de la prueba del martes siguiente. La hoja no puede
    repetirlo.
    """
    print("\n== la hoja no niega anuncios que la nota si trae ==")
    from orquestador import hoja_html as h

    nota = (
        "# Aprendizaje - Duopolio\n\n## Lo que el profesor pidió\n\n### Avisos\n\n"
        '- **Prueba la próxima semana.** El profesor lo confirmó: "el próximo\n'
        '  martes".\n\n### Evaluación\n\n- **Capítulos 3 al 9, sin el 8.** Textual: "sin el 8".\n\n'
        "## La materia\n\nOtra cosa\n"
    )
    tarjeta = h.tarjeta_lo_que_pidio(nota, None)
    check("con llamados en null, la tarjeta sale de la nota",
          "Prueba la próxima semana" in tarjeta, tarjeta)
    check("une la viñeta que sigue en la linea de abajo",
          "próximo martes" in tarjeta and "próximo\n" not in tarjeta, tarjeta)
    check("conserva los subtitulos de la nota", "Avisos" in tarjeta and "Evaluación" in tarjeta)
    check("no se mete en la seccion siguiente", "Otra cosa" not in tarjeta)
    check("y nunca dice que no hubo anuncios", "no anunció" not in tarjeta)

    vacio = h.tarjeta_lo_que_pidio("", {"avisos": [], "evaluacion": []})
    check("llamados vacios y comprobados si pueden decir que no hubo anuncios",
          "no anunció" in vacio)

    desconocido = h.tarjeta_lo_que_pidio("", None)
    check("sin nota y sin llamados dice que no se pudo comprobar",
          "No se pudo comprobar" in desconocido)
    check("y no lo disfraza de 'no hubo anuncios'", "no anunció" not in desconocido)

    dudoso = h.tarjeta_lo_que_pidio(
        "", {"avisos": [{"que": "control", "textual": "el jueves", "seguro": False}], "evaluacion": []}
    )
    check("un llamado dudoso lleva la marca de verificar", 'class="f duda"' in dudoso)

    peligroso = h.tarjeta_lo_que_pidio(
        "## Lo que el profesor pidió\n\n- <script>x()</script> **ojo**\n\n## Fin", None
    )
    check("el texto de la nota se escapa",
          "<script>" not in peligroso and "&lt;script&gt;" in peligroso, peligroso)

    # Como escribe la skill en la practica: subtitulos en negrita en vez de ###,
    # puntos que declara no seguros y un callout de varias lineas. La primera
    # version juntaba los subtitulos arriba y todas las viñetas abajo, asi que
    # no se distinguia que entraba en la prueba y que era un aviso. Paso con
    # un ramo, del 12-08 y del 26-08-2026.
    import re

    real = (
        "## Lo que el profesor pidió\n\n"
        "**Entra en evaluación:**\n\n"
        '- El control es sobre esta clase. Cita textual: "va a ser sobre\n'
        '  los mecanismos".\n\n'
        "**Avisos:**\n\n"
        "- Va a subir una lectura *(no seguro respecto del contenido exacto)*.\n"
        "- Solemne 1 entre el 7 y el 25. **(seguro: no)**\n\n"
        "> [!verificar] Las fechas quedaron poco claras\n"
        "> en el audio, confírmalas.\n\n"
        "## La materia\n"
    )
    t = h.tarjeta_lo_que_pidio(real, None)
    orden = [t.find("Entra en evaluación"), t.find("El control es sobre"),
             t.find("Avisos"), t.find("Va a subir una lectura")]
    check("respeta el orden de la nota: cada subtitulo antes de sus viñetas",
          -1 not in orden and orden == sorted(orden), str(orden))
    check("un subtitulo en negrita se muestra como subtitulo",
          '<span class="clave">Entra en evaluación</span>' in t, t)
    check("una viñeta que la nota declara no segura lleva la marca de verificar",
          re.search(r'lectura <i>\(no seguro[^<]*</i>\.<span class="f duda">', t) is not None
          and re.search(r'seguro: no\)</b><span class="f duda">', t) is not None, t)
    check("una viñeta sin dudas conserva la marca de la clase",
          re.search(r'mecanismos"\.<span class="f cl">', t) is not None, t)
    check("el callout de varias lineas queda en un solo parrafo",
          t.count('<p class="mini">') == 1 and "poco claras en el audio" in t, t)

    # La advertencia en negrita puede ir en la linea siguiente de su viñeta.
    # Tomarla como subtitulo le quitaba la duda a la viñeta y colgaba la
    # siguiente de un titulo falso (clase del 12-08-2026).
    continuacion = (
        "## Lo que el profesor pidió\n\n"
        "**Qué dijo que entra en evaluación**\n\n"
        '- La nota de presentación reemplazaría la del examen. Textual: "le repito esa nota".\n'
        "  **(seguro: no, el tramo del audio está cortado)**\n"
        "- En los casos se evalúa el argumento.\n\n"
        "## Fin\n"
    )
    t2 = h.tarjeta_lo_que_pidio(continuacion, None)
    check("una advertencia en negrita pegada a su viñeta no se vuelve subtitulo",
          t2.count('<span class="clave">') == 1, t2)
    check("y esa viñeta conserva la marca de verificar",
          re.search(r'cortado\)</b><span class="f duda">', t2) is not None, t2)
    check("la viñeta siguiente sigue en la misma lista, con su marca de clase",
          t2.count("<ul>") == 1 and re.search(r'argumento\.<span class="f cl">', t2) is not None, t2)

    # Un enlace de Obsidian no funciona fuera del vault. Salia con los corchetes
    # en la tarjeta de un ramo, del 02-09-2026.
    enlace = h.tarjeta_lo_que_pidio(
        "## Lo que el profesor pidió\n\n"
        "En esta clase no hubo anuncios. Ver [[Aprendizaje - Clase anterior]] y "
        "[[Fuente - Otra clase|la fuente]].\n\n## Fin\n",
        None,
    )
    check("un enlace de Obsidian queda como el nombre de la nota, sin corchetes",
          "[[" not in enlace and "<i>Aprendizaje - Clase anterior</i>" in enlace
          and "<i>la fuente</i>" in enlace, enlace)


def probar_una_sola_frase_cuando_no_hubo_anuncios() -> None:
    """
    La skill copiaba sus propias reglas dentro de "Lo que el profesor pidio", y
    la hoja las mostraba tal cual en la tarjeta que mas se cree. Revisadas el
    14-09-2026, pasaba al menos en 12 de las 21 notas del semestre que traen la
    seccion, y en las cinco cuyos llamados vinieron vacios. En un ramo, del 02-09 decia "Es una respuesta correcta y frecuente: no se
    rellena con suposiciones", y el 19-08 "Esta seccion queda sin contenido a
    proposito". Las otras formas eran justificar lo que no se listaba y mandar a
    revisar las notas de clases anteriores.

    El arreglo esta en la skill: da la frase exacta para el caso vacio y dice que
    sus reglas no son texto para la nota. No se filtra en Python, porque un
    filtro por patrones solo reconoce las formas ya vistas y la siguiente llega
    con otras palabras.

    Esta prueba fija que la frase de la skill sea la misma que la hoja escribe
    cuando la tarjeta sale de llamados vacios, para que el estudiante lea una
    sola version venga de donde venga la tarjeta.
    """
    print("\n== una sola frase cuando el profesor no anuncio nada ==")
    from orquestador import hoja_html as h

    frase = "En esta clase el profesor no anunció fechas, entregas ni contenidos de evaluación."
    skill = RAIZ / ".claude" / "skills" / "transcripciones-a-conocimiento" / "SKILL.md"
    check("la skill da la frase exacta para el caso sin anuncios",
          frase in skill.read_text(encoding="utf-8"))
    check("la hoja escribe esa misma frase cuando los llamados vienen vacios",
          frase in h.tarjeta_lo_que_pidio("", {"avisos": [], "evaluacion": []}))

    nota = f"## Lo que el profesor pidió\n\n{frase}\n\n## La materia\n\nOtra cosa\n"
    tarjeta = h.tarjeta_lo_que_pidio(nota, None)
    check("una nota con solo esa frase la muestra entera en la tarjeta", frase in tarjeta, tarjeta)
    check("y no la toma por algo que no se pudo comprobar",
          "No se pudo comprobar" not in tarjeta, tarjeta)


def probar_hoja_pule_usos_del_modelo() -> None:
    """
    Dos usos del modelo que se veian mal en la hoja de un ramo, del 26-08-2026: un subtitulo usado como etiqueta de una frase, que dejaba la
    linea de abajo empezando con dos puntos, y la frase de que verificar metida
    dentro de la marca "?", que se imprime chica y en superindice.
    """
    print("\n== la hoja corrige usos del modelo que se ven mal ==")
    from orquestador import hoja_html as h

    etiqueta = '<p><span class="clave">Dato de delegación</span>: el área comercial autoriza descuentos.</p>'
    check("un subtitulo seguido de dos puntos pierde los dos puntos",
          h.pulir_cuerpo(etiqueta) == '<p><span class="clave">Dato de delegación</span>el área comercial autoriza descuentos.</p>',
          h.pulir_cuerpo(etiqueta))

    marca = '<p>No los desarrolla en esta clase.<span class="f duda">? revisa si los explica en la lectura</span></p>'
    pulida = h.pulir_cuerpo(marca)
    check("la frase sale de la marca y la marca queda solo con su signo",
          pulida == '<p>No los desarrolla en esta clase. Revisa si los explica en la lectura.<span class="f duda">?</span></p>',
          pulida)

    bien = ('<p>Dijo que entra.<span class="f cl">CL</span> Dudoso.<span class="f duda">?</span> '
            'Propio.<span class="f mas">+</span></p>')
    check("las marcas bien usadas no cambian", h.pulir_cuerpo(bien) == bien, h.pulir_cuerpo(bien))


def probar_hoja_degrada_sin_mentir() -> None:
    """
    Si la redaccion falla, la clase no se pierde y la hoja lo dice arriba. Y la
    etapa corre sin ninguna herramienta: recibe las notas en el prompt y
    devuelve texto, asi que no depende de que un gate este bien escrito.
    """
    print("\n== la hoja degrada sin mentir ==")
    import asyncio
    import inspect

    from orquestador import cancelacion
    from orquestador import finalizar_clase as fc
    from orquestador import hoja_html as h

    cuerpo_bueno = '<section class="bloque"><p>' + "materia " * 120 + "</p></section>"
    BUENA = f"{h.MARCA_INICIO}\n{cuerpo_bueno}\n{h.MARCA_FIN}"
    opciones_vistas = []

    def query_que_mira(mensajes):
        async def _query(*a, **kw):
            opciones_vistas.append(kw.get("options"))
            async for m in _falsear_query(mensajes)(*a, **kw):
                yield m
        return _query

    from orquestador import skill_runner as sr

    originales = (sr.query, h.registrar_uso)
    h.registrar_uso = lambda *a, **kw: None
    try:
        argumentos = ("nota de aprendizaje", "nota de fuente", "", [], "TERMODINAMICA",
                      "Titulo", "2026-08-27", "s1")

        sr.query = query_que_mira(_mensajes_sdk(BUENA, False))
        cuerpo, motivo = asyncio.run(h.redactar_cuerpo(*argumentos))
        check("una respuesta completa se acepta", cuerpo is not None and motivo is None, str(motivo))

        opciones = opciones_vistas[-1]
        check("la etapa no tiene ninguna herramienta permitida", opciones.allowed_tools == [])
        check("y prohibe explicitamente las que escriben, leen o ejecutan",
              {"Bash", "Read", "Write", "Edit"} <= set(opciones.disallowed_tools))
        check("no carga la configuracion del proyecto", opciones.setting_sources == [])

        sr.query = query_que_mira(_mensajes_sdk(BUENA, True, api_error_status=529))
        cuerpo, motivo = asyncio.run(h.redactar_cuerpo(*argumentos))
        check("un error de la API despues de entregar la hoja no la descarta",
              cuerpo is not None, str(motivo))

        sr.query = query_que_mira(_mensajes_sdk("no alcance", True, api_error_status=429))
        cuerpo, motivo = asyncio.run(h.redactar_cuerpo(*argumentos))
        check("sin la hoja no hay cuerpo", cuerpo is None)
        check("y el motivo nombra el problema de conexion", bool(motivo) and "429" in motivo, str(motivo))

        sr.query = query_que_mira(
            _mensajes_sdk(f"{h.MARCA_INICIO}\n<p>casi nada</p>\n{h.MARCA_FIN}", False)
        )
        cuerpo, motivo = asyncio.run(h.redactar_cuerpo(*argumentos))
        check("una hoja casi vacia se trata como falla",
              cuerpo is None and "vacía" in (motivo or ""), str(motivo))

        antes = len(opciones_vistas)
        cuerpo, motivo = asyncio.run(h.redactar_cuerpo("", "", "", [], "X", "T", "2026-01-01", "s2"))
        check("sin notas no gasta una llamada al modelo",
              len(opciones_vistas) == antes and cuerpo is None)
    finally:
        sr.query, h.registrar_uso = originales

    doc = h.armar_documento({"ramo": "X", "numero_clase": 1, "fecha": "2026-08-27"},
                            "T", None, "", "", "se corto", "s9")
    check("la hoja degradada avisa que falta la materia", "Falta la materia condensada" in doc)
    check("y aclara que eso no significa que la clase no tenga materia",
          "no significa que la clase no tenga materia" in doc)

    # El envoltorio de finalizar_clase degrada ante cualquier falla, salvo el
    # aborto pedido desde la barra de menu, que tiene que seguir subiendo.
    avisos = []
    originales_fc = (fc.redactar_cuerpo, fc.notificar_aviso)
    fc.notificar_aviso = lambda t, c: avisos.append((t, c))
    try:
        async def revienta(*a, **kw):
            raise RuntimeError("fallo inventado")
        fc.redactar_cuerpo = revienta
        cuerpo, motivo = asyncio.run(fc._redactar_hoja("a", "f", "", [], "R", "T", "2026-01-01", "s"))
        check("una falla de la redaccion no tira la clase",
              cuerpo is None and "RuntimeError" in (motivo or ""), str(motivo))
        check("y avisa al estudiante", len(avisos) == 1, str(avisos))

        async def aborta(*a, **kw):
            raise cancelacion.Abortado()
        fc.redactar_cuerpo = aborta
        try:
            asyncio.run(fc._redactar_hoja("a", "f", "", [], "R", "T", "2026-01-01", "s"))
            check("un aborto pedido no se traga", False, "no subio")
        except cancelacion.Abortado:
            check("un aborto pedido no se traga", True)
    finally:
        fc.redactar_cuerpo, fc.notificar_aviso = originales_fc

    fuente = inspect.getsource(fc.procesar_clase_reconocida)
    check("la redaccion va antes de la seccion critica, que no se puede abortar",
          0 <= fuente.find("_redactar_hoja(") < fuente.find("seccion_critica()"))
    check("el pipeline ya no arma el .docx ni agrega a Anki",
          "generar_docx" not in fuente and "anki_connect" not in fuente
          and "extraer_preguntas_respuestas" not in fuente)


def probar_reunion_audio_txt_y_docx_con_el_mismo_nombre() -> None:
    """
    El audio, el .txt y el .docx de una reunion se llaman igual, empezando por
    la fecha (pedido del estudiante, 09-10-2026). La nota de Obsidian conserva
    su nombre, porque renombrar notas rompe enlaces.
    """
    print("\n== una reunion deja audio, .txt y .docx con el mismo nombre ==")
    import asyncio
    import json

    from orquestador import ensayo, reuniones as rn

    base = Path(tempfile.mkdtemp(prefix="reunion_"))
    pendientes = base / "pendientes"
    pendientes.mkdir()
    audio = base / "grabacion.m4a"
    audio.write_bytes(b"a")
    texto = pendientes / "s1.txt"
    texto.write_text("lo que se dijo", encoding="utf-8")
    (pendientes / "s1_reunion.json").write_text(json.dumps({
        "titulo": "Café con un profe", "tipo": "conversacion", "calidad_audio": "buena",
        "resumen": "Hablamos.", "decisiones": [], "acciones": [], "preguntas_abiertas": [],
        "entrevista": None, "investigar": []}, ensure_ascii=False), encoding="utf-8")
    config = {"rutas": {"output": str(base / "Output"), "procesados": str(base / "Procesados"),
                        "vault_obsidian": str(base / "vault")}, ensayo.CLAVE: True}
    original = rn.notificar_reunion
    rn.notificar_reunion = lambda *a, **k: None
    usar_dir_pendientes(pendientes)
    try:
        docx = asyncio.run(rn.procesar_reunion(
            {"slug": "s1", "fecha": "2026-10-07", "ramo": rn.RAMO,
             "archivos_originales": [str(audio)], "archivo_texto": str(texto)}, config))
        esperado = "07.10.26 - Café con un profe - Reunión"
        archivados = sorted(p.name for p in (base / "Procesados" / rn.RAMO).iterdir())
        check("el audio y el .txt quedan con el nombre nuevo",
              archivados == [f"{esperado}.m4a", f"{esperado}.txt"], str(archivados))
        check("y el .docx tambien", docx.name == f"{esperado}.docx", docx.name)
        check("la nota de Obsidian conserva su formato",
              [p.name for p in (base / "vault").rglob("*.md")] == ["Reunión - 2026-10-07 - Café con un profe.md"],
              str(list((base / "vault").rglob("*.md"))))
    finally:
        rn.notificar_reunion = original
        usar_dir_pendientes(None)
        shutil.rmtree(base, ignore_errors=True)


def probar_reunion_en_un_solo_lugar() -> None:
    """
    Fuera de un ensayo, una reunion deja la grabacion, la transcripcion y el .docx
    juntos en la carpeta de reuniones, con el mismo nombre. La grabacion se mueve y la
    transcripcion se copia. Carpetas temporales y el analisis guardado de antemano.
    """
    print("\n== cada reunion en un solo lugar ==")
    import asyncio
    import json

    from orquestador import reuniones as rn

    base = Path(tempfile.mkdtemp(prefix="reunion_lugar_"))
    pendientes = base / "pendientes"
    pendientes.mkdir()
    audio = base / "Input" / "grabacion.m4a"
    audio.parent.mkdir()
    audio.write_bytes(b"a")
    texto = pendientes / "s1.txt"
    texto.write_text("lo que se dijo", encoding="utf-8")
    (pendientes / "s1_reunion.json").write_text(json.dumps({
        "titulo": "Café con un profe", "tipo": "conversacion", "calidad_audio": "buena",
        "resumen": "Hablamos.", "decisiones": [], "acciones": [], "preguntas_abiertas": [],
        "entrevista": None, "investigar": []}, ensure_ascii=False), encoding="utf-8")
    config = {"rutas": {"output": str(base / "Output"), "procesados": str(base / "Procesados"),
                        "vault_obsidian": str(base / "vault")}}
    originales = (rn.notificar_reunion, rn.CARPETA_DOCX, rn.evaluaciones.RUTA)
    rn.notificar_reunion = lambda *a, **k: None
    rn.CARPETA_DOCX, rn.evaluaciones.RUTA = base / "Reuniones post Claude", base / "evaluaciones.json"
    rn.evaluaciones.guardar({"ramos": {}, "pendientes": []})
    usar_dir_pendientes(pendientes)
    try:
        docx = asyncio.run(rn.procesar_reunion(
            {"slug": "s1", "fecha": "2026-10-07", "ramo": rn.RAMO,
             "archivos_originales": [str(audio)], "archivo_texto": str(texto)}, config))
        esperado = "07.10.26 - Café con un profe - Reunión"
        juntos = sorted(p.name for p in docx.parent.iterdir())
        check("grabación, transcripción y .docx quedan juntos, con el mismo nombre",
              juntos == [f"{esperado}.docx", f"{esperado}.m4a", f"{esperado}.txt"], str(juntos))
        sala = sorted(p.name for p in (base / "Procesados" / rn.RAMO).iterdir())
        check("la grabación no queda dos veces: en la sala de trabajo solo la transcripción",
              sala == [f"{esperado}.txt"], str(sala))
    finally:
        rn.notificar_reunion, rn.CARPETA_DOCX, rn.evaluaciones.RUTA = originales
        usar_dir_pendientes(None)
        shutil.rmtree(base, ignore_errors=True)


def probar_renumerar_corrige_la_hoja() -> None:
    """
    En los ramos con numeracion por orden, una clase mas antigua que llega tarde
    corre el numero de las siguientes. Desde el 09-10-2026 el
    numero ya no va en el nombre del archivo: solo en la cabecera de la hoja.
    """
    print("\n== renumerar corrige tambien la cabecera de la hoja ==")
    with tempfile.TemporaryDirectory() as tmp:
        procesados, output = Path(tmp) / "Procesados", Path(tmp) / "Output"
        (procesados / "RAMO").mkdir(parents=True)
        (output / "RAMO").mkdir(parents=True)
        (procesados / "RAMO" / "03.08.26 - Primera - Ramo.m4a").write_bytes(b"a")
        (procesados / "RAMO" / "10.08.26 - Segunda - Ramo.m4a").write_bytes(b"a")
        hoja = output / "RAMO" / "10.08.26 - Segunda - Ramo.html"
        hoja.write_text(
            '<p class="ramo">RAMO &middot; Clase 01 &middot; lunes</p><p>lo vimos en la Clase 01</p>',
            encoding="utf-8",
        )
        nombres.renumerar_clases_ramo(procesados, output, "RAMO")
        nueva = hoja
        check("la hoja conserva su nombre", nueva.is_file())
        texto = nueva.read_text(encoding="utf-8") if nueva.is_file() else ""
        check("y su cabecera dice el numero nuevo", "&middot; Clase 02 &middot;" in texto, texto)
        check("sin tocar la materia que menciona el numero viejo",
              "lo vimos en la Clase 01" in texto, texto)


def probar_clase_completa_termina_en_notas_y_hoja() -> None:
    """
    Desde el 14-09-2026 una clase termina en dos cosas y nada mas: las notas en
    Obsidian y la hoja HTML. Anki y el .docx salieron del pipeline. Esto corre
    procesar_clase_reconocida entera en un sandbox de ensayo, con las tres
    llamadas al modelo reemplazadas por respuestas fijas, para comprobar sin
    gastar cuota que la cadena completa sigue cerrando sin ellos.
    """
    print("\n== una clase completa termina en notas y hoja, sin Anki ni .docx ==")
    import asyncio
    import importlib.util

    from orquestador import estado_vivo as ev
    from orquestador import finalizar_clase as fc

    for retirado in ("anki_connect", "dialogo_anki", "extraer_flashcards", "docx_generator",
                     "formulas", "mapa_visual", "regenerar", "procesar_aparte"):
        check(f"el modulo {retirado} ya no existe",
              importlib.util.find_spec(f"orquestador.{retirado}") is None)
    check("la barra de menu cuenta cuatro pasos",
          len(ev.PASOS) == 4 and not hasattr(ev, "PASO_ANKI"), str(ev.PASOS))

    base = Path(tempfile.mkdtemp(prefix="clase_completa_"))
    ramo = "RAMO DE PRUEBA"
    carpeta = base / "vault" / ramo
    carpeta.mkdir(parents=True)
    audio = base / "Input" / "clase.m4a"
    audio.parent.mkdir()
    audio.write_bytes(b"audio de mentira")
    pendientes = base / "pendientes"
    pendientes.mkdir()
    texto = pendientes / "s1.txt"
    texto.write_text("transcripcion de mentira", encoding="utf-8")

    config = {
        "rutas": {"vault_obsidian": str(base / "vault"), "output": str(base / "Output"),
                  "procesados": str(base / "Procesados")},
        "carpetas_ramo": {},
        ensayo.CLAVE: True,
    }
    trabajo = {"slug": "s1", "fecha": "2026-09-09", "ramo": ramo, "numero_clase": 6,
               "archivo_texto": str(texto), "archivos_originales": [str(audio)],
               "reconocido": True}

    async def skill_falsa(*a, **kw):
        aprendizaje = carpeta / "Aprendizaje - Clase de prueba.md"
        aprendizaje.write_text(
            "# Aprendizaje\n\n## Lo que el profesor pidió\n\n"
            '- Hay control el jueves. Textual: "el jueves hay control".\n\n'
            "## La materia\n\nAlgo que estudiar.\n", encoding="utf-8")
        fuente = carpeta / "Fuente - Clase de prueba.md"
        fuente.write_text("# Fuente\n\nLo que se dijo.\n", encoding="utf-8")
        return {"titulo": "Una clase de prueba", "fuente": str(fuente),
                "aprendizaje": str(aprendizaje), "contexto": "",
                "conceptos_repetidos": [], "llamados": None}

    async def revision_falsa(*a, **kw):
        return {"veredicto": "aprobado", "hallazgos": []}

    cuerpo = ('<section class="bloque"><h2><span>Tema</span></h2><div class="rejilla">'
              '<div class="t con"><h3><span class="n">A1</span>Idea</h3><p>'
              + "palabra " * 120 + '<span class="f cl">CL</span></p></div></div></section>')

    async def hoja_falsa(*a, **kw):
        return cuerpo, None

    pasos, avisos = [], []
    originales = (fc.aplicar_skill, fc.revisar, fc.redactar_cuerpo, fc.notificar_progreso,
                  fc.notificar_aviso, fc.notificar_exito, ev.fijar_clase)
    fc.aplicar_skill, fc.revisar, fc.redactar_cuerpo = skill_falsa, revision_falsa, hoja_falsa
    fc.notificar_progreso = lambda paso, detalle="": pasos.append((paso, detalle))
    fc.notificar_aviso = lambda titulo, mensaje: avisos.append((titulo, mensaje))
    fc.notificar_exito = lambda *a, **kw: None
    ev.fijar_clase = lambda nombre: None
    usar_dir_pendientes(pendientes)
    try:
        ruta = asyncio.run(fc.procesar_clase_reconocida(trabajo, config))
        check("deja la hoja en Output, con el nombre de la clase",
              ruta.is_file() and ruta.name == f"09.09.26 - Una clase de prueba - {nombres.ramo_legible(ramo)}.html",
              str(ruta))
        hoja = ruta.read_text(encoding="utf-8") if ruta.is_file() else ""
        check("la hoja trae la materia y lo que pidio el profesor",
              "palabra palabra" in hoja and "Hay control el jueves" in hoja)
        check("las notas quedan en la carpeta del ramo en el vault",
              (carpeta / "Aprendizaje - Clase de prueba.md").is_file())
        archivados = list((base / "Procesados" / ramo).glob("09.09.26 - *.m4a"))
        check("el audio se archiva (en ensayo se copia)",
              len(archivados) == 1 and audio.is_file(), str(archivados))
        check("y su .txt queda al lado, con el mismo nombre que el audio y la hoja",
              bool(archivados) and archivados[0].with_suffix(".txt").is_file()
              and archivados[0].stem == ruta.stem, str(list((base / "Procesados" / ramo).iterdir())))
        check("no se crea ningun .docx", not list(base.rglob("*.docx")))
        check("ningun paso ni aviso habla de Anki o de flashcards",
              not any(p in str(x).lower() for x in pasos + avisos for p in ("anki", "flashcard")),
              str(pasos + avisos))
        check("y no hubo avisos", avisos == [], str(avisos))
    finally:
        (fc.aplicar_skill, fc.revisar, fc.redactar_cuerpo, fc.notificar_progreso,
         fc.notificar_aviso, fc.notificar_exito, ev.fijar_clase) = originales
        usar_dir_pendientes(None)
        shutil.rmtree(base, ignore_errors=True)


def probar_clase_en_un_solo_lugar() -> None:
    """
    Una clase termina en la carpeta de su ramo en OneDrive, en Transcripciones/, con el audio,
    la transcripcion y la hoja juntos y con el mismo nombre. El audio se mueve y lo
    demas se copia: Procesados y Output quedan como sala de trabajo. Nunca pisa, no
    toca la carpeta real en un ensayo, y detener la corrida lo deshace. La carpeta
    del ramo sale de carpetas_onedrive en config.json. Todo en carpetas temporales.
    """
    print("\n== cada clase en un solo lugar ==")
    from orquestador import archivado as ar
    from orquestador.bitacora import Bitacora

    base = Path(tempfile.mkdtemp(prefix="clase_lugar_"))
    ramo = "RAMO DE PRUEBA"
    nombre = "09.09.26 - Una clase de prueba - Ramo de Prueba"
    procesados, salida = base / "Procesados" / ramo, base / "Output" / ramo
    procesados.mkdir(parents=True)
    salida.mkdir(parents=True)
    carpeta_ramo = base / "OneDrive" / "Ramo de prueba"
    carpeta_ramo.mkdir(parents=True)
    config = {"rutas": {"procesados": str(base / "Procesados"), "output": str(base / "Output")},
              "carpetas_onedrive": {ramo: str(carpeta_ramo)}}

    def clase():
        audio, txt, hoja = procesados / f"{nombre}.m4a", procesados / f"{nombre}.txt", salida / f"{nombre}.html"
        audio.write_bytes(b"audio comprimido")
        txt.write_text("transcripcion", encoding="utf-8")
        hoja.write_text("<html>hoja</html>", encoding="utf-8")
        return [audio, txt], hoja

    try:
        archivados, hoja = clase()
        clases = ar.publicar_en_onedrive(archivados, hoja, ramo, config)
        check("audio, transcripción y hoja quedan juntos en Transcripciones, con el mismo nombre",
              clases == carpeta_ramo / "Transcripciones"
              and sorted(p.name for p in clases.iterdir()) == [f"{nombre}.html", f"{nombre}.m4a", f"{nombre}.txt"],
              str(list(clases.iterdir())) if clases else "None")
        check("el audio se mueve y no queda dos veces, la transcripción y la hoja se copian",
              not archivados[0].exists() and archivados[1].is_file() and hoja.is_file())

        archivados, hoja = clase()
        ar.publicar_en_onedrive(archivados, hoja, ramo, config)
        check("una clase con el mismo nombre no pisa la que ya estaba",
              (clases / f"{nombre} (2).m4a").is_file()
              and (clases / f"{nombre}.m4a").read_bytes() == b"audio comprimido")

        archivados, hoja = clase()
        check("un ramo sin carpeta declarada no publica ni mueve nada",
              ar.publicar_en_onedrive(archivados, hoja, "OTRO RAMO", config) is None and archivados[0].is_file())
        check("en un ensayo no toca OneDrive",
              ar.publicar_en_onedrive(archivados, hoja, ramo, dict(config, **{ensayo.CLAVE: True})) is None
              and archivados[0].is_file())

        nuevo = dict(config, carpetas_onedrive={ramo: str(base / "OneDrive" / "Ramo nuevo")})
        (base / "OneDrive" / "Ramo nuevo").mkdir()
        b = Bitacora(base / "bitacora.json")
        publicada = ar.publicar_en_onedrive(archivados, hoja, ramo, nuevo, b)
        b.deshacer()
        check("detener la corrida devuelve el audio y saca las copias",
              publicada is not None and archivados[0].is_file() and not publicada.exists(), str(publicada))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def probar_alcance_de_evaluaciones() -> None:
    """
    Cada clase pertenece a la proxima evaluacion de su ramo, y la hoja por
    evaluacion junta solo esas clases. Se prueba con fechas de mentira que
    imitan a Marketing (examen sin fecha confirmada) y a un ramo con todas sus
    fechas.
    """
    print("\n== alcance de las evaluaciones ==")
    from orquestador import evaluaciones as ev

    mkt = [{"nombre": "S1", "fecha": "2026-09-21"}, {"nombre": "S2", "fecha": "2026-11-09"},
           {"nombre": "Examen", "fecha": None, "acumula": True}]
    fechas = ["2026-08-03", "2026-08-31", "2026-09-21", "2026-09-28", "2026-10-05", "2026-11-16"]

    def nombre(evaluaciones, fecha):
        e = ev.evaluacion_de(evaluaciones, fecha)
        return e and e["nombre"]

    check("una clase antes de la S1 va a la S1", nombre(mkt, "2026-08-31") == "S1")
    check("una clase entre S1 y S2 va a la S2", nombre(mkt, "2026-09-28") == "S2")
    check("una clase el mismo dia de la S1 ya va a la S2", nombre(mkt, "2026-09-21") == "S2")
    check("despues de la S2, al examen aunque no tenga fecha", nombre(mkt, "2026-11-16") == "Examen")
    check("la S1 junta las clases hasta su fecha",
          ev.clases_de(mkt, "S1", fechas) == ["2026-08-03", "2026-08-31"])
    check("la S2 no repite la materia de la S1",
          ev.clases_de(mkt, "S2", fechas) == ["2026-09-21", "2026-09-28", "2026-10-05"])
    check("el examen junta todo el semestre", ev.clases_de(mkt, "Examen", fechas) == fechas)

    acumulada = [dict(e) for e in mkt]
    acumulada[1]["acumula"] = True
    check("una S2 anunciada como acumulativa trae tambien la materia de la S1",
          ev.clases_de(acumulada, "S2", fechas) == fechas[:5])

    completo = [{"nombre": "S1", "fecha": "2026-09-08"}, {"nombre": "S2", "fecha": "2026-11-17"},
                {"nombre": "Examen", "fecha": "2026-12-04", "acumula": True}]
    check("despues del examen la clase no pertenece a ninguna",
          ev.evaluacion_de(completo, "2026-12-05") is None)
    sin_fecha_s2 = [dict(e) for e in completo]
    sin_fecha_s2[1]["fecha"] = None
    check("una S2 sin fecha confirmada se queda con las clases hasta tenerla",
          nombre(sin_fecha_s2, "2026-11-26") == "S2")


def probar_anuncios_de_evaluacion() -> None:
    """
    Los anuncios de clase actualizan las fechas del semestre, y el estudiante
    las cree sin revisarlas. Un anuncio solo cambia algo si viene seguro y su
    cita esta en la transcripcion. Todo lo demas queda como posible, y se avisa.
    """
    print("\n== anuncios de evaluacion ==")
    import copy

    from orquestador import evaluaciones as ev
    from orquestador import finalizar_clase as fc

    transcripcion = ("bueno chicos les recuerdo las fechas, sobre el 2, 16 de noviembre y "
                     "ojo que entra todo lo de la primera solemne tambien. el examen es un "
                     "trabajo, la primera parte del trabajo corregida")
    base = {"ramos": {"RAMO": [
        {"nombre": "S1", "fecha": "2026-09-21", "tipo": "desconocido", "acumula": False,
         "fuente": "calendario"},
        {"nombre": "S2", "fecha": "2026-11-09", "tipo": "desconocido", "acumula": False,
         "fuente": "calendario"},
        {"nombre": "Examen", "fecha": None, "tipo": "desconocido", "acumula": True,
         "fuente": "calendario"}]}, "clases_perdidas": []}
    fecha_s2 = {"evaluacion": "S2", "fecha": "2026-11-16", "tipo": None, "acumula": None,
                "textual": "Sobre el 2, 16 de noviembre", "seguro": True}

    check("una cita unida con ... y con palabras entre corchetes se reconoce",
          ev.cita_respaldada("les recuerdo las fechas... ojo que entra [todo] lo de la primera",
                             transcripcion))
    check("una cita inventada no se reconoce",
          not ev.cita_respaldada("la solemne dos se posterga una semana", transcripcion))
    check("una cita de menos de tres palabras no alcanza", not ev.cita_respaldada("el 2", transcripcion))

    datos = copy.deepcopy(base)
    cambios = ev.aplicar_anuncios(datos, "RAMO", [fecha_s2], "2026-09-29", transcripcion)
    s2 = datos["ramos"]["RAMO"][1]
    check("un anuncio seguro y citado cambia la fecha",
          s2["fecha"] == "2026-11-16" and s2["fuente"] == "clase" and s2["clase"] == "2026-09-29",
          str(s2))
    check("y lo dice en palabras", cambios == ["S2: fecha 09-11 a 16-11 (anuncio del 29-09)"],
          str(cambios))
    check("repetir el mismo anuncio no cambia nada",
          ev.aplicar_anuncios(datos, "RAMO", [fecha_s2], "2026-09-29", transcripcion) == [])
    check("un anuncio de una clase anterior no pisa a uno mas nuevo",
          ev.aplicar_anuncios(datos, "RAMO", [dict(fecha_s2, fecha="2026-11-23")],
                              "2026-09-22", transcripcion) == [] and s2["fecha"] == "2026-11-16")

    datos = copy.deepcopy(base)
    cambios = ev.aplicar_anuncios(datos, "RAMO", [
        {"evaluacion": "S2", "acumula": True, "seguro": True,
         "textual": "ojo que entra todo lo de la primera solemne tambien"},
        {"evaluacion": "Examen", "tipo": "trabajo", "seguro": True,
         "textual": "el examen es un trabajo"}], "2026-09-29", transcripcion)
    check("tambien aprende si acumula y si es trabajo",
          datos["ramos"]["RAMO"][1]["acumula"] is True
          and datos["ramos"]["RAMO"][2]["tipo"] == "trabajo" and len(cambios) == 2, str(cambios))

    datos = copy.deepcopy(base)
    inventado = dict(fecha_s2, textual="la solemne dos se posterga al 16")
    cambios = ev.aplicar_anuncios(datos, "RAMO", [inventado], "2026-09-29", transcripcion)
    s2 = datos["ramos"]["RAMO"][1]
    check("con una cita que no esta en el audio, la fecha no cambia",
          s2["fecha"] == "2026-11-09" and s2["fuente"] == "calendario", str(s2))
    check("queda como posible y se avisa",
          s2.get("fecha_posible") == "2026-11-16" and "sin confirmar" in cambios[0], str(cambios))

    datos = copy.deepcopy(base)
    ev.aplicar_anuncios(datos, "RAMO", [dict(fecha_s2, seguro=False)], "2026-09-29", transcripcion)
    check("un anuncio dudoso, aunque este citado, tampoco pisa la fecha",
          datos["ramos"]["RAMO"][1]["fecha"] == "2026-11-09")

    datos = copy.deepcopy(base)
    check("un control, una fecha mal escrita o un ramo sin evaluaciones no tocan nada",
          ev.aplicar_anuncios(datos, "RAMO", [dict(fecha_s2, evaluacion="Control 3")],
                              "2026-09-29", transcripcion) == []
          and ev.aplicar_anuncios(datos, "RAMO", [dict(fecha_s2, fecha="16 de noviembre")],
                                  "2026-09-29", transcripcion) == []
          and ev.aplicar_anuncios(datos, "OTRO", [fecha_s2], "2026-09-29", transcripcion) == []
          and datos == base)

    carpeta = Path(tempfile.mkdtemp(prefix="anuncios_"))
    archivo, texto = carpeta / "evaluaciones.json", carpeta / "clase.txt"
    texto.write_text(transcripcion, encoding="utf-8")
    trabajo = {"ramo": "RAMO", "fecha": "2026-09-29", "archivo_texto": str(texto)}
    resultado = {"evaluaciones": [fecha_s2]}
    avisos = []
    originales = (ev.RUTA, fc.notificar_aviso)
    ev.RUTA = archivo
    fc.notificar_aviso = lambda titulo, mensaje: avisos.append((titulo, mensaje))
    try:
        ev.guardar(copy.deepcopy(base))
        fc.aplicar_anuncios_de_clase(resultado, trabajo, {ensayo.CLAVE: True})
        check("en ensayo avisa pero no guarda",
              ev.cargar() == base and "En ensayo no se guardó" in avisos[-1][1], str(avisos))
        fc.aplicar_anuncios_de_clase(resultado, trabajo, {})
        check("en una clase real queda guardado y avisa",
              ev.cargar()["ramos"]["RAMO"][1]["fecha"] == "2026-11-16"
              and avisos[-1][0] == "Cambió una evaluación de RAMO", str(avisos))
        avisos.clear()
        fc.aplicar_anuncios_de_clase(resultado, dict(trabajo, archivo_texto=str(carpeta / "no")), {})
        check("si falla, avisa y la clase sigue",
              len(avisos) == 1 and avisos[0][0].startswith("No se revisaron"), str(avisos))
        archivo.unlink()
        avisos.clear()
        fc.aplicar_anuncios_de_clase(resultado, trabajo, {})
        check("sin archivo de evaluaciones no hace nada ni se cae", avisos == [], str(avisos))
    finally:
        ev.RUTA, fc.notificar_aviso = originales
        shutil.rmtree(carpeta, ignore_errors=True)


def _pdf_con_texto(texto: str) -> bytes:
    """Un PDF minimo y valido, con una linea de texto (o ninguna, si texto es
    vacio, que es como se ve un PDF escaneado para quien extrae texto)."""
    flujo = (f"BT /F1 12 Tf 10 50 Td ({texto}) Tj ET" if texto else "").encode()
    objetos = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
               b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R"
               b"/Resources<</Font<</F1 5 0 R>>>>>>",
               b"<</Length %d>>stream\n" % len(flujo) + flujo + b"\nendstream",
               b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    salida, posiciones = b"%PDF-1.4\n", []
    for i, obj in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    salida += b"".join(b"%010d 00000 n \n" % p for p in posiciones)
    return salida + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)


def probar_material_de_onedrive() -> None:
    """
    La hoja por evaluacion lee material de la carpeta del ramo en OneDrive, y
    esas carpetas tienen cosas que nunca deben abrirse. Se arma un arbol falso
    con los mismos casos que hay en las carpetas reales: un perfil de LinkedIn,
    un trabajo con nombre propio en .docx, libros, lo que dejo el propio agente,
    controles, y un contrato en la raiz del semestre.
    """
    print("\n== material de OneDrive, con lista blanca ==")

    from orquestador import onedrive as od

    semestre = Path(tempfile.mkdtemp(prefix="semestre_"))
    ramo = semestre / "Ramo X"
    archivos = {
        "Solemne-2-RamoX_pauta_v1.pdf": "Pregunta sobre elasticidad",
        "Evaluaciones pasadas/SOLEMNE-N2_RAMOX-PAUTA.pdf": "Pregunta sobre oligopolio",
        "Evaluaciones pasadas/Solemne-2-RamoX_pauta_v1 copia.pdf": "Pregunta sobre elasticidad",
        "Solemne-2-escaneada.pdf": "",
        "PAUTA-SOLEMNE-N1_RAMOX.pdf": "Pregunta de la S1",
        "Examen-2025-pauta.pdf": "Pregunta de examen",
        "Programa_RamoX.pdf": "Unidad 1 y unidad 2",
        "Instrucciones-Caso.pdf": "Entregar un informe de diez paginas",
        "Control-2-RamoX-Pauta.pdf": "Pregunta de control",
        "Nombre Apellido _ LinkedIn.pdf": "perfil de una persona",
        "Transcripciones/Solemne 2 - RamoX.pdf": "hoja del propio agente",
        "Libros/Quimica - Autor.pdf": "un libro entero",
        "Solemne-2-borrador.docx": "",
        "Trabajo Nombre Apellido - Caso.docx": "",
    }
    for nombre, texto in archivos.items():
        ruta = ramo / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(_pdf_con_texto(texto) if nombre.endswith(".pdf") else b"docx")
    (semestre / "contrato_12.345.678-9.pdf").write_bytes(_pdf_con_texto("RUT 12.345.678-9"))

    (ramo / "programa de becas - postulacion.pdf").write_bytes(_pdf_con_texto("datos personales"))

    def nombres(evaluacion, tipo, programa="Programa_RamoX.pdf"):
        return sorted(r.name for _, r in od.elegir_archivos(ramo, evaluacion, tipo, programa))

    check("sin programa declarado no entra ningun programa",
          not [n for n in nombres("S2", "prueba", None) if "rograma" in n], str(nombres("S2", "prueba", None)))

    check("para la S2 entran sus pautas pasadas y el programa, sin repetir la copia",
          nombres("S2", "prueba") == ["Programa_RamoX.pdf", "SOLEMNE-N2_RAMOX-PAUTA.pdf",
                                      "Solemne-2-RamoX_pauta_v1.pdf", "Solemne-2-escaneada.pdf"],
          str(nombres("S2", "prueba")))
    check("para la S1, solo las de la S1",
          nombres("S1", "prueba") == ["PAUTA-SOLEMNE-N1_RAMOX.pdf", "Programa_RamoX.pdf"])
    check("para el examen, los examenes pasados",
          nombres("Examen", "prueba") == ["Examen-2025-pauta.pdf", "Programa_RamoX.pdf"])
    check("si la evaluacion es un trabajo, el enunciado y no las pruebas pasadas",
          nombres("S2", "trabajo") == ["Instrucciones-Caso.pdf", "Programa_RamoX.pdf"],
          str(nombres("S2", "trabajo")))
    prohibidos = {"programa de becas - postulacion.pdf", "Nombre Apellido _ LinkedIn.pdf", "Solemne 2 - RamoX.pdf", "Quimica - Autor.pdf",
                  "Control-2-RamoX-Pauta.pdf", "Solemne-2-borrador.docx",
                  "Trabajo Nombre Apellido - Caso.docx", "contrato_12.345.678-9.pdf"}
    todos = {n for e in ("S1", "S2", "Examen") for t in ("prueba", "trabajo") for n in nombres(e, t)}
    check("nunca abre LinkedIn, .docx, libros, controles, lo del agente ni la raiz",
          not todos & prohibidos, str(todos & prohibidos))

    config = {"carpetas_onedrive": {"RAMO X": str(ramo)}}
    try:
        material = {m["archivo"]: m for m in od.leer_material("RAMO X", "S2", "prueba", None, config)}
        check("lee el texto de cada PDF",
              material.get("SOLEMNE-N2_RAMOX-PAUTA.pdf", {}).get("texto") == "Pregunta sobre oligopolio",
              str(material))
        check("y marca el escaneado como sin texto",
              material.get("Solemne-2-escaneada.pdf", {}).get("sin_texto") is True)
        check("un ramo sin carpeta declarada no trae material", od.leer_material("MIC", "S2", "prueba", None, config) == [])
    finally:
        shutil.rmtree(semestre, ignore_errors=True)


def probar_hoja_por_evaluacion() -> None:
    """
    Despues de cada clase se rehace la hoja de la proxima evaluacion de su
    ramo: junta solo las clases que entran, usa el material de OneDrive, queda
    en Output y en OneDrive con nombre fijo, y se sobrescribe solo si la
    redaccion salio bien. Todo falso: modelo, vault, OneDrive y fechas, que se
    calculan desde hoy para que la prueba no caduque.
    """
    print("\n== hoja por evaluacion ==")
    import asyncio
    from datetime import timedelta

    from orquestador import evaluaciones as ev
    from orquestador import hoja_evaluacion as he

    hoy = date.today()
    dia = lambda n: (hoy + timedelta(days=n)).isoformat()  # noqa: E731
    base = Path(tempfile.mkdtemp(prefix="hoja_evaluacion_"))
    ramo = "RAMO DE PRUEBA"
    carpeta = base / "vault" / ramo
    carpeta.mkdir(parents=True)
    for n, tema in ((-40, "Tema uno"), (-10, "Tema dos"), (-3, "Tema tres")):
        (carpeta / f"Aprendizaje - Clase {dia(n)} - {tema}.md").write_text(
            f"# {tema}\n\nmateria de {tema}\n", encoding="utf-8")
    datos = {"ramos": {ramo: [
        {"nombre": "S1", "fecha": dia(-20), "tipo": "prueba", "acumula": False, "fuente": "calendario"},
        {"nombre": "S2", "fecha": dia(30), "tipo": "prueba", "acumula": False, "fuente": "calendario"},
        {"nombre": "Examen", "fecha": None, "tipo": "desconocido", "acumula": True,
         "fuente": "calendario"}]},
        "clases_perdidas": [{"ramo": ramo, "fecha": dia(-5)}]}
    semestre = base / "Semestre"
    (semestre / "Ramo de prueba").mkdir(parents=True)
    (semestre / "Ramo de prueba" / "Solemne-2-pauta.pdf").write_bytes(
        _pdf_con_texto("Pregunta pasada sobre tema dos"))
    (semestre / "Ramo de prueba" / "Instrucciones-Trabajo.pdf").write_bytes(
        _pdf_con_texto("Entregar un informe"))
    config = {"rutas": {"vault_obsidian": str(base / "vault"), "output": str(base / "Output")},
              "carpetas_ramo": {ramo: str(carpeta)},
              "carpetas_onedrive": {ramo: str(semestre / "Ramo de prueba")}}
    salida = base / "Output" / ramo / "Evaluaciones" / "Solemne 2 - Ramo de Prueba.html"
    en_onedrive = semestre / "Ramo de prueba" / "Transcripciones" / salida.name

    pedidos, avisos, hojas = [], [], []
    respuesta = {"cuerpo": "<section>primera version</section><section><h2><span>Práctica</span><span class=\"peso\">el caso</span></h2>caso</section>", "motivo": None}

    async def pedir_falso(system_prompt, prompt, etapa, slug, minimo):
        pedidos.append((system_prompt, prompt))
        return (None, respuesta["motivo"]) if respuesta["motivo"] else (respuesta["cuerpo"], None)

    originales = (ev.RUTA, he.pedir_hoja, he.notificar_aviso,
                  he.notificar_hoja_evaluacion, he.notificar_progreso)
    ev.RUTA, he.pedir_hoja = base / "evaluaciones.json", pedir_falso
    he.notificar_aviso = lambda titulo, mensaje: avisos.append((titulo, mensaje))
    he.notificar_hoja_evaluacion = lambda *a: hojas.append(a)
    he.notificar_progreso = lambda *a, **kw: None
    ev.guardar(datos)
    try:
        check("el nombre del ramo se lee bien",
              he.ramo_legible("TALLER DE QUÍMICA III") == "Taller de Química III"
              and he.ramo_legible("HISTORIA Y GEOGRAFÍA") == "Historia y Geografía")

        ruta = asyncio.run(he.actualizar_tras_clase(ramo, dia(-3), config))
        documento = salida.read_text(encoding="utf-8") if salida.is_file() else ""
        system_prompt, prompt = pedidos[-1] if pedidos else ("", "")
        check("una clase entre S1 y S2 rehace la hoja de la S2, con nombre fijo", ruta == salida, str(ruta))
        check("junta solo las clases que entran",
              "materia de Tema dos" in prompt and "materia de Tema tres" in prompt
              and "materia de Tema uno" not in prompt)
        check("y le pasa la pauta pasada, no el enunciado de un trabajo",
              "Pregunta pasada sobre tema dos" in prompt and "Entregar un informe" not in prompt)
        check("con las instrucciones de la hoja de clase mas las de evaluacion",
              "Hoja de repaso de una clase" in system_prompt and "Hoja de una evaluación" in system_prompt)
        check("la tarjeta de datos la arma Python: clases, clase perdida y material",
              "Tema dos" in documento and "Tema uno" not in documento
              and "Clases a las que faltaste" in documento and "Solemne-2-pauta.pdf" in documento)
        check("queda igual en OneDrive y lo avisa",
              en_onedrive.is_file() and en_onedrive.read_text(encoding="utf-8") == documento
              and hojas[-1][2:] == (2, salida, True), str(hojas))
        check("junto a las clases, en Transcripciones, sin carpetas viejas",
              not (semestre / "Ramo de prueba" / "Clases").exists()
              and not (semestre / "Ramo de prueba" / "Creada por Claude").exists())

        respuesta["cuerpo"] = "<section>segunda version</section><section><h2><span>Práctica</span><span class=\"peso\">el caso</span></h2>caso</section>"
        asyncio.run(he.actualizar_tras_clase(ramo, dia(-3), config))
        check("la siguiente clase la reemplaza en el mismo archivo, sin copias (2)",
              "segunda version" in salida.read_text(encoding="utf-8")
              and "segunda version" in en_onedrive.read_text(encoding="utf-8")
              and len(list(en_onedrive.parent.glob("*.html"))) == 1)

        respuesta["motivo"] = "el modelo no devolvió la hoja"
        check("si la redaccion falla, no devuelve hoja",
              asyncio.run(he.actualizar_tras_clase(ramo, dia(-3), config)) is None)
        check("y la version anterior queda intacta, con aviso",
              "segunda version" in salida.read_text(encoding="utf-8")
              and "segunda version" in en_onedrive.read_text(encoding="utf-8")
              and avisos[-1][0] == "No se actualizó la hoja de la Solemne 2", str(avisos))
        respuesta["motivo"] = None

        # La hoja del 18-10-2026: paro a la mitad y nombraba la seccion que faltaba.
        respuesta["cuerpo"] = ("<section><h2>Materia</h2>tercera version a medias. Lo que falta: "
                               "<strong>Práctica con un caso</strong>. ¿Quieres que te devuelva la "
                               "hoja completa?</section>")
        check("si sale cortada, sin la sección de práctica, no devuelve hoja",
              asyncio.run(he.actualizar_tras_clase(ramo, dia(-3), config)) is None)
        check("y la version anterior queda intacta, con aviso de que salió cortada",
              "segunda version" in en_onedrive.read_text(encoding="utf-8")
              and "cortada" in avisos[-1][1], str(avisos[-1]))
        respuesta["cuerpo"] = "<section>segunda version</section><section><h2><span>Práctica</span><span class=\"peso\">el caso</span></h2>caso</section>"

        antes = len(pedidos)
        check("una evaluacion que ya paso queda congelada",
              asyncio.run(he.actualizar_tras_clase(ramo, dia(-25), config)) is None
              and len(pedidos) == antes)

        respuesta["cuerpo"] = "<section>version de ensayo</section><section><h2><span>Práctica</span><span class=\"peso\">el caso</span></h2>caso</section>"
        asyncio.run(he.actualizar_tras_clase(ramo, dia(-3), dict(config, **{ensayo.CLAVE: True})))
        check("en ensayo no toca OneDrive",
              "segunda version" in en_onedrive.read_text(encoding="utf-8") and hojas[-1][4] is False, str(hojas[-1]))

        datos["ramos"][ramo][1]["tipo"] = "trabajo"
        ev.guardar(datos)
        asyncio.run(he.actualizar_tras_clase(ramo, dia(-3), dict(config, **{ensayo.CLAVE: True})))
        _, prompt = pedidos[-1]
        check("si la S2 es un trabajo, pide la hoja del trabajo con su enunciado",
              "Tipo: trabajo" in prompt and "Entregar un informe" in prompt
              and "Pregunta pasada" not in prompt
              and "Hoja del trabajo" in salida.read_text(encoding="utf-8"))
    finally:
        (ev.RUTA, he.pedir_hoja, he.notificar_aviso,
         he.notificar_hoja_evaluacion, he.notificar_progreso) = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_vigilante_de_airdrop() -> None:
    """
    El vigilante toma de Descargas solo el audio que llego por AirDrop (macOS
    lo marca con el agente "sharingd" en com.apple.quarantine), espera a que
    termine de llegar y lo mueve a Input sin pisar nada.
    """
    print("\n== vigilante de AirDrop ==")
    import subprocess

    from orquestador import vigilar_descargas as vd

    base = Path(tempfile.mkdtemp(prefix="descargas_"))
    descargas, entrada = base / "Downloads", base / "Input"
    descargas.mkdir()

    def archivo(nombre: str, agente: str | None) -> Path:
        ruta = descargas / nombre
        ruta.write_bytes(b"audio")
        if agente:
            subprocess.run(["xattr", "-w", "com.apple.quarantine", f"0081;66f00000;{agente};", str(ruta)],
                           check=True)
        return ruta

    clase = archivo("Marketing 29.09.26.m4a", "sharingd")
    archivo("Cancion.mp3", "Safari")
    archivo("Sin marca.m4a", None)
    archivo("Apunte.pdf", "sharingd")
    try:
        check("toma solo el audio que llego por AirDrop",
              vd.audios_de_airdrop(descargas) == [clase], str(vd.audios_de_airdrop(descargas)))

        original = vd.time.sleep
        vd.time.sleep = lambda s: clase.write_bytes(b"audio que sigue llegando")
        try:
            check("no toma un audio que todavia esta llegando", vd.estables([clase]) == [])
        finally:
            vd.time.sleep = original
        check("si toma uno que ya termino de llegar", vd.estables([clase], espera=0.01) == [clase])

        entrada.mkdir()
        (entrada / clase.name).write_bytes(b"otra grabacion con el mismo nombre")
        movidos = vd.mover_a_input([clase], entrada)
        check("lo mueve a Input sin pisar un nombre ocupado",
              [p.name for p in movidos] == ["Marketing 29.09.26 (2).m4a"] and not clase.exists()
              and (entrada / clase.name).read_bytes() == b"otra grabacion con el mismo nombre",
              str(movidos))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def probar_cliente_telegram() -> None:
    """
    El cliente de Telegram parte los mensajes largos, sube archivos, nunca
    muestra el token en un error, y los avisos del pipeline nunca fallan ni
    demoran el procesamiento si Telegram no responde. Red falsa.
    """
    print("\n== cliente de Telegram ==")
    import json as _json
    import urllib.error

    tg = _telegram
    pedidos = []
    respuesta = {"ok": True, "result": {"message_id": 7}}

    class _Resp:
        def __init__(self, datos):
            self.datos = datos

        def read(self, *a):
            return self.datos

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen_falso(req, timeout=None):
        if respuesta.get("caida"):
            raise urllib.error.URLError("sin red")
        if respuesta.get("corte"):
            raise TimeoutError("The read operation timed out")
        pedidos.append(req)
        return _Resp(_json.dumps(respuesta).encode())

    originales = (tg.token, tg.chat_autorizado, tg.urllib.request.urlopen)
    tg.token, tg.chat_autorizado = (lambda: "123:SECRETO"), (lambda: 42)
    tg.urllib.request.urlopen = urlopen_falso
    try:
        largo = "\n".join(f"linea {i} " + "x" * 80 for i in range(100))
        tg.enviar(largo, teclado=tg.botones([("Sí, anótalo", "si"), ("No", "no")]))
        cuerpos = [_json.loads(r.data) for r in pedidos]
        check("un mensaje largo se parte en trozos de hasta 4096",
              len(cuerpos) == 3 and all(len(c["text"]) <= 4096 for c in cuerpos)
              and "".join(c["text"] for c in cuerpos).replace("\n", "") == largo.replace("\n", ""),
              str([len(c["text"]) for c in cuerpos]))
        check("los botones van solo en el ultimo trozo",
              "reply_markup" in cuerpos[-1] and all("reply_markup" not in c for c in cuerpos[:-1])
              and cuerpos[-1]["reply_markup"]["inline_keyboard"][0][1] == {"text": "No", "callback_data": "no"})

        pedidos.clear()
        archivo = Path(tempfile.mkdtemp(prefix="tg_")) / "Solemne 2 - Ramo.html"
        archivo.write_text("<html>hoja</html>", encoding="utf-8")
        tg.enviar_documento(archivo, "Hoja al día")
        cuerpo = pedidos[0].data
        check("sube el archivo como documento",
              b"<html>hoja</html>" in cuerpo and b'name="chat_id"\r\n\r\n42' in cuerpo
              and b'filename="Solemne 2 - Ramo.html"' in cuerpo
              and pedidos[0].full_url.endswith("/sendDocument"))
        shutil.rmtree(archivo.parent, ignore_errors=True)

        respuesta.update(ok=False, description="Unauthorized")
        try:
            tg.enviar("hola")
            mensaje = ""
        except RuntimeError as e:
            mensaje = str(e)
        check("un rechazo de Telegram se explica sin mostrar el token",
              "Unauthorized" in mensaje and "SECRETO" not in mensaje, mensaje)
        check("un aviso del pipeline nunca lanza aunque Telegram rechace", tg.avisar("hola") is False)
        respuesta["caida"] = True
        check("ni aunque no haya red", tg.avisar("hola") is False)
        respuesta.update(caida=False, corte=True)
        try:
            tg.llamar("getUpdates", espera=70, timeout=50)
            mensaje = ""
        except RuntimeError as e:
            mensaje = str(e)
        check("un corte por tiempo llega como falta de conexion, que el bot ya maneja",
              "sin conexión" in mensaje, mensaje)
        tg.chat_autorizado = lambda: None
        check("sin chat emparejado no intenta nada", tg.avisar("hola") is False)
    finally:
        tg.token, tg.chat_autorizado, tg.urllib.request.urlopen = originales


def probar_comprimir_aac_pesado() -> None:
    """Las notas de voz llegan a 251 kbps en estereo. Se bajan a 48 kbps mono, y
    el original se aparta para que el estudiante lo borre, nunca se borra."""
    print("\n== un AAC pesado se comprime y el original se aparta ==")
    from orquestador import comprimir_notas_voz as cnv

    if not shutil.which("ffmpeg"):
        print("  (sin ffmpeg: se salta)")
        return
    base = Path(tempfile.mkdtemp(prefix="comprimir_"))
    original_carpeta = cnv.CARPETA_A_BORRAR
    cnv.CARPETA_A_BORRAR = base / "por borrar"
    try:
        audio = base / "RAMO" / "clase.m4a"
        audio.parent.mkdir()
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                        "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(audio)], check=True)
        resultado = cnv.comprimir_en_sitio(audio)
        check("se comprime y pesa menos", resultado is not None and resultado[1] < resultado[0], str(resultado))
        check("queda en mono con el mismo nombre", audio.is_file() and "1" == subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=channels", "-of", "csv=p=0", str(audio)],
            capture_output=True, text=True).stdout.strip())
        check("el original queda apartado para borrar a mano",
              (base / "por borrar" / "RAMO" / "clase.m4a").is_file())
        check("y uno ya liviano no se vuelve a tocar", cnv.comprimir_en_sitio(audio) is None)
    finally:
        cnv.CARPETA_A_BORRAR = original_carpeta
        shutil.rmtree(base, ignore_errors=True)


def probar_secretaria_espera_la_conexion() -> None:
    """El 12-10-2026 el Mac no tenia red a las 9:00 y el mensaje se perdio. Sin
    conexion se reintenta, y cualquier otro error no se tapa."""
    print("\n== la secretaria espera a que vuelva la conexion ==")
    from orquestador import secretaria as sec

    original_sleep = sec.time.sleep
    sec.time.sleep = lambda s: None
    try:
        llamadas = []

        def falla_dos_veces(texto):
            llamadas.append(texto)
            if len(llamadas) < 3:
                raise RuntimeError("Telegram rechazó sendMessage: sin conexión ([Errno 8])")

        sec._enviar_con_paciencia(falla_dos_veces, "hola")
        check("sin conexion reintenta hasta que sale", len(llamadas) == 3, str(len(llamadas)))

        def otro_error(texto):
            llamadas.append(texto)
            raise RuntimeError("Telegram rechazó sendMessage: chat not found")

        llamadas.clear()
        try:
            sec._enviar_con_paciencia(otro_error, "hola")
            salio = False
        except RuntimeError:
            salio = True
        check("otro error no se reintenta y se ve", salio and len(llamadas) == 1)
    finally:
        sec.time.sleep = original_sleep


def probar_pedidos_y_secretaria() -> None:
    """
    Lo que el profesor pide para una fecha queda anotado y la secretaria lo
    recuerda ese dia a las 9:00, junto con las evaluaciones de la semana y la
    vispera de cada una. Si no hay nada, no escribe.
    """
    print("\n== pedidos del profesor y mensaje de las 9:00 ==")
    from datetime import date as _date

    from orquestador import evaluaciones as ev
    from orquestador import secretaria as se

    transcripcion = "para la proxima clase traigan leido el caso cox energy completo por favor"
    datos = {"ramos": {"RAMO": [{"nombre": "S2", "fecha": "2026-10-09", "tipo": "prueba"}]},
             "feriados": ["2026-10-12"], "pendientes": []}
    aviso = {"que": "Traer leído el caso Cox", "cuando": "la próxima clase", "fecha": None,
             "para_la_proxima_clase": True, "textual": "traigan leido el caso cox energy completo",
             "seguro": True}
    anotados = ev.aplicar_pedidos(datos, "RAMO", [aviso], "2026-10-05", transcripcion, 0)
    check("\"para la proxima clase\" cae en la siguiente clase del ramo, saltando el feriado",
          datos["pendientes"][0]["fecha"] == "2026-10-19" and anotados, str(datos["pendientes"]))
    check("repetirlo no lo duplica",
          ev.aplicar_pedidos(datos, "RAMO", [aviso], "2026-10-05", transcripcion, 0) == []
          and len(datos["pendientes"]) == 1)
    check("una cita que no esta en el audio no se anota",
          ev.aplicar_pedidos(datos, "RAMO", [dict(aviso, textual="traigan el informe final impreso")],
                             "2026-10-05", transcripcion, 0) == [])
    check("un pedido sin fecha ni proxima clase no se anota",
          ev.aplicar_pedidos(datos, "RAMO", [dict(aviso, para_la_proxima_clase=False)],
                             "2026-10-05", transcripcion, 0) == [])

    base = Path(tempfile.mkdtemp(prefix="secretaria_"))
    config = {"rutas": {"output": str(base)}}
    hoja = base / "RAMO" / "Evaluaciones" / "Solemne 2 - Ramo.html"
    hoja.parent.mkdir(parents=True)
    hoja.write_text("hoja")
    try:
        texto, hojas = se.resumen(_date(2026, 10, 15), datos, config)
        check("sin nada que decir, no escribe", texto is None and hojas == [], str(texto))
        datos["ramos"]["RAMO"][0]["fecha"] = "2026-10-20"
        texto, hojas = se.resumen(_date(2026, 10, 19), datos, config)
        check("el dia que toca recuerda el pedido con la cita del profesor",
              "Traer leído el caso Cox" in (texto or "") and "traigan leido el caso cox" in (texto or ""),
              str(texto))
        check("y la vispera avisa la evaluacion y adjunta su hoja",
              "Mañana: Solemne 2 de Ramo (prueba)" in (texto or "") and hojas == [hoja], str(texto))
        texto, _ = se.resumen(_date(2026, 10, 19), datos, config, fin_de_semana=True)
        check("el fin de semana solo avisa evaluaciones",
              "Mañana:" in (texto or "") and "Traer leído" not in (texto or ""), str(texto))

        # Controles y solemnes: dos dias antes, la vispera y el mismo dia (08-10-2026).
        datos["pendientes"].append({"ramo": "RAMO", "fecha": "2026-10-22", "que": "Control 2",
                                    "textual": "el veintidos es el control dos", "clase": "2026-10-15"})
        avisos = {d: se.resumen(_date(2026, 10, d), datos, config)[0] or "" for d in (19, 20, 21, 22)}
        check("el control se avisa dos dias antes, la vispera y el mismo dia",
              "Pasado mañana: Ramo: Control 2" in avisos[20] and "Mañana: Ramo: Control 2" in avisos[21]
              and "Hoy: Ramo: Control 2" in avisos[22], str(avisos))
        check("la solemne tambien se avisa dos dias antes y el mismo dia",
              "Pasado mañana: Solemne 2" in se.resumen(_date(2026, 10, 18), datos, config)[0]
              and "Hoy: Solemne 2" in avisos[20], str(avisos[20]))
        check("el control de hoy no se repite como pedido", avisos[22].count("Control 2") == 1, avisos[22])
        check("antes de los dos dias va en la lista de la semana",
              "jueves 22 de octubre: Ramo: Control 2" in avisos[19], avisos[19])
        check("el fin de semana tambien avisa el control",
              "Mañana: Ramo: Control 2" in (se.resumen(_date(2026, 10, 21), datos, config, fin_de_semana=True)[0] or ""))
        datos["pendientes"].pop()
        datos["ramos"]["RAMO"][0]["fecha"] = "2026-10-24"
        texto, _ = se.resumen(_date(2026, 10, 18), datos, config)
        check("las evaluaciones de la semana van con su dia",
              "sábado 24 de octubre: Solemne 2 de Ramo" in (texto or ""), str(texto))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def probar_bot_de_telegram() -> None:
    """
    El bot contesta "Recibido" al instante, recuerda la conversacion por 3
    horas o hasta /nuevaconversacion, pide confirmacion antes de cambiar las
    fechas, guarda los archivos en "Bot de IA" del ramo, y no le contesta a
    nadie mas. Telegram, el modelo, OneDrive y las evaluaciones son falsos.
    """
    print("\n== bot de Telegram ==")
    import json
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from orquestador import bot as b
    from orquestador import evaluaciones as ev

    base = Path(tempfile.mkdtemp(prefix="bot_"))
    ramo = "RAMO DE PRUEBA"
    vault = base / "vault" / ramo
    vault.mkdir(parents=True)
    semestre = base / "Semestre"
    (semestre / "Ramo de prueba").mkdir(parents=True)
    config = {"rutas": {"output": str(base / "Output"), "vault_obsidian": str(base / "vault")},
              "carpetas_ramo": {ramo: str(vault)},
              "carpetas_onedrive": {ramo: str(semestre / "Ramo de prueba")},
              "ramos": {"lunes": {"nombre": ramo, "inicio": "08:30", "fin": "10:00", "sala": "A-101"}}}
    datos = {"ramos": {ramo: [{"nombre": "S2", "fecha": "2026-11-09", "tipo": "prueba",
                               "fuente": "clase", "clase": "2026-09-28",
                               "textual": "la solemne dos es el nueve"}]},
             "clases_perdidas": [], "pendientes": []}

    enviados, editados, sesiones = [], [], []
    respuesta = {"texto": "La elasticidad mide... (clase 28-09)"}

    def enviar(texto, chat_id=None, teclado=None):
        enviados.append((texto, teclado))
        return {"message_id": len(enviados)}

    async def preguntar_falso(texto, sesion, system_prompt, raices, al_usar):
        sesiones.append(sesion)
        respuesta["prompt"] = system_prompt + "\n" + texto
        respuesta["system"], respuesta["mensaje"] = system_prompt, texto
        return respuesta["texto"], "sesion-1", None

    def descargar(file_id, destino):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"foto")
        return destino

    cambios = []  # (mensaje, botones con que queda) de cada cambiar_botones
    tg = b.telegram
    originales = (tg.enviar, tg.editar, tg.escribiendo, tg.responder_boton, tg.descargar,
                  b._preguntar, b.RUTA_ESTADO, ev.RUTA, b.cargar_config, tg.cambiar_botones,
                  b.RUTA_OPINIONES)
    tg.enviar, tg.editar = enviar, (lambda mid, texto, chat_id=None: editados.append(texto))
    tg.cambiar_botones = lambda mid, teclado=None, chat_id=None: cambios.append((mid, teclado))
    b.RUTA_OPINIONES = base / "opiniones.jsonl"

    def datos_de(teclado):
        return [x["callback_data"] for fila in (teclado or {}).get("inline_keyboard", []) for x in fila]

    def confirma(teclado):
        return "si" in datos_de(teclado)
    tg.escribiendo, tg.responder_boton, tg.descargar = (lambda *a: None), (lambda *a: None), descargar
    b._preguntar, b.RUTA_ESTADO = preguntar_falso, base / "bot_estado.json"
    ev.RUTA, b.cargar_config = base / "evaluaciones.json", (lambda: config)
    ev.guardar(datos)

    def mensaje(texto=None, chat=1, **extra):
        return {"message": {"chat": {"id": chat}, "text": texto, **extra}}

    def boton(dato):
        return {"callback_query": {"id": "x", "data": dato, "message": {"chat": {"id": 1}}}}

    def boton_en(dato, mid):
        # Telegram trae el id del mensaje donde se toco el boton.
        return {"callback_query": {"id": "x", "data": dato, "message": {"chat": {"id": 1}, "message_id": mid}}}

    def accion(**campos):
        return "ACCION: " + json.dumps({"ramo": ramo, **campos}, ensure_ascii=False)

    try:
        b.atender(mensaje("¿qué es la elasticidad?"), 1)
        check("contesta Recibido antes de pensar, y la respuesta despues",
              enviados[0][0] == "⏳ Recibido, pensando..." and enviados[-1][0].startswith("La elasticidad")
              and editados[-1].startswith("✅ Listo ("), str(enviados))

        b.atender(mensaje("¿y un ejemplo?"), 1)
        check("una pregunta de seguimiento sigue la misma conversacion", sesiones[-1] == "sesion-1")
        estado = json.loads(b.RUTA_ESTADO.read_text())
        estado["ultimo"] = (_dt.now() - _td(hours=4)).isoformat()
        b.RUTA_ESTADO.write_text(json.dumps(estado))
        b.atender(mensaje("otra cosa"), 1)
        check("despues de 3 horas sin escribir parte de cero", sesiones[-1] is None)
        b.atender(mensaje("/nuevaconversacion"), 1)
        b.atender(mensaje("hola de nuevo"), 1)
        check("/nuevaconversacion borra la memoria de inmediato",
              sesiones[-1] is None and "empezamos de cero" in enviados[-3][0], str(enviados[-3:]))

        respuesta["texto"] = ("Entendido.\nACCION: {\"tipo\": \"clase_perdida\", \"ramo\": \"" + ramo
                              + "\", \"fecha\": \"2026-10-05\"}")
        b.atender(mensaje("falté a la clase del lunes"), 1)
        texto, teclado = enviados[-1]
        check("un aviso que cambia fechas pide confirmacion con botones, sin mostrar la linea ACCION",
              "ACCION" not in texto and "¿Anoto que faltaste" in texto and confirma(teclado), texto)
        b.atender(boton("si"), 1)
        check("y lo anota solo al confirmar",
              ev.cargar()["clases_perdidas"] == [{"ramo": ramo, "fecha": "2026-10-05"}]
              and "Anotado" in enviados[-1][0], str(enviados[-1]))

        respuesta["texto"] = ("Ok.\nACCION: {\"tipo\": \"fecha_evaluacion\", \"ramo\": \"" + ramo
                              + "\", \"evaluacion\": \"S2\", \"fecha\": \"2026-11-16\"}")
        b.atender(mensaje("la S2 se movió al 16"), 1)
        check("si el profesor dijo otra cosa en clase, lo advierte antes de anotar",
              "Ojo: en la clase del" in enviados[-1][0], enviados[-1][0])
        b.atender(boton("no"), 1)
        check("y con No no toca nada", ev.cargar()["ramos"][ramo][0]["fecha"] == "2026-11-09")

        respuesta["texto"] = "Ok.\nACCION: {\"tipo\": \"clase_perdida\", \"ramo\": \"OTRO\", \"fecha\": \"2026-10-05\"}"
        b.atender(mensaje("falté a otro ramo"), 1)
        check("una accion con un ramo que no existe no ofrece botones", not confirma(enviados[-1][1]))
        check("el bot conoce el horario", "lunes: Ramo de Prueba, 08:30 a 10:00, sala A-101"
              in respuesta["prompt"], respuesta["prompt"][-600:])

        # Programas: solo el declarado a mano, nunca cualquier PDF que diga "programa".
        (semestre / "Ramo de prueba" / "Programa del ramo.pdf").write_bytes(b"%PDF")
        (semestre / "Ramo de prueba" / "programa de becas - postulacion.pdf").write_bytes(b"%PDF")
        nombres = [r.name for r in b._raices_de_lectura({**config, "programas": {ramo: "Programa del ramo.pdf"}})]
        check("el bot lee el programa declarado y no otro PDF que diga programa",
              "Programa del ramo.pdf" in nombres and "programa de becas - postulacion.pdf" not in nombres,
              str(nombres))
        check("sin programa declarado no entra ninguno",
              not any("rograma" in x for x in (r.name for r in b._raices_de_lectura(config))))

        respuesta["texto"] = ("Ok.\nACCION: {\"tipo\": \"pendiente\", \"ramo\": \"" + ramo
                              + "\", \"fecha\": \"2036-10-13\", \"que\": \"Control 2\"}")
        b.atender(mensaje("el lunes 12 hay control 2"), 1)
        check("un control contado por el chat pide confirmacion",
              "¿Anoto para el lunes 13 de octubre" in enviados[-1][0] and confirma(enviados[-1][1]),
              enviados[-1][0])
        b.atender(boton("si"), 1)
        b.atender(mensaje("el lunes 12 hay control 2"), 1)
        b.atender(boton("si"), 1)
        anotados = [x for x in ev.cargar()["pendientes"] if x["que"] == "Control 2"]
        check("y queda en los pendientes una sola vez", len(anotados) == 1, str(anotados))
        from datetime import date as _date

        from orquestador import secretaria as se
        # Una fecha lejana: el contexto del bot solo lista lo de hoy en adelante.
        texto9, _ = se.resumen(_date(2036, 10, 13), ev.cargar(), config)
        check("la secretaria lo avisa ese dia como evaluacion",
              "🚨 Hoy: Ramo de Prueba: Control 2" in (texto9 or ""), str(texto9))
        b.atender(mensaje("otra pregunta"), 1)
        check("y el bot lo ve en su contexto", "Control 2" in respuesta["prompt"])
        # La hora y los pendientes cambian: fuera del prompt fijo, o el cache no sirve.
        secciones = ("## Hoy", "## Evaluaciones", "## Pendientes anotados")
        check("lo que cambia va con cada mensaje, no en el prompt fijo",
              all(x in respuesta["mensaje"] and x not in respuesta["system"] for x in secciones)
              and "Control 2" in respuesta["mensaje"])
        check("y el prompt fijo sigue trayendo horario y rutas",
              "## Horario de clases" in respuesta["system"] and "## Rutas que puedes leer" in respuesta["system"])

        # --- La secretaria: tono, clase suspendida, cancelar y marcar hecho ---
        tipos = ("clase_perdida", "clase_suspendida", "fecha_evaluacion", "pendiente", "hecho", "cancelar")
        check("las instrucciones ofrecen las seis acciones y las reglas de secretaria",
              all(f'"tipo": "{t}"' in respuesta["system"] for t in tipos)
              and "No especules" in respuesta["system"] and "No digas \"Anotado\"" in respuesta["system"])

        respuesta["texto"] = accion(tipo="clase_suspendida", fecha="2026-10-12")
        b.atender(mensaje("se suspendió la clase de hoy"), 1)
        texto, teclado = enviados[-1]
        check("una clase suspendida se propone como suspendida, sin decir que faltaste, y sin relleno",
              "se suspendió" in texto and "faltaste" not in texto and confirma(teclado)
              and texto.startswith("📝"), texto)
        b.atender(boton_en("si", len(enviados)), 1)
        datos_ev = ev.cargar()
        check("y queda como fecha sin clase, sin tocar las clases perdidas",
              datos_ev["clases_suspendidas"] == [{"ramo": ramo, "fecha": "2026-10-12"}]
              and datos_ev["clases_perdidas"] == [{"ramo": ramo, "fecha": "2026-10-05"}]
              and "Anotado" in enviados[-1][0], str(enviados[-1]))
        pedido = {"que": "Traer el caso leído", "seguro": True, "para_la_proxima_clase": True,
                  "textual": "para la próxima clase traigan el caso leído"}
        ev.registrar_clase(ramo, [], [pedido], "2026-10-05", "dijo para la próxima clase traigan el caso leído ok", 0)
        fechas = [x["fecha"] for x in ev.cargar()["pendientes"] if x["que"] == "Traer el caso leído"]
        check("lo pedido para la próxima clase salta la clase suspendida", fechas == ["2026-10-19"], str(fechas))

        respuesta["texto"] = accion(tipo="pendiente", fecha="2036-11-02", que="Entrega 1")
        b.atender(mensaje("hay una entrega el 2"), 1)
        propuesta = len(enviados)
        respuesta["texto"] = 'ACCION: {"tipo": "cancelar"}'
        b.atender(mensaje("no, esa no era"), 1)
        check("cancelar retira la propuesta, le quita los botones y avisa que no anotó nada",
              cambios[-1][0] == propuesta and not confirma(cambios[-1][1]) and "No anoté nada" in enviados[-1][0]
              and "pendiente" not in json.loads(b.RUTA_ESTADO.read_text()), f"{cambios[-1:]} {enviados[-1]}")
        b.atender(boton_en("si", propuesta), 1)
        check("y tocar ese botón después no anota nada",
              "ya no está vigente" in enviados[-1][0]
              and not [x for x in ev.cargar()["pendientes"] if x["que"] == "Entrega 1"])
        b.atender(mensaje("no, esa no era"), 1)
        check("cancelar sin nada que cancelar lo dice", "nada pendiente" in enviados[-1][0], enviados[-1][0])

        respuesta["texto"] = accion(tipo="pendiente", fecha="2036-11-02", que="Entrega 1")
        b.atender(mensaje("hay una entrega el 2"), 1)
        primera = len(enviados)
        respuesta["texto"] = accion(tipo="pendiente", fecha="2036-11-09", que="Entrega 2")
        b.atender(mensaje("perdón, era el 9"), 1)
        segunda = len(enviados)
        check("una propuesta nueva le quita el Sí/No a la anterior",
              cambios[-1][0] == primera and not confirma(cambios[-1][1]), str(cambios[-1:]))
        b.atender(boton_en("si", primera), 1)
        check("y el botón de la anterior no confirma la nueva",
              "ya no está vigente" in enviados[-1][0]
              and not [x for x in ev.cargar()["pendientes"] if x["que"].startswith("Entrega")])
        b.atender(boton_en("si", segunda), 1)
        check("pero el de la nueva sí",
              [x["que"] for x in ev.cargar()["pendientes"] if x["que"].startswith("Entrega")] == ["Entrega 2"])

        ev.anotar_pendiente("REUNIÓN", "2036-11-20", "Mandar el CV", "reunion")
        respuesta["texto"] = accion(tipo="hecho", que="control 2")
        b.atender(mensaje("ya hice el control 2"), 1)
        texto, teclado = enviados[-1]
        check("marcar un pendiente como hecho pide confirmación, aunque no diga la fecha ni las mayúsculas",
              "Control 2" in texto and "hecho" in texto and confirma(teclado), texto)
        b.atender(boton_en("si", len(enviados)), 1)
        dia = _date(2036, 10, 13)
        check("y deja de salir en /pendientes, pero los otros siguen",
              "Control 2" not in se.lista_pendientes(ev.cargar(), dia)
              and "Entrega 2" in se.lista_pendientes(ev.cargar(), dia), se.lista_pendientes(ev.cargar(), dia))
        texto9, _ = se.resumen(dia, ev.cargar(), config)
        check("ni lo avisa la secretaria ese día", "Control 2" not in (texto9 or ""), str(texto9))
        check("se conserva en el archivo, con su estado",
              [x["estado"] for x in ev.cargar()["pendientes"] if x["que"] == "Control 2"] == ["hecho"])
        respuesta["texto"] = accion(tipo="hecho", que="Control 2")
        b.atender(mensaje("ya hice el control 2"), 1)
        check("uno que ya está hecho no se vuelve a ofrecer", not confirma(enviados[-1][1]))
        respuesta["texto"] = accion(tipo="hecho", que="Algo que nunca anoté")
        b.atender(mensaje("ya hice algo"), 1)
        check("ni uno que no existe", not confirma(enviados[-1][1]))
        respuesta["texto"] = accion(tipo="hecho", que="Mandar el CV").replace(ramo, "REUNIÓN")
        b.atender(mensaje("ya mandé el CV"), 1)
        check("el pendiente de una reunión también se marca", confirma(enviados[-1][1]), enviados[-1][0])

        # --- 👍/👎 en cada respuesta ---
        def opiniones():
            ruta = b.RUTA_OPINIONES
            return [json.loads(x) for x in ruta.read_text().splitlines()] if ruta.exists() else []

        respuesta["texto"] = "La elasticidad mide la sensibilidad... (clase 28-09)"
        b.atender(mensaje("¿qué es la elasticidad?"), 1)
        votada = len(enviados)
        check("cada respuesta lleva 👍 y 👎, sin Sí/No si no propone nada",
              datos_de(enviados[-1][1]) == ["voto:+", "voto:-"], str(enviados[-1][1]))
        b.atender(boton_en("voto:-", votada), 1)
        texto, teclado = enviados[-1]
        check("👎 pregunta en una línea qué faltó, como respuesta opcional",
              "¿Qué faltó?" in texto and (teclado or {}).get("force_reply") is True, texto)
        check("y le quita los votos a esa respuesta", cambios[-1] == (votada, None), str(cambios[-1:]))
        reg = opiniones()
        check("el 👎 queda registrado con la pregunta, la respuesta y la sesión",
              len(reg) == 1 and reg[0]["voto"] == "-" and reg[0]["pregunta"] == "¿qué es la elasticidad?"
              and reg[0]["respuesta"].startswith("La elasticidad") and reg[0]["sesion"] == "sesion-1", str(reg))
        llamadas = len(sesiones)
        b.atender(mensaje("le faltó un ejemplo numérico", reply_to_message={"message_id": len(enviados)}), 1)
        reg = opiniones()
        check("contestar esa pregunta deja el motivo, sin llamar al modelo",
              reg[-1]["motivo"] == "le faltó un ejemplo numérico" and reg[-1]["voto"] == "-"
              and len(sesiones) == llamadas and "Gracias" in enviados[-1][0], str(reg[-1:]))
        b.atender(boton_en("voto:+", votada), 1)
        check("una respuesta ya votada no se vota de nuevo",
              "ya no está vigente" in enviados[-1][0] and len(opiniones()) == 2)

        b.atender(mensaje("¿y la elasticidad cruzada?"), 1)
        b.atender(boton_en("voto:-", len(enviados)), 1)
        b.atender(mensaje("otra pregunta, sin contestar el qué faltó"), 1)
        check("ignorar el qué faltó deja seguir conversando",
              len(sesiones) == llamadas + 2 and opiniones()[-1]["motivo"] is None)
        b.atender(boton_en("voto:+", len(enviados)), 1)
        check("👍 queda registrado sin preguntar nada",
              opiniones()[-1]["voto"] == "+" and "Qué faltó" not in enviados[-1][0])

        respuesta["texto"] = accion(tipo="pendiente", fecha="2036-12-01", que="Entrega 3")
        b.atender(mensaje("hay una entrega el 1"), 1)
        propuesta = len(enviados)
        check("una propuesta lleva Sí/No y además los votos",
              datos_de(enviados[-1][1]) == ["si", "no", "voto:+", "voto:-"], str(enviados[-1][1]))
        b.atender(boton_en("si", propuesta), 1)
        check("al confirmar se va el Sí/No y quedan los votos",
              cambios[-1][0] == propuesta and datos_de(cambios[-1][1]) == ["voto:+", "voto:-"], str(cambios[-1:]))

        # --- Memoria: perfil, wiki y guardar con confirmacion ---
        wiki = base / "vault" / "Wiki"
        wiki.mkdir()
        (wiki / "Perfil de Alguien.md").write_text("# Perfil\n\nEstudia de noche y busca práctica.", encoding="utf-8")
        (wiki / "Índice - Wiki.md").write_text("# Índice - Wiki\n\n## Personas y organizaciones\n", encoding="utf-8")
        (wiki / "Registro - Wiki.md").write_text("# Registro - Wiki\n", encoding="utf-8")
        respuesta["texto"] = "Hola."
        b.atender(mensaje("hola"), 1)
        check("el bot trae el perfil en su prompt fijo y puede leer la wiki",
              "Estudia de noche y busca práctica." in respuesta["system"]
              and str(wiki) in respuesta["system"] and wiki in b._raices_de_lectura(config))
        check("pero no el resto de la bóveda", base / "vault" not in b._raices_de_lectura(config))

        recuerdo = dict(tipo="memoria", pagina="Empresa Ejemplo", tipo_pagina="entidad",
                        texto="Pasé a la segunda etapa de la práctica, que es un caso grupal")
        respuesta["texto"] = "Qué bueno.\n" + accion(**recuerdo)
        b.atender(mensaje("pasé a la segunda etapa en Empresa Ejemplo"), 1)
        texto, teclado = enviados[-1]
        check("ofrece guardarlo con un botón, mostrando el texto exacto",
              "🧠 ¿Lo guardo en tu memoria?\n📄 Empresa Ejemplo\n• Pasé a la segunda etapa" in texto
              and teclado["inline_keyboard"][0][0]["text"] == "Sí, guárdalo", texto)
        check("y sin confirmar no escribe nada", not (wiki / "Personas y organizaciones").exists())
        b.atender(boton_en("si", len(enviados)), 1)
        guardada = wiki / "Personas y organizaciones" / "Empresa Ejemplo.md"
        check("al confirmar queda en la wiki, con el índice y el registro",
              guardada.is_file() and "caso grupal" in guardada.read_text(encoding="utf-8")
              and "[[Empresa Ejemplo]]" in (wiki / "Índice - Wiki.md").read_text(encoding="utf-8")
              and "Empresa Ejemplo" in (wiki / "Registro - Wiki.md").read_text(encoding="utf-8")
              and "Guardado en tu memoria" in enviados[-1][0], enviados[-1][0])
        (base / "vault" / "Carrera").mkdir()
        (base / "vault" / "Carrera" / "Proceso de práctica.md").write_text("nota fuente", encoding="utf-8")
        respuesta["texto"] = accion(**dict(recuerdo, pagina="Proceso de práctica"))
        b.atender(mensaje("la práctica parte en enero"), 1)
        check("no ofrece guardar en una nota que está fuera de la wiki", not confirma(enviados[-1][1]))
        respuesta["texto"] = accion(**dict(recuerdo, texto="Mi correo para la práctica es alguien@example.com"))
        b.atender(mensaje("anota mi correo"), 1)
        check("un recuerdo con un dato sensible no se ofrece", not confirma(enviados[-1][1]))
        respuesta["texto"] = accion(**dict(recuerdo, texto="Otra cosa que no quiero guardar"))
        b.atender(mensaje("algo más"), 1)
        b.atender(boton_en("no", len(enviados)), 1)
        check("con No no se guarda nada",
              "Otra cosa" not in guardada.read_text(encoding="utf-8") and "no anoté nada" in enviados[-1][0])

        foto = {"photo": [{"file_id": "chica"}, {"file_id": "grande", "file_size": 1000}]}
        b.atender(mensaje(**foto), 1)
        check("una foto sin ramo pregunta de que ramo es", "¿De qué ramo es?" in enviados[-1][0])
        b.atender(boton("ramo:0"), 1)
        guardadas = list((semestre / "Ramo de prueba" / "Bot de IA").glob("Foto *.jpg"))
        check("y la guarda en Bot de IA del ramo", len(guardadas) == 1 and "Guardado en" in enviados[-1][0],
              str(guardadas))
        b.atender(mensaje(caption="ramo de prueba", document={"file_id": "d", "file_name": "Guia.pdf"}), 1)
        check("un archivo que dice su ramo se guarda sin preguntar",
              (semestre / "Ramo de prueba" / "Bot de IA" / "Guia.pdf").is_file())
        b.atender(mensaje(document={"file_id": "g", "file_name": "Enorme.pdf", "file_size": 30_000_000}), 1)
        check("uno de mas de 20 MB se rechaza explicando por que", "20 MB" in enviados[-1][0])

        b.atender(mensaje("/resumenhtmlparasolemne"), 1)
        b.atender(boton("hoja:0"), 1)
        check("sin hoja todavia, lo dice", "Todavía no hay hoja" in enviados[-1][0], enviados[-1][0])
        b.atender(mensaje("/evaluaciones"), 1)
        check("/evaluaciones lista las fechas del ramo", "Ramo de Prueba" in enviados[-1][0])

        antes = len(enviados)
        b.atender(mensaje("hola, soy otra persona", chat=999), 1)
        b.atender({"callback_query": {"id": "y", "data": "si", "message": {"chat": {"id": 999}}}}, 1)
        check("a cualquier otro chat no le contesta", len(enviados) == antes)
    finally:
        (tg.enviar, tg.editar, tg.escribiendo, tg.responder_boton, tg.descargar,
         b._preguntar, b.RUTA_ESTADO, ev.RUTA, b.cargar_config, tg.cambiar_botones,
         b.RUTA_OPINIONES) = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_revision_de_reuniones() -> None:
    """
    Una reunion o entrevista pasa por un revisor aparte, como una clase: marca al
    lado de cada punto lo que la transcripcion no respalda, y pone el puntaje de 1
    a 100. Si el revisor falla, la reunion sigue igual y dice que no se pudo medir.
    """
    print("\n== revision de reuniones ==")
    import asyncio
    import json as _json

    from orquestador import reuniones as re_

    datos = {"titulo": "T", "resumen": "Se habló del avance.", "decisiones": ["Entregar el viernes", "Usar R"],
             "acciones": [{"que": "Mandar el informe", "quien": "el estudiante"}],
             "seguimientos": [], "preguntas_abiertas": [], "investigar": [], "entrevista": None,
             "calidad_audio": "regular"}
    revision = {"hallazgos": [{"campo": "decisiones", "indice": 1, "problema": "nadie dijo R"},
                              {"campo": "acciones", "indice": 0, "problema": "no se dijo quién"},
                              {"campo": "resumen", "indice": None, "problema": "no se habló del avance"},
                              {"campo": "decisiones", "indice": 9, "problema": "fuera de rango"}],
                "transcripcion": {"puntaje": 81, "motivo": "se entiende casi todo"}}
    linea = f"{re_.MARCA_REVISION_INICIO}\n{_json.dumps(revision)}\n{re_.MARCA_REVISION_FIN}"
    check("lee la revision entre sus marcas", re_.extraer_revision(linea) == revision)
    check("una revision rota no se acepta", re_.extraer_revision("nada") is None)

    marcado = re_.aplicar_revision(_json.loads(_json.dumps(datos)), revision)
    secciones = dict(re_.secciones(marcado))
    check("la decision sin respaldo queda marcada al lado",
          secciones["Decisiones"] == ["Entregar el viernes", "Usar R (a verificar: nadie dijo R)"],
          str(secciones["Decisiones"]))
    check("la accion tambien", "a verificar: no se dijo quién" in secciones["Acciones"][0])
    check("y el resumen", "A verificar: no se habló del avance" in secciones["Resumen"])
    check("el puntaje reemplaza a la calidad del audio",
          "81/100" in secciones.get("Calidad de la transcripción", "")
          and "Calidad del audio" not in secciones)
    check("Telegram tambien trae el puntaje", "81/100" in re_.resumen_telegram(marcado, 0))
    sin = dict(re_.secciones(re_.aplicar_revision(_json.loads(_json.dumps(datos)), None)))
    check("sin revision dice que no se pudo medir",
          "No se pudo medir" in sin.get("Calidad de la transcripción", ""), str(sin))
    check("una reunion vieja, de antes del revisor, muestra su calidad del audio",
          dict(re_.secciones(datos)).get("Calidad del audio") == "regular")

    from orquestador import skill_runner as sr
    originales = (sr.query, re_.registrar_uso)
    re_.registrar_uso = lambda *a, **kw: None
    try:
        sr.query = _falsear_query(_mensajes_sdk(linea, False))
        r = asyncio.run(re_.revisar_reunion("transcripcion", datos, "slug"))
        check("el revisor devuelve su revision", r == revision, str(r))
        sr.query = _falsear_query(_mensajes_sdk("no dije nada", True, api_error_status=529))
        check("si el revisor falla, no revienta", asyncio.run(re_.revisar_reunion("t", datos, "s")) is None)
    finally:
        sr.query, re_.registrar_uso = originales


def probar_reuniones() -> None:
    """
    Una grabacion marcada como REUNIÓN no pasa por la skill de clases: deja una
    nota en Reuniones/, un .docx sin pisar otro, y sus acciones con fecha futura
    quedan para la secretaria. Modelo, boveda, OneDrive y avisos son falsos.
    """
    print("\n== reuniones ==")
    import asyncio
    import json as _json

    from docx import Document

    from orquestador import evaluaciones as ev
    from orquestador import finalizar_clase as fc
    from orquestador import reuniones as re_
    from orquestador import secretaria as se

    analisis = {
        "titulo": "Entrevista grupal en Empresa Ejemplo", "tipo": "entrevista",
        "una_linea": "Entrevista grupal de práctica en Empresa Ejemplo",
        "calidad_audio": "mala, el teléfono iba en el bolsillo",
        "resumen": "Entrevista grupal con Nombre Apellido, de selección.",
        "decisiones": [],
        "acciones": [
            {"que": "Mandar el CV corregido", "quien": "el estudiante", "cuando": "el lunes",
             "fecha": "2026-10-05", "hora": None, "lugar": None, "presencial": False,
             "cita": "mándame tu cv corregido el lunes"},
            {"que": "Algo que ya pasó", "quien": "el estudiante", "fecha": "2026-09-01"},
            {"que": "Algo sin fecha", "quien": "Nombre Apellido", "fecha": None},
            {"que": "Llamar al gerente", "quien": "el estudiante", "fecha": "2026-10-06",
             "cita": "una frase que nadie dijo en la entrevista"}],
        "seguimientos": [
            {"que": "Avisar si pasa a la segunda etapa", "quien": "la empresa", "cuando": "en una semana",
             "fecha": None, "plazo_dias": 7, "cita": "te respondemos en una semana como mucho"},
            {"que": "Mandar el contrato", "quien": "la empresa", "fecha": "2026-10-02",
             "cita": "promesa que no aparece en la grabación"}],
        "preguntas_abiertas": ["Cuándo avisan el resultado"],
        "entrevista": {"preguntas": [{"pregunta": "Háblame de ti", "tu_respuesta": "Conté mi carrera",
                                      "como_mejorarla": "Partir por el logro principal"}],
                       "impresion_general": "Bien", "proximos_pasos": ["Segunda entrevista"]},
        "investigar": [{"tema": "Empresa Ejemplo", "por_que": "Para la segunda entrevista",
                        "explicacion": "Es una caja de compensación.", "fuentes": ["conocimiento general"]}],
    }
    texto = f"bla bla\n{re_.MARCA_INICIO}\n{_json.dumps(analisis, ensure_ascii=False)}\n{re_.MARCA_FIN}\n"
    transcripcion = ("hola a todos... mándame tu CV corregido el lunes, por favor. Y nosotros te "
                     "respondemos en una semana como mucho.")
    check("lee el analisis entre las marcas", re_.extraer(texto)["titulo"] == analisis["titulo"])
    check("un analisis sin titulo o con JSON roto no se acepta",
          re_.extraer(texto.replace('"titulo"', '"otro"')) is None
          and re_.extraer(f"{re_.MARCA_INICIO}\n{{roto\n{re_.MARCA_FIN}") is None)

    base = Path(tempfile.mkdtemp(prefix="reuniones_"))
    config = {"rutas": {"vault_obsidian": str(base / "vault"), "output": str(base / "Output"),
                        "procesados": str(base / "Procesados")}}
    avisos = []
    originales = (re_.CARPETA_DOCX, re_.notificar_reunion, ev.RUTA)
    re_.CARPETA_DOCX, ev.RUTA = base / "OneDrive", base / "evaluaciones.json"
    re_.notificar_reunion = lambda titulo, texto, docx: avisos.append((titulo, texto, docx))
    ev.guardar({"ramos": {}, "pendientes": []})
    try:
        datos = re_.extraer(texto)
        nota, docx = re_.guardar(datos, "2026-09-29", "Entrevista grupal.m4a", config, transcripcion)
        md = nota.read_text(encoding="utf-8")
        check("deja la nota en Reuniones/ de la boveda, con el nombre de la persona",
              nota.parent.name == "Reuniones" and nota.name.startswith("Reunión - 2026-09-29")
              and "Nombre Apellido" in md, str(nota))
        check("la nota trae acciones, la entrevista y lo que hay que investigar",
              all(s in md for s in ("## Acciones", "## La entrevista", "Sugerencia para mejorarla",
                                    "## Para investigar: Empresa Ejemplo")))
        doc = Document(docx)
        check("y un .docx en su propia carpeta de OneDrive, con su mismo nombre",
              docx.parent.parent == base / "OneDrive" and docx.parent.name == docx.stem
              and doc.paragraphs[0].text == analisis["titulo"], str(docx))
        pendientes = ev.cargar()["pendientes"]
        acciones = [p for p in pendientes if p.get("tipo") != "seguimiento"]
        check("solo la accion con fecha futura y cita en la grabacion queda para la secretaria",
              [p["fecha"] for p in acciones] == ["2026-10-05"] and acciones[0]["origen"] == "reunion",
              str(acciones))
        seguimientos = [p for p in pendientes if p.get("tipo") == "seguimiento"]
        check("una promesa con plazo de la otra parte queda como seguimiento, con la fecha calculada",
              [(p["fecha"], p["que"]) for p in seguimientos]
              == [("2026-10-06", "Avisar si pasa a la segunda etapa (la empresa)")], str(seguimientos))
        check("la accion sin cita en la grabacion queda en la nota marcada para verificar",
              "Llamar al gerente" in md and "verifícala" in md)
        _, docx2 = re_.guardar(re_.extraer(texto), "2026-09-29", "Entrevista grupal.m4a", config, transcripcion)
        check("repetirla no pisa el .docx ni duplica los pendientes",
              docx2.name.endswith("(2).docx") and len(ev.cargar()["pendientes"]) == 2, docx2.name)
        aviso = avisos[0][1]
        check("avisa con una linea de que fue, las acciones con fecha y los seguimientos",
              aviso.startswith("📝 Entrevista grupal de práctica en Empresa Ejemplo")
              and "Mandar el CV corregido - el estudiante, 05-10" in aviso
              and "⏳ Seguimientos\n• Avisar si pasa a la segunda etapa - la empresa, 06-10" in aviso
              and avisos[0][2] == docx, aviso)
        check("sin afirmar lo que no tiene cita ni repetir el resumen largo",
              "Llamar al gerente" not in aviso and "Mandar el contrato" not in aviso
              and analisis["resumen"] not in aviso, aviso)
        texto9, _ = se.resumen(date(2026, 10, 5), ev.cargar(), config)
        check("el dia que toca, la secretaria lo recuerda como de una reunion",
              "de tus reuniones" in (texto9 or "") and "Mandar el CV corregido" in texto9, str(texto9))
        check("y /pendientes lo lista", "Mandar el CV corregido" in se.lista_pendientes(ev.cargar(), date(2026, 10, 1)))

        llamadas = []

        async def reunion_falsa(meta, config, bitacora=None):
            llamadas.append(meta["ramo"])
            return base / "x.docx"

        pend = base / "pendientes"
        pend.mkdir()
        audio = base / "Entrevista grupal.m4a"
        audio.write_bytes(b"audio de mentira")  # si no existe, el flujo la da por ya archivada
        (pend / "s9.json").write_text(_json.dumps({"slug": "s9", "fecha": "2026-09-29", "ramo": re_.RAMO,
                                                   "reconocido": True, "archivos_originales": [str(audio)],
                                                   "archivos": [str(audio)]}), encoding="utf-8")
        original_rr = re_.procesar_reunion
        re_.procesar_reunion = reunion_falsa
        usar_dir_pendientes(pend)
        try:
            asyncio.run(fc.procesar_pendientes_reconocidos(config))
        finally:
            re_.procesar_reunion = original_rr
            usar_dir_pendientes(None)
        check("una grabacion marcada REUNIÓN va al analisis de reuniones, no a la skill de clases",
              llamadas == [re_.RAMO], str(llamadas))
    finally:
        re_.CARPETA_DOCX, re_.notificar_reunion, ev.RUTA = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_seguimientos_de_reuniones() -> None:
    """
    El dia que vence una promesa de una reunion, la secretaria pregunta si se
    cumplio, con Si (cierra), No (pospone una semana) y Descartar. Los botones los
    atiende el bot, que es otro proceso. Telegram y el archivo son falsos.
    """
    print("\n== seguimientos de las reuniones ==")
    from datetime import timedelta

    from orquestador import bot as b
    from orquestador import evaluaciones as ev
    from orquestador import secretaria as se

    base = Path(tempfile.mkdtemp(prefix="seguimientos_"))
    enviados, cambios = [], []
    tg = b.telegram
    originales = (tg.enviar, tg.responder_boton, tg.cambiar_botones, ev.RUTA, b.RUTA_ESTADO, b.cargar_config)
    tg.enviar = lambda texto, chat_id=None, teclado=None: enviados.append((texto, teclado)) or {"message_id": len(enviados)}
    tg.responder_boton = lambda *a: None
    tg.cambiar_botones = lambda mid, teclado=None, chat_id=None: cambios.append((mid, teclado))
    ev.RUTA, b.RUTA_ESTADO, b.cargar_config = base / "evaluaciones.json", base / "estado.json", (lambda: {})
    ev.guardar({"ramos": {}, "pendientes": []})
    hoy = date(2036, 10, 6)

    def tocar(dato, mid):
        b.atender({"callback_query": {"id": "x", "data": dato,
                                      "message": {"chat": {"id": 1}, "message_id": mid}}}, 1)

    def pendiente(que):
        return next(p for p in ev.cargar()["pendientes"] if p["que"] == que)

    try:
        for que, fecha in (("Avisar el resultado (la empresa)", "2036-10-06"),
                           ("Mandar el contrato (la empresa)", "2036-10-04"),
                           ("Llamar de vuelta (la empresa)", "2036-10-20")):
            ev.anotar_pendiente("REUNIÓN", fecha, que, "reunion", clase="2036-09-29",
                                reunion="Entrevista de práctica", tipo="seguimiento")
        ev.anotar_pendiente("REUNIÓN", "2036-10-06", "Mandar el CV", "reunion", clase="2036-09-29",
                            reunion="Entrevista de práctica")
        preguntas = se.seguimientos(ev.cargar(), hoy)
        check("pregunta por lo que vence hoy y por lo vencido sin respuesta, no por lo que viene",
              [t.split("\n")[1] for t, _ in preguntas]
              == ["• Mandar el contrato (la empresa)", "• Avisar el resultado (la empresa)"], str(preguntas))
        botones = [x["text"] for fila in preguntas[0][1]["inline_keyboard"] for x in fila]
        check("con los botones Sí, No y Descartar", botones == ["Sí", "No, pospón 1 semana", "Descartar"], str(botones))
        texto, _ = se.resumen(hoy, ev.cargar(), {"rutas": {"output": str(base)}})
        check("y no repite el seguimiento como tarea para hoy, pero si la accion",
              "Avisar el resultado" not in (texto or "") and "Mandar el CV" in (texto or ""), str(texto))

        datos_boton = lambda i, k: preguntas[i][1]["inline_keyboard"][0][k]["callback_data"]
        tocar(datos_boton(1, 0), 10)
        check("Sí cierra el seguimiento y le quita los botones al mensaje",
              pendiente("Avisar el resultado (la empresa)")["estado"] == "hecho"
              and cambios[-1] == (10, None) and "Cerrado" in enviados[-1][0], str(enviados[-1:]))
        tocar(datos_boton(0, 1), 11)
        check("No lo pospone una semana",
              pendiente("Mandar el contrato (la empresa)")["fecha"] == "2036-10-11"
              and "Lo pospongo" in enviados[-1][0], str(enviados[-1:]))
        tocar(datos_boton(0, 2), 11)
        check("un botón viejo de un seguimiento ya pospuesto no hace nada",
              "ya no está abierto" in enviados[-1][0] and "estado" not in pendiente("Mandar el contrato (la empresa)"))
        ev.anotar_pendiente("REUNIÓN", "2036-10-01", "Confirmar la fecha (la empresa)", "reunion",
                            clase="2036-09-29", tipo="seguimiento")
        vencido = ev.resolver_seguimiento(ev.clave_pendiente(pendiente("Confirmar la fecha (la empresa)")), "no",
                                          hoy=date(2036, 10, 9))
        check("uno que venció hace días se pospone una semana desde hoy, no desde su fecha",
              vencido["fecha"] == (date(2036, 10, 9) + timedelta(days=7)).isoformat(), str(vencido))
        p = pendiente("Llamar de vuelta (la empresa)")
        tocar(f"seg:x:{ev.clave_pendiente(p)}", 12)
        check("Descartar lo saca de la vista sin borrarlo",
              pendiente("Llamar de vuelta (la empresa)")["estado"] == "descartado"
              and "Llamar de vuelta" not in se.lista_pendientes(ev.cargar(), hoy))
        textos = " ".join(t for t, _ in se.seguimientos(ev.cargar(), date(2036, 10, 30)))
        check("y la secretaria ya no pregunta por lo cerrado ni por lo descartado",
              "Avisar el resultado" not in textos and "Llamar de vuelta" not in textos
              and "Mandar el contrato" in textos, textos)
    finally:
        tg.enviar, tg.responder_boton, tg.cambiar_botones, ev.RUTA, b.RUTA_ESTADO, b.cargar_config = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_memoria_en_la_wiki() -> None:
    """
    Un recuerdo confirmado queda en la wiki de la boveda con su formato: la pagina con
    frontmatter y fuente, una linea en el indice y una entrada al final del registro.
    Nunca borra, nunca toca una nota fuera de la wiki ni las paginas reservadas, y
    rechaza un recuerdo con un dato sensible. La boveda es temporal.
    """
    print("\n== memoria en la wiki ==")
    from orquestador import memoria as me

    boveda = Path(tempfile.mkdtemp(prefix="boveda_"))
    wiki = boveda / "Wiki"
    (wiki / "Temas").mkdir(parents=True)
    (boveda / "Reuniones").mkdir()
    (boveda / "Reuniones" / "Reunión - 2036-10-01 - Entrevista.md").write_text("fuente", encoding="utf-8")
    indice = ("---\ntags:\n  - wiki\n---\n\n# Índice - Wiki\n\n## Temas\n\n### Un ramo\n\n"
              "- [[Elasticidad]] - cuanto cambia la demanda\n\n## Personas y organizaciones\n\n"
              "- [[Empresa Antigua]] - ya estaba\n\n## Síntesis\n")
    (wiki / "Índice - Wiki.md").write_text(indice, encoding="utf-8")
    (wiki / "Registro - Wiki.md").write_text("# Registro - Wiki\n\n## [2036-10-01] ingesta | Algo\n\nNada.\n",
                                             encoding="utf-8")
    hoy = date(2036, 10, 14)
    try:
        ruta = me.guardar_recuerdo(boveda, {"pagina": "Empresa Ejemplo", "tipo": "entidad",
                                            "texto": "Caja de compensación donde postulé a la práctica",
                                            "fuente": "Reunión - 2036-10-01 - Entrevista"}, hoy)
        pagina = ruta.read_text(encoding="utf-8")
        check("crea la página en Personas y organizaciones con el formato de la wiki",
              ruta == wiki / "Personas y organizaciones" / "Empresa Ejemplo.md"
              and pagina.startswith("---\nfecha: 2036-10-14\ntipo: entidad\ntags:\n  - wiki\nfuentes:\n"
                                    '  - "[[Reunión - 2036-10-01 - Entrevista]]"\n---\n')
              and "## Lo que sabemos\n\n- Caja de compensación donde postulé a la práctica. "
                  "([[Reunión - 2036-10-01 - Entrevista]])" in pagina
              and "## Contradicciones y dudas" in pagina, pagina)
        actual = (wiki / "Índice - Wiki.md").read_text(encoding="utf-8")
        check("la anota en su sección del índice, sin tocar lo demás",
              "- [[Empresa Antigua]] - ya estaba\n- [[Empresa Ejemplo]] - Caja de compensación" in actual
              and "- [[Elasticidad]] - cuanto cambia la demanda" in actual, actual)
        registro = (wiki / "Registro - Wiki.md").read_text(encoding="utf-8")
        check("y deja una entrada al final del registro, con el prefijo de la wiki",
              registro.startswith("# Registro - Wiki\n\n## [2036-10-01] ingesta | Algo")
              and registro.rstrip().endswith("sacado de [[Reunión - 2036-10-01 - Entrevista]].")
              and "## [2036-10-14] ingesta | Memoria confirmada por Telegram: Empresa Ejemplo" in registro, registro)

        me.guardar_recuerdo(boveda, {"pagina": "empresa ejemplo", "tipo": "entidad",
                                     "texto": "La segunda etapa es un caso grupal"}, date(2036, 10, 20))
        pagina2 = ruta.read_text(encoding="utf-8")
        check("un recuerdo nuevo de la misma página se agrega sin borrar lo anterior",
              pagina2.count("- Caja de compensación donde postulé") == 1
              and "- La segunda etapa es un caso grupal. (contado por Telegram el 20-10-2036)" in pagina2
              and "actualizada: 2036-10-20" in pagina2 and pagina2.index("caso grupal") < pagina2.index("## Contradicciones"),
              pagina2)
        check("y no la repite en el índice",
              (wiki / "Índice - Wiki.md").read_text(encoding="utf-8").count("[[Empresa Ejemplo]]") == 1)

        me.guardar_recuerdo(boveda, {"pagina": "Costo de oportunidad: idea clave", "tipo": "tema",
                                     "texto": "Lo que dejas de ganar por elegir otra opción"}, hoy)
        actual = (wiki / "Índice - Wiki.md").read_text(encoding="utf-8")
        check("un tema va a Temas, con un nombre que Obsidian admite, y en el índice antes de las secciones de ramo",
              (wiki / "Temas" / "Costo de oportunidad idea clave.md").is_file()
              and actual.index("[[Costo de oportunidad idea clave]]") < actual.index("### Un ramo"), actual)

        antes = {p: p.read_text(encoding="utf-8") for p in boveda.rglob("*.md")}
        rechazados = [
            {"pagina": "Alguien", "tipo": "entidad", "texto": "Su correo es alguien@example.com"},
            {"pagina": "Alguien", "tipo": "entidad", "texto": "Su RUT es 12.345.678-9"},
            {"pagina": "Alguien", "tipo": "entidad", "texto": "Mi cuenta es la 00-000-00000-00"},
            {"pagina": "Perfil de alguien", "tipo": "tema", "texto": "algo"},
            {"pagina": "Índice - Wiki", "tipo": "tema", "texto": "algo"},
            {"pagina": "Reunión - 2036-10-01 - Entrevista", "tipo": "entidad", "texto": "algo"},
            {"pagina": "Algo", "tipo": "otra cosa", "texto": "algo"},
            {"pagina": "Algo", "tipo": "tema", "texto": ""},
        ]
        fallaron = []
        for r in rechazados:
            try:
                me.guardar_recuerdo(boveda, r, hoy)
            except me.RecuerdoInvalido:
                continue
            fallaron.append(r)
        check("rechaza datos sensibles, páginas reservadas, notas fuera de la wiki y recuerdos mal formados",
              fallaron == [], str(fallaron))
        check("y al rechazar no escribe nada",
              antes == {p: p.read_text(encoding="utf-8") for p in boveda.rglob("*.md")})
        check("un monto con puntos no es un dato sensible",
              me.revisar({"pagina": "P&G", "tipo": "entidad", "texto": "Ahorró 10.000.000.000 de dólares"})["texto"]
              .startswith("Ahorró"))
    finally:
        shutil.rmtree(boveda, ignore_errors=True)


def probar_reunion_ofrece_memoria() -> None:
    """
    Al cerrar una reunion, se ofrece guardar en la wiki quien era la organizacion y que
    se hablo, con la nota de la reunion como fuente. Lo guarda el bot solo si el
    estudiante toca Si. Un recuerdo con un dato sensible ni se ofrece. Boveda, OneDrive,
    Telegram y archivo de fechas son falsos.
    """
    print("\n== una reunion ofrece guardar en la memoria ==")
    from orquestador import bot as b
    from orquestador import evaluaciones as ev
    from orquestador import reuniones as re_

    base = Path(tempfile.mkdtemp(prefix="reunion_memoria_"))
    wiki = base / "vault" / "Wiki"
    wiki.mkdir(parents=True)
    (wiki / "Índice - Wiki.md").write_text("# Índice - Wiki\n\n## Personas y organizaciones\n", encoding="utf-8")
    (wiki / "Registro - Wiki.md").write_text("# Registro - Wiki\n", encoding="utf-8")
    config = {"rutas": {"vault_obsidian": str(base / "vault"), "output": str(base / "Output")}}
    ofertas, enviados, cambios = [], [], []
    tg = b.telegram
    originales = (re_.CARPETA_DOCX, re_.CARPETA_PROPUESTAS, re_.notificar_reunion, re_.notificar_memoria,
                  ev.RUTA, tg.enviar, tg.responder_boton, tg.cambiar_botones, b.RUTA_ESTADO, b.cargar_config)
    re_.CARPETA_DOCX, re_.CARPETA_PROPUESTAS = base / "OneDrive", base / "propuestas"
    re_.notificar_reunion = lambda *a: None
    re_.notificar_memoria = lambda r, clave: ofertas.append((r, clave))
    ev.RUTA, b.RUTA_ESTADO, b.cargar_config = base / "evaluaciones.json", base / "estado.json", (lambda: config)
    tg.enviar = lambda texto, chat_id=None, teclado=None: enviados.append(texto) or {"message_id": len(enviados)}
    tg.responder_boton = lambda *a: None
    tg.cambiar_botones = lambda mid, teclado=None, chat_id=None: cambios.append((mid, teclado))
    ev.guardar({"ramos": {}, "pendientes": []})

    def tocar(dato):
        b.atender({"callback_query": {"id": "x", "data": dato, "message": {"chat": {"id": 1}, "message_id": 7}}}, 1)

    try:
        datos = {"titulo": "Entrevista de práctica", "resumen": "r", "decisiones": [], "acciones": [],
                 "preguntas_abiertas": [], "investigar": [], "entrevista": None,
                 "memoria": [
                     {"pagina": "Empresa Ejemplo", "tipo_pagina": "entidad",
                      "texto": "Caja de compensación. La entrevistadora de selección explicó que la práctica es en el área de datos"},
                     {"pagina": "Empresa Ejemplo", "tipo_pagina": "entidad",
                      "texto": "El contacto es alguien@example.com"}]}
        nota, _ = re_.guardar(datos, "2036-10-01", "Entrevista.m4a", config, "transcripción")
        check("ofrece guardar la organización, con la nota de la reunión como fuente",
              len(ofertas) == 1 and ofertas[0][0]["pagina"] == "Empresa Ejemplo"
              and ofertas[0][0]["fuente"] == nota.stem, str(ofertas))
        check("y el recuerdo con un dato sensible ni se ofrece",
              not any("example.com" in r["texto"] for r, _ in ofertas))
        check("sin tocar el botón no se guarda nada", not (wiki / "Personas y organizaciones").exists())
        clave = ofertas[0][1]
        tocar(f"mem:si:{clave}")
        pagina = wiki / "Personas y organizaciones" / "Empresa Ejemplo.md"
        check("con Sí queda en la wiki, citando la nota de la reunión",
              pagina.is_file() and f"[[{nota.stem}]]" in pagina.read_text(encoding="utf-8")
              and "Guardado en tu memoria" in enviados[-1] and cambios[-1] == (7, None), str(enviados[-1:]))
        tocar(f"mem:si:{clave}")
        check("tocarlo de nuevo no lo guarda dos veces",
              "ya no está vigente" in enviados[-1]
              and pagina.read_text(encoding="utf-8").count("área de datos") == 2)  # resumen y viñeta
        tocar("mem:si:../../etc")
        check("una clave que no es de una propuesta no lee nada", "ya no está vigente" in enviados[-1])

        ofertas.clear()
        re_.guardar(dict(datos, titulo="Otra entrevista"), "2036-10-02", "Otra.m4a", config, "transcripción")
        tocar(f"mem:no:{ofertas[0][1]}")
        check("con No no se guarda nada",
              "no guardé nada" in enviados[-1] and "[[Reunión - 2036-10-02" not in pagina.read_text(encoding="utf-8"))
        ofertas.clear()
        re_.guardar(dict(datos, titulo="En ensayo"), "2036-10-03", "x.m4a", dict(config, **{ensayo.CLAVE: True}),
                    "transcripción")
        check("en un ensayo no se ofrece nada", ofertas == [])
    finally:
        (re_.CARPETA_DOCX, re_.CARPETA_PROPUESTAS, re_.notificar_reunion, re_.notificar_memoria,
         ev.RUTA, tg.enviar, tg.responder_boton, tg.cambiar_botones, b.RUTA_ESTADO, b.cargar_config) = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_fecha_que_trae_el_audio() -> None:
    """
    Sin fecha en el nombre, la fecha de la clase sale de la que trae el propio audio y no
    del archivo en disco, que en OneDrive es la hora de subida. El audio la trae en UTC, y
    una clase vespertina aqui ya es el dia siguiente en UTC. Audios reales hechos con
    ffmpeg, que es lo que usa el sistema para leerlos.
    """
    print("\n== la fecha que trae el audio ==")
    import os
    import shutil as _sh
    from datetime import datetime as _dt

    if not _sh.which("ffmpeg") or not _sh.which("ffprobe"):
        print("  (sin ffmpeg en este Mac: se omite)")
        return
    base = Path(tempfile.mkdtemp(prefix="fecha_audio_"))
    subida = _dt(2026, 10, 14, 13, 0).timestamp()

    def audio(nombre, creacion=None):
        ruta = base / nombre
        extra = ["-metadata", f"creation_time={creacion}"] if creacion else ["-map_metadata", "-1"]
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.2",
                        "-c:a", "aac", *extra, str(ruta)], check=True)
        os.utime(ruta, (subida, subida))  # como si OneDrive la hubiera bajado hoy
        return ruta

    try:
        nota_de_voz = audio("Grabación nueva 3.m4a", "2026-10-08T00:30:00.000000Z")
        grupos = deteccion.agrupar_por_fecha([nota_de_voz])
        check("una nota de voz sin fecha en el nombre cae el día que se grabó, en la hora de este Mac",
              list(grupos) == [date(2026, 10, 7)], str(grupos))
        momento = deteccion.momento_de_grabacion(nota_de_voz)
        check("y se sabe a qué hora empezó", momento is not None and (momento.hour, momento.minute) == (21, 30),
              str(momento))
        con_nombre = audio("Clase 06.10.26.m4a", "2026-10-08T00:30:00.000000Z")
        check("la fecha del nombre sigue mandando", list(deteccion.agrupar_por_fecha([con_nombre])) == [date(2026, 10, 6)])
        sin_nada = audio("Grabación nueva 4.m4a")
        check("sin fecha en el nombre ni en el audio, queda la del archivo",
              list(deteccion.agrupar_por_fecha([sin_nada])) == [date(2026, 10, 14)])
        futura = audio("Grabación nueva 5.m4a", "2099-01-01T00:00:00.000000Z")
        check("una fecha imposible en el audio no se cree", deteccion.momento_de_grabacion(futura) is None)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def probar_pregunta_de_ramo_por_telegram() -> None:
    """
    Una grabacion cuyo nombre dice el ramo y la fecha se procesa sin preguntar. Cualquier
    otra duda se pregunta por Telegram con un boton por ramo, Reunion y No es una clase, y
    la grabacion espera sin procesarse. La respuesta la anota el bot, y la corrida
    siguiente la procesa. Se recuerda una sola vez. La ventana del Mac no aparece en
    ningun camino. Telegram, Whisper y el lanzamiento del procesamiento son falsos.
    """
    print("\n== la pregunta de ramo va por Telegram ==")
    import json
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from orquestador import bot as b
    import importlib.util

    from orquestador import pregunta_ramo as pr
    from orquestador import procesar_input as pi
    from orquestador import transcripcion

    base = Path(tempfile.mkdtemp(prefix="pregunta_ramo_"))
    entrada = base / "Input"
    entrada.mkdir()
    config = {"rutas": {"input": str(entrada), "procesados": str(base / "Procesados"), "output": str(base / "Output")},
              "semestre": {"fecha_inicio": "2026-08-03"},
              "ramos": {"lunes": {"nombre": "MICROECONOMIA AVANZADA", "perfil_whisper": "es-chile"}},
              "ramos_adicionales": {"REUNIÓN": {"perfil_whisper": "es-chile", "contexto": ""}}}

    def trabajo(nombre, fecha, ramo_por_dia=None):
        ruta = entrada / nombre
        ruta.write_bytes(b"audio")
        return {"clave": f"{fecha}|{nombre}", "fecha": fecha, "dia_semana": "lunes", "archivos": [str(ruta)],
                "reconocido": ramo_por_dia is not None, "ramo": ramo_por_dia, "perfil_whisper": "es-chile",
                "numero_clase": 1}

    claro = trabajo("Microeconomia avanzada 05.10.26.m4a", "2026-10-05")
    del_dia = trabajo("Grabación nueva 7.m4a", "2026-10-05", "MICROECONOMIA AVANZADA")
    raro = trabajo("Audio 12.m4a", "2026-10-07")
    trabajos = [claro, del_dia, raro]
    avisos, transcritos, enviados, cambios, lanzados = [], [], [], [], []
    telegram_ok = [True]
    tg = b.telegram
    originales = (pr.RUTA, tg.avisar, tg.enviar, tg.responder_boton, tg.cambiar_botones, b.lanzar_procesamiento,
                  b.RUTA_ESTADO, b.cargar_config, transcripcion.construir_trabajos,
                  transcripcion.transcribir_trabajo, transcripcion.notificar_aviso, deteccion.TRABAJOS_EMITIDOS_PATH)
    pr.RUTA = base / "preguntas.json"
    tg.avisar = lambda texto, adjunto=None, teclado=None: telegram_ok[0] and (avisos.append((texto, teclado)) or True)
    tg.enviar = lambda texto, chat_id=None, teclado=None: enviados.append(texto) or {"message_id": 1}
    tg.responder_boton = lambda *a: None
    tg.cambiar_botones = lambda mid, teclado=None, chat_id=None: cambios.append((mid, teclado))
    b.lanzar_procesamiento = lambda: lanzados.append(1)
    b.RUTA_ESTADO, b.cargar_config = base / "estado.json", (lambda: config)
    transcripcion.construir_trabajos = lambda c: [dict(t) for t in trabajos]
    transcripcion.transcribir_trabajo = lambda t, c: transcritos.append(Path(t["archivos"][0]).name) or "texto"
    transcripcion.notificar_aviso = lambda *a: None
    deteccion.TRABAJOS_EMITIDOS_PATH = base / "emitidos.json"
    usar_dir_pendientes(base / "pendientes")

    def tocar(dato):
        b.atender({"callback_query": {"id": "x", "data": dato, "message": {"chat": {"id": 1}, "message_id": 5}}}, 1)

    def botones(teclado):
        return [x["text"] for fila in teclado["inline_keyboard"] for x in fila]

    try:
        transcripcion.procesar_pendientes(config)
        check("con el ramo y la fecha en el nombre se procesa sin preguntar",
              transcritos == ["Microeconomia avanzada 05.10.26.m4a"], str(transcritos))
        check("lo demás se pregunta por Telegram, y espera sin procesarse",
              len(avisos) == 2 and "Grabación nueva 7.m4a" not in transcritos and "Audio 12.m4a" not in transcritos)
        texto, teclado = avisos[0]
        check("con un botón por ramo, Reunión y No es una clase",
              botones(teclado) == ["Microeconomia Avanzada", "Reunión", "No es una clase"], str(botones(teclado)))
        check("y si el día sugiere un ramo, lo dice", "👉 Por el día, parece de Microeconomia Avanzada" in texto, texto)
        check("la que espera queda marcada como esperando", pr.esperando(Path(del_dia["archivos"][0])))

        transcripcion.procesar_pendientes(config)
        check("una corrida siguiente no vuelve a preguntar", len(avisos) == 2)

        id_del_dia = next(p["id"] for p in json.loads(pr.RUTA.read_text()).values() if "nueva 7" in p["clave"])
        id_raro = next(p["id"] for p in json.loads(pr.RUTA.read_text()).values() if "Audio 12" in p["clave"])
        tocar(f"grab:{id_del_dia}:1")
        check("el bot anota la respuesta, quita los botones y relanza el procesamiento",
              "como una reunión" in enviados[-1] and cambios[-1] == (5, None) and lanzados == [1]
              and not pr.esperando(Path(del_dia["archivos"][0])), str(enviados[-1:]))
        tocar(f"grab:{id_del_dia}:0")
        check("tocar otro botón después no cambia la respuesta", "ya tiene respuesta" in enviados[-1] and lanzados == [1])

        transcripcion.procesar_pendientes(config)
        meta = [json.loads(m.read_text()) for m in (base / "pendientes").glob("*.json")]
        check("la corrida siguiente la procesa con lo que respondió",
              "Grabación nueva 7.m4a" in transcritos and any(m["ramo"] == "REUNIÓN" for m in meta), str(meta))

        tocar(f"grab:{id_raro}:2")
        transcripcion.procesar_pendientes(config)
        check("No es una clase la transcribe sin tratarla como clase",
              "Audio 12.m4a" in transcritos and (base / "Output" / "Sin clasificar").is_dir()
              and not Path(raro["archivos"][0]).exists())
        check("la ventana del Mac ya no existe en ningún camino",
              importlib.util.find_spec("orquestador.pantalla_confirmacion") is None
              and importlib.util.find_spec("orquestador.dialogo_no_reconocido") is None)

        # Recordatorio: una vez, solo pasado el plazo, y no si la grabacion ya no esta.
        tarde = trabajo("Grabación nueva 9.m4a", "2026-10-12")
        trabajos[:] = [tarde]
        transcripcion.procesar_pendientes(config)
        antes = len(avisos)
        check("no recuerda antes de tiempo", pr.recordar(_dt.now()) == 0)
        check("pasado el plazo la recuerda una vez, con los mismos botones",
              pr.recordar(_dt.now() + _td(hours=4)) == 1 and avisos[-1][0].startswith("⏰ Sigue esperando")
              and botones(avisos[-1][1]) == botones(avisos[antes - 1][1]))
        check("y no la vuelve a recordar", pr.recordar(_dt.now() + _td(hours=30)) == 0)

        telegram_ok[0] = False
        sin_red = trabajo("Grabación nueva 10.m4a", "2026-10-12")
        trabajos[:] = [sin_red]
        transcripcion.procesar_pendientes(config)
        check("si la pregunta no se pudo enviar, no queda esperando una respuesta que no llegará",
              not pr.esperando(Path(sin_red["archivos"][0])))

        # Si lo unico en Input espera respuesta, la corrida termina sin avisar "copiando".
        mac = []
        orig_pi = (pi.lock.adquirir_no_bloqueante, pi.lock.liberar, pi.cargar_config, pi.notificar_aviso,
                   pi.notificar_inicio, pi.estado_vivo.limpiar)
        pi.lock.adquirir_no_bloqueante, pi.lock.liberar = (lambda: object()), (lambda f: None)
        pi.cargar_config, pi.notificar_aviso = (lambda: config), (lambda t, m: mac.append(t))
        pi.notificar_inicio, pi.estado_vivo.limpiar = (lambda n: mac.append("inicio")), (lambda: None)
        try:
            for audio in entrada.iterdir():  # solo queda la que espera respuesta
                if audio.name != "Grabación nueva 9.m4a":
                    audio.unlink()  # archivo de prueba en una carpeta temporal
            pi.main()
            silencio = list(mac)
            pi.main(avisar_si_espera=True)
        finally:
            (pi.lock.adquirir_no_bloqueante, pi.lock.liberar, pi.cargar_config, pi.notificar_aviso,
             pi.notificar_inicio, pi.estado_vivo.limpiar) = orig_pi
        check("si todo espera respuesta, la corrida automática termina en silencio", silencio == [], str(silencio))
        check("y un clic en la app dice que espera tu respuesta", mac == ["Esperando tu respuesta"], str(mac))
    finally:
        (pr.RUTA, tg.avisar, tg.enviar, tg.responder_boton, tg.cambiar_botones, b.lanzar_procesamiento,
         b.RUTA_ESTADO, b.cargar_config, transcripcion.construir_trabajos,
         transcripcion.transcribir_trabajo, transcripcion.notificar_aviso, deteccion.TRABAJOS_EMITIDOS_PATH) = originales
        usar_dir_pendientes(None)
        shutil.rmtree(base, ignore_errors=True)


def probar_ramo_propuesto_por_la_hora() -> None:
    """
    Si el nombre no dice el ramo, la hora en que empezo la grabacion se cruza con el
    horario: si cae en una clase, se propone ese ramo con un primer boton para
    confirmarlo. Si a esa hora no habia clase, la pregunta es la general. Nada se
    procesa antes de la respuesta. Telegram y la hora del audio son falsos.
    """
    print("\n== el ramo se propone por la hora de grabacion ==")
    import json
    from datetime import datetime as _dt

    from orquestador import pregunta_ramo as pr

    base = Path(tempfile.mkdtemp(prefix="ramo_hora_"))
    config = {"ramos": {"lunes": {"nombre": "MICROECONOMIA AVANZADA", "inicio": "19:00", "fin": "22:00"},
                        "martes": {"nombre": "FINANZAS CORPORATIVAS", "inicio": "19:00", "fin": "22:00"}},
              "ramos_adicionales": {"REUNIÓN": {}}}
    horas = {}
    avisos = []
    originales = (pr.RUTA, pr.telegram.avisar, pr.deteccion.momento_de_grabacion)
    pr.RUTA = base / "preguntas.json"
    pr.telegram.avisar = lambda texto, adjunto=None, teclado=None: avisos.append((texto, teclado)) or True
    pr.deteccion.momento_de_grabacion = lambda ruta: horas.get(Path(ruta).name)

    def trabajo(nombre, fecha, momento):
        horas[nombre] = momento
        return {"clave": f"{fecha}|{nombre}", "fecha": fecha, "archivos": [str(base / nombre)],
                "reconocido": True, "ramo": "MICROECONOMIA AVANZADA"}

    def textos(teclado):
        return [x["text"] for fila in teclado["inline_keyboard"] for x in fila]

    try:
        check("una grabación que empieza a las 18:40 de un lunes es de la clase del lunes",
              pr.ramo_por_horario(_dt(2026, 10, 5, 18, 40), config) == "MICROECONOMIA AVANZADA")
        check("una de las 13:00 no es de ninguna clase", pr.ramo_por_horario(_dt(2026, 10, 5, 13, 0), config) is None)

        decisiones = pr.preguntar([trabajo("Grabación nueva 1.m4a", "2026-10-06", _dt(2026, 10, 6, 19, 10))], config)
        texto, teclado = avisos[-1]
        check("si la hora cae en una clase, propone ese ramo con un primer botón para confirmarlo",
              "Empezó durante tu clase de Finanzas Corporativas" in texto
              and textos(teclado)[0] == "✅ Sí, es de Finanzas Corporativas"
              and textos(teclado)[1:] == ["Microeconomia Avanzada", "Reunión", "No es una clase"], str(textos(teclado)))
        check("y no la procesa antes de la respuesta", decisiones == {})
        id_ = json.loads(pr.RUTA.read_text())["2026-10-06|Grabación nueva 1.m4a"]["id"]
        dato = teclado["inline_keyboard"][0][0]["callback_data"]
        check("el botón de confirmar responde ese ramo",
              pr.responder(id_, int(dato.split(":")[2]))["respuesta"] == {"que_hacer": "procesar", "ramo": "FINANZAS CORPORATIVAS"})

        pr.preguntar([trabajo("Grabación nueva 2.m4a", "2026-10-05", _dt(2026, 10, 5, 13, 0))], config)
        texto, teclado = avisos[-1]
        check("si a esa hora no había clase, la pregunta es la general, sin sugerir el ramo del día",
              "👉" not in texto and textos(teclado)[0] == "Microeconomia Avanzada", texto)

        pr.preguntar([trabajo("Entrevista con la empresa.m4a", "2026-10-05", _dt(2026, 10, 5, 19, 30))], config)
        check("un nombre que dice otra cosa no se propone como clase, aunque la hora calce", "👉" not in avisos[-1][0])
    finally:
        pr.RUTA, pr.telegram.avisar, pr.deteccion.momento_de_grabacion = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_vigilante_de_input_y_onedrive() -> None:
    """
    El vigilante toma lo que llega a la carpeta de grabaciones de OneDrive y lo que se
    deja en Input, ademas de AirDrop. De OneDrive copia la grabacion completa a Input y
    aparta el original, sin borrarlo. Una grabacion que sigue creciendo no se toma. En
    Input solo dispara una corrida lo nuevo, nunca lo que ya fallo ni lo que espera una
    respuesta. Carpetas temporales, y el procesamiento es falso.
    """
    print("\n== el vigilante toma Input y OneDrive ==")
    import json

    from orquestador import pregunta_ramo as pr
    from orquestador import vigilar_descargas as vd

    base = Path(tempfile.mkdtemp(prefix="vigilante_"))
    entrada, nube, descargas = base / "Input", base / "Nube", base / "Descargas"
    for c in (entrada, nube, descargas):
        c.mkdir()
    config = {"rutas": {"input": str(entrada), "grabaciones_onedrive": str(nube)}}
    corridas = []
    originales = (vd.cargar_config, vd.DESCARGAS, vd.ESPERA_TAMANO_SEGUNDOS, vd.procesar_input.main,
                  vd.lock.adquirir_bloqueante, vd.lock.liberar, deteccion.ESTADO_TAMANOS_PATH, pr.RUTA, vd.YA_TOMADAS,
                  vd.time.sleep)
    vd.YA_TOMADAS = base / "para borrar"
    vd.cargar_config, vd.DESCARGAS, vd.ESPERA_TAMANO_SEGUNDOS = (lambda: config), descargas, 0
    vd.procesar_input.main = lambda: corridas.append(sorted(p.name for p in entrada.iterdir()))
    vd.lock.adquirir_bloqueante, vd.lock.liberar = (lambda: None), (lambda f: None)
    deteccion.ESTADO_TAMANOS_PATH, pr.RUTA = base / "tamanos.json", base / "preguntas.json"
    try:
        (nube / "Clase del martes.m4a").write_bytes(b"audio completo")
        (nube / "notas.txt").write_text("no es audio")
        vd.main()
        check("una grabación de OneDrive se copia entera a Input y se procesa",
              (entrada / "Clase del martes.m4a").read_bytes() == b"audio completo"
              and corridas == [["Clase del martes.m4a"]], str(corridas))
        check("el original queda en archivos para borrar, no borrado, y lo demás no se toca",
              (vd.YA_TOMADAS / "Clase del martes.m4a").is_file() and (nube / "notas.txt").is_file()
              and not (nube / "Clase del martes.m4a").exists())

        creciendo = nube / "Subiendo.m4a"
        creciendo.write_bytes(b"a")
        vd.time.sleep = lambda s: creciendo.write_bytes(creciendo.read_bytes() + b"mas")
        vd.main()
        vd.time.sleep = originales[-1]  # el sleep de verdad
        check("una grabación que sigue creciendo no se toma",
              creciendo.exists() and not (entrada / "Subiendo.m4a").exists())

        corridas.clear()
        vd.main()
        check("cuando deja de crecer, se toma", corridas == [["Clase del martes.m4a", "Subiendo.m4a"]], str(corridas))

        # Lo que vio la ultima corrida, como lo deja deteccion.actualizar_estables.
        deteccion.actualizar_estables(entrada)
        corridas.clear()
        vd.main()
        check("lo que ya estaba en Input en la corrida anterior no dispara otra (una clase que falló)",
              corridas == [], str(corridas))

        (entrada / "Dejada a mano.m4a").write_bytes(b"audio")
        vd.main()
        check("un audio dejado a mano en Input se procesa sin abrir la app",
              len(corridas) == 1 and "Dejada a mano.m4a" in corridas[0], str(corridas))

        corridas.clear()
        (entrada / "Esperando.m4a").write_bytes(b"audio")
        pr.RUTA.write_text(json.dumps({"k": {"archivos": [str(entrada / "Esperando.m4a")]}}))
        deteccion.actualizar_estables(entrada)
        (entrada / "Esperando.m4a").write_bytes(b"audio que cambio")
        vd.main()
        check("ni lo que espera una respuesta por Telegram", corridas == [], str(corridas))

        sin_nube = {"rutas": {"input": str(entrada)}}
        vd.cargar_config = lambda: sin_nube
        vd.main()
        check("sin carpeta de OneDrive configurada sigue funcionando", True)
    finally:
        (vd.cargar_config, vd.DESCARGAS, vd.ESPERA_TAMANO_SEGUNDOS, vd.procesar_input.main,
         vd.lock.adquirir_bloqueante, vd.lock.liberar, deteccion.ESTADO_TAMANOS_PATH, pr.RUTA, vd.YA_TOMADAS,
         vd.time.sleep) = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_enlace_de_calendario() -> None:
    """
    Un evento que sale de una reunion se ofrece con un boton que abre Google
    Calendar ya lleno. Solo para acciones marcadas como evento y con fecha: una
    tarea no va al calendario, y sin fecha no se inventa una.
    """
    print("\n== enlace de Google Calendar ==")
    import urllib.parse

    from orquestador import evaluaciones as ev
    from orquestador import reuniones as re_

    def params(url):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))

    con_hora = {"que": "Segunda entrevista", "fecha": "2026-10-08", "hora": "10:00", "lugar": "Oficina central",
                "presencial": True, "evento": True, "cita": "nos vemos el jueves ocho a las diez"}
    p = params(re_.enlace_calendario(con_hora, "Entrevista grupal"))
    check("con hora, dura una hora en la zona horaria de Santiago",
          p["dates"] == "20261008T100000/20261008T110000" and p["ctz"] == "America/Santiago"
          and p["text"] == "Segunda entrevista" and p["location"] == "Oficina central", str(p))
    check("si hay que moverse, lo recuerda en el detalle", "2 horas antes" in p["details"])
    check("siempre de color Mandarina", p.get("pprop") == "eventColor:6", str(p))
    p_linea = params(re_.enlace_calendario(dict(con_hora, presencial=False, lugar="Zoom"), "x"))
    check("sin lugar si no hay que ir a ninguna parte", "location" not in p_linea, str(p_linea))
    p = params(re_.enlace_calendario(dict(con_hora, hora=None), "Entrevista grupal"))
    check("sin hora, es de dia completo", p["dates"] == "20261008/20261009", str(p))
    check("sin fecha no hay enlace", re_.enlace_calendario(dict(con_hora, fecha=None), "x") is None)

    base = Path(tempfile.mkdtemp(prefix="calendario_"))
    config = {"rutas": {"vault_obsidian": str(base / "vault"), "output": str(base / "Output")}}
    eventos = []
    originales = (re_.CARPETA_DOCX, re_.notificar_reunion, re_.notificar_evento, ev.RUTA)
    re_.CARPETA_DOCX, ev.RUTA = base / "OneDrive", base / "evaluaciones.json"
    re_.notificar_reunion = lambda *a: None
    re_.notificar_evento = lambda que, cuando, url, lugar="": eventos.append((que, url))
    ev.guardar({"ramos": {}, "pendientes": []})
    try:
        datos = {"titulo": "Entrevista", "resumen": "r", "decisiones": [], "preguntas_abiertas": [],
                 "investigar": [], "entrevista": None,
                 "acciones": [con_hora,
                              {"que": "Mandar el CV", "fecha": "2026-10-05", "evento": False},
                              {"que": "Llamada sin fecha", "fecha": None, "evento": True}]}
        dicho = "y bueno nos vemos el jueves ocho a las diez entonces"
        re_.guardar(datos, "2026-09-29", "Entrevista.m4a", config, dicho)
        check("solo el evento con fecha se ofrece para el calendario",
              [q for q, _ in eventos] == ["Segunda entrevista"], str(eventos))
        eventos.clear()
        re_.guardar(datos, "2026-09-29", "Entrevista.m4a", dict(config, **{ensayo.CLAVE: True}), dicho)
        check("en ensayo no ofrece eventos", eventos == [])
    finally:
        re_.CARPETA_DOCX, re_.notificar_reunion, re_.notificar_evento, ev.RUTA = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_conectores_apagados() -> None:
    """Toda llamada al modelo apaga los conectores de claude.ai y no lleva el token
    de setup-token, que el SDK no usa y que impedia renovar la sesion (06-10-2026)."""
    print("\n== conectores apagados y sin token ==")
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions

    from orquestador import skill_runner as sr

    vistas = []

    async def query_que_mira(*a, **kw):
        vistas.append(kw["options"].env)
        for m in _mensajes_sdk("ok", False):
            yield m

    async def correr():
        async for _ in sr.consultar("hola", ClaudeAgentOptions(env={"OTRA": "1"})):
            pass

    original = sr.query
    sr.query = query_que_mira
    try:
        asyncio.run(correr())
    finally:
        sr.query = original
    check("apaga los conectores sin perder el resto del entorno",
          vistas == [{"OTRA": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}], str(vistas))


def probar_reintento_de_credencial() -> None:
    """Si Claude rechaza la credencial, se intenta una segunda vez sin que quien llama
    vea el intento fallido."""
    print("\n== reintento si Claude rechaza la credencial ==")
    import asyncio

    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock

    from orquestador import skill_runner as sr

    rechazo = [AssistantMessage(content=[TextBlock(text="Failed to authenticate")], model="falso",
                                error="authentication_failed"),
               *_mensajes_sdk("", True, api_error_status=401,
                              result="Failed to authenticate. API Error: 401 OAuth access token has expired")[1:]]
    bien = _mensajes_sdk("ok", False)
    intentos = []

    def por_turnos(*secuencias):
        pendientes = list(secuencias)

        async def _query(*a, **kw):
            intentos.append(1)
            async for m in _falsear_query(pendientes.pop(0))(*a, **kw):
                yield m
        return _query

    async def recibir():
        return [m async for m in sr.consultar("hola", ClaudeAgentOptions())]

    originales = (sr.query, sr.SEGUNDOS_ANTES_DE_REINTENTAR)
    sr.SEGUNDOS_ANTES_DE_REINTENTAR = 0
    try:
        sr.query = por_turnos(rechazo, bien)
        recibidos = asyncio.run(recibir())
        check("si rechaza la credencial, la respuesta llega igual al segundo intento",
              [type(m).__name__ for m in recibidos] == ["AssistantMessage", "ResultMessage"]
              and not recibidos[-1].is_error and len(intentos) == 2, str(recibidos))

        intentos.clear()
        sr.query = por_turnos(bien)
        asyncio.run(recibir())
        check("si la acepta, hay un solo intento", len(intentos) == 1, str(intentos))

        sr.query = por_turnos(rechazo, rechazo)
        recibidos = asyncio.run(recibir())
        check("si la rechaza dos veces, quien llama recibe el error para explicarlo",
              recibidos and recibidos[-1].is_error and recibidos[-1].api_error_status == 401, str(recibidos))
    finally:
        sr.query, sr.SEGUNDOS_ANTES_DE_REINTENTAR = originales


def probar_tramo_vacio() -> None:
    """
    Una racha como "....." o "100000" en una sola frase no puede borrar un tramo de
    20 minutos (clase del 07-10-2026), y un tramo vacio se reintenta una vez.
    """
    print("\n== tramo vacio: filtro de Transcriptotem y reintento ==")
    from orquestador import transcripcion as tr
    from orquestador.config import cargar_config

    # 1. Transcriptotem, por la misma puerta que usa el agente, con Whisper simulado.
    try:
        ruta_tt = cargar_config()["rutas"]["transcriptotem"]
    except Exception:
        ruta_tt = None
    transcribe = None
    if ruta_tt and (Path(ruta_tt) / "backend" / "transcriber.py").exists():
        try:
            import mlx_whisper

            transcribe = tr._obtener_transcribe(ruta_tt)
        except (ImportError, OSError) as e:
            # Transcriptotem vive en OneDrive: si un archivo quedo solo en la nube
            # y no se puede bajar, esto no es un fallo del agente.
            print(f"  (no se pudo cargar Transcriptotem: {type(e).__name__}, se salta el filtro)")
    else:
        print("  (sin Transcriptotem en este Mac: se salta la parte del filtro)")
    if transcribe:
        modulo = sys.modules["backend.transcriber"]
        # Frases distintas: la misma frase seguida tres veces es un bucle y se recorta.
        frases = [f"Hoy vemos la integración vertical, parte {i}." for i in range(60)] + [
            ".....", "El costo fue de 100000 pesos.", "El coeficiente da 0,891."] + \
            ["Gracias."] * 11 + ["No.", "No."] + \
            [f"La empresa decide qué hacer y qué comprar, caso {i}." for i in range(60)]
        originales = (mlx_whisper.transcribe, modulo._detect_engine)
        opciones = {}
        mlx_whisper.transcribe = lambda *a, **k: opciones.update(k) or {
            "text": " ".join(frases), "segments": [{"text": " " + f} for f in frases]}
        modulo._detect_engine = lambda: "mlx"
        carpeta = Path(tempfile.mkdtemp(prefix="vacio_"))
        audio = carpeta / "tramo.m4a"
        audio.write_bytes(b"x")
        try:
            texto, _, _ = transcribe(str(audio), language_profile="es-chile", context_text="")
        finally:
            mlx_whisper.transcribe, modulo._detect_engine = originales
            shutil.rmtree(carpeta, ignore_errors=True)
        check("una racha en una frase no borra el tramo", len(texto) > 4000, f"{len(texto)} caracteres")
        check("los numeros dichos en clase se conservan",
              "100000 pesos" in texto and "0,891" in texto, texto[-200:])
        check("y la frase basura si se descarta", "....." not in texto)
        check("un bucle de la misma frase queda en dos", texto.count("Gracias.") == 2,
              f"{texto.count('Gracias.')} veces")
        check("pero una frase dicha dos veces se conserva", texto.count("No.") == 2)
        check("Whisper se salta las frases inventadas en los silencios",
              opciones.get("hallucination_silence_threshold") and opciones.get("word_timestamps"), opciones)

    # 2. El agente reintenta una vez un tramo que salio vacio.
    respuestas = ["", "palabra " * 300]
    llamadas = []

    def transcribe_falso(ruta, language_profile, context_text):
        llamadas.append(ruta)
        return respuestas.pop(0), "es", 1

    texto = tr._transcribir_verificando(transcribe_falso, "tramo.m4a", "es-chile", "", 900)
    check("un tramo vacio se reintenta y se salva", len(llamadas) == 2 and len(texto) > 2000)
    respuestas[:] = ["", ""]
    try:
        tr._transcribir_verificando(transcribe_falso, "tramo.m4a", "es-chile", "", 900)
        check("si sale vacio dos veces, se detiene con el aviso", False)
    except ValueError as e:
        check("si sale vacio dos veces, se detiene con el aviso", "también al reintentarla" in str(e))


def probar_salida_cruda_de_whisper() -> None:
    """Los segmentos de Whisper con su confianza se guardan aparte en cada
    transcripcion (18-10-2026), y si no se pueden capturar la clase sigue igual.
    Con un Transcriptotem simulado: no hace falta Whisper ni OneDrive."""
    print("\n== salida cruda de Whisper ==")
    import json
    import types
    from orquestador import transcripcion as tr

    frase = "Hoy vemos los supuestos de MCO y cómo validarlos en R. " * 6
    falso = types.ModuleType("backend.transcriber")
    falso._texto_por_frases = lambda r: "\n".join(s["text"].strip() for s in r["segments"])

    def transcribe(ruta, language_profile, context_text):
        # Como el de verdad: llama a _texto_por_frases por el modulo, no por una copia.
        r = {"segments": [{"start": 0.5, "end": 9.0, "text": " " + frase, "avg_logprob": -0.21,
                           "no_speech_prob": 0.01, "compression_ratio": 1.3,
                           "words": [{"word": " Hoy", "start": 0.5, "end": 0.9, "probability": 0.87654}]}]}
        return sys.modules["backend.transcriber"]._texto_por_frases(r), "es", 1

    paquete = types.ModuleType("backend")
    paquete.transcriber = falso
    antes = {k: sys.modules.get(k) for k in ("backend", "backend.transcriber")}
    tmp = Path(tempfile.mkdtemp())
    try:
        sys.modules.update({"backend": paquete, "backend.transcriber": falso})
        crudos = []
        texto = tr._transcribir_archivo(transcribe, "clase.m4a", "es-chile", "", 60, "x", crudos)
        check("el texto sale igual que antes", texto == frase.strip())
        check("cada segmento trae su tiempo y su confianza",
              crudos and crudos[0]["start"] == 0.5 and crudos[0]["avg_logprob"] == -0.21
              and crudos[0]["no_speech_prob"] == 0.01, crudos)
        check("y cada palabra, compacta, con su probabilidad",
              crudos and crudos[0]["words"] == [[0.5, 0.9, "Hoy", 0.877]], crudos and crudos[0]["words"])
        check("la interceptacion se deshace al terminar", falso._texto_por_frases.__name__ == "<lambda>")

        trozo = []
        tr._anotar_crudos(trozo, [{"start": 1.0, "end": 2.0, "text": "a", "words": [[1.0, 1.5, "a", 0.9]]}],
                          1200.0, canal="Tú")
        check("en un trozo, los tiempos se corren al inicio del trozo, también los de cada palabra",
              trozo == [{"start": 1201.0, "end": 1202.0, "text": "a", "words": [[1201.0, 1201.5, "a", 0.9]],
                         "canal": "Tú"}], trozo)

        usar_dir_pendientes(tmp)
        trabajo = {"clave": "2026-10-15|clase.m4a", "archivos": ["clase.m4a"], "segmentos_whisper": crudos}
        tr._guardar_pendiente(trabajo, texto)
        slug = nombres.slug_pendiente(trabajo["clave"])
        guardado = json.loads((tmp / f"{slug}_whisper.json").read_text(encoding="utf-8"))
        meta = json.loads((tmp / f"{slug}.json").read_text(encoding="utf-8"))
        check("se guardan en <slug>_whisper.json", guardado == crudos)
        check("y no inflan el .json del trabajo", "segmentos_whisper" not in meta)

        # Sin Transcriptotem que interceptar, la transcripcion no se pierde.
        sys.modules["backend.transcriber"] = None
        crudos = []
        texto = tr._transcribir_archivo(lambda ruta, language_profile, context_text: (frase, "es", 1),
                                        "clase.m4a", "es-chile", "", 60, "x", crudos)
        check("si no se pueden capturar, la clase sigue sin segmentos", texto == frase and crudos == [])
    finally:
        usar_dir_pendientes(None)
        for k, v in antes.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def probar_evaluaciones_unico_escritor() -> None:
    """evaluaciones.py es el unico que escribe evaluaciones.json, con candado
    (decision del 08-10-2026). Sin Telegram ni modelo: solo el archivo."""
    print("\n== evaluaciones: unico escritor y candado ==")
    import fcntl
    import json
    import threading

    from orquestador import evaluaciones as ev

    base = Path(tempfile.mkdtemp(prefix="evaluaciones_"))
    originales = (ev.RUTA, ev.SEGUNDOS_CANDADO)
    ev.RUTA = base / "evaluaciones.json"
    try:
        ev.guardar({"ramos": {"RAMO": [{"nombre": "S2", "fecha": "2026-11-09", "fuente": "clase",
                                        "clase": "2026-10-01", "textual": "la solemne es el nueve"}]},
                    "pendientes": [{"ramo": "RAMO", "fecha": "2026-10-20", "que": "Traer el caso",
                                    "textual": "traigan el caso", "clase": "2026-10-13"}],
                    "clases_perdidas": []})

        # 1. Las operaciones, sin Telegram.
        check("los pendientes antiguos reciben su origen al leerse",
              ev.cargar()["pendientes"][0]["origen"] == "clase")
        check("anotar un pendiente lo guarda con su origen",
              ev.anotar_pendiente("RAMO", "2026-10-22", "Control 2", "chat")
              and ev.cargar()["pendientes"][-1] == {"ramo": "RAMO", "fecha": "2026-10-22",
                                                    "que": "Control 2", "origen": "chat"})
        check("repetirlo no lo duplica", ev.anotar_pendiente("RAMO", "2026-10-22", "Control 2", "chat") is False)
        ev.anotar_clase_perdida("RAMO", "2026-10-13")
        ev.anotar_clase_perdida("RAMO", "2026-10-13")
        check("una clase perdida se anota una vez", ev.cargar()["clases_perdidas"] == [{"ramo": "RAMO", "fecha": "2026-10-13"}])
        revisada = ev.revisar_accion_del_chat({"tipo": "fecha_evaluacion", "ramo": "RAMO", "evaluacion": "S2",
                                               "fecha": "2026-11-16"}, ev.cargar())
        check("al revisar una fecha del chat, avisa lo que dijo el profesor",
              revisada["choque"] == {"clase": "2026-10-01", "textual": "la solemne es el nueve"}, str(revisada))
        check("una accion sin sentido no se ofrece",
              ev.revisar_accion_del_chat({"tipo": "pendiente", "ramo": "OTRO", "fecha": "2026-10-22",
                                          "que": "x"}, ev.cargar()) is None)
        ev.fijar_fecha_desde_chat("RAMO", "S2", "2026-11-16")
        s2 = ev.cargar()["ramos"]["RAMO"][0]
        check("fijar la fecha desde el chat la deja como del chat",
              s2["fecha"] == "2026-11-16" and s2["fuente"] == "chat" and "textual" not in s2, str(s2))
        ev.anotar_pendiente("RAMO", "2026-10-30", "Ensayo", "chat", ensayo=True)
        check("en ensayo no se escribe nada", all(p["que"] != "Ensayo" for p in ev.cargar()["pendientes"]))

        # 2. Dos escritores a la vez: ninguno pisa al otro.
        def anotar(i):
            ev.anotar_pendiente("RAMO", "2026-11-01", f"tarea {i}", "chat")

        hilos = [threading.Thread(target=anotar, args=(i,)) for i in range(20)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        tareas = [p for p in ev.cargar()["pendientes"] if p["que"].startswith("tarea ")]
        check("veinte escritores a la vez no pierden ninguna anotacion", len(tareas) == 20, str(len(tareas)))

        # 3. Si otro proceso tiene el candado, no se anota y se avisa.
        ev.SEGUNDOS_CANDADO = 0.2
        with open(ev.RUTA.with_suffix(".lock"), "w") as otro:
            fcntl.flock(otro, fcntl.LOCK_EX)
            try:
                ev.anotar_pendiente("RAMO", "2026-11-02", "bloqueado", "chat")
                check("con el candado ocupado se detiene", False)
            except ev.CandadoOcupado:
                check("con el candado ocupado se detiene", True)
            fcntl.flock(otro, fcntl.LOCK_UN)
        check("y no deja nada a medias", json.loads(ev.RUTA.read_text())["pendientes"][-1]["que"] != "bloqueado")
    finally:
        ev.RUTA, ev.SEGUNDOS_CANDADO = originales
        shutil.rmtree(base, ignore_errors=True)


def probar_descarga_de_onedrive_encendida() -> None:
    """Un LaunchAgent arranca con la descarga de archivos en la nube apagada (1).
    Importar el paquete tiene que encenderla (2), o una clase falla con Errno 11."""
    print("\n== descarga de archivos de OneDrive desde launchd ==")
    import ctypes
    import importlib

    import orquestador

    libc = ctypes.CDLL(None)
    libc.setiopolicy_np(3, 0, 1)  # como la deja launchd
    importlib.reload(orquestador)
    check("al importar, la descarga queda encendida", libc.getiopolicy_np(3, 0) == 2)


def probar_pre_commit_frena_rutas_del_mac() -> None:
    """El pre-commit frena una ruta con el usuario real del Mac, porque el repo
    es publico, y deja pasar el ejemplo /Users/tu_usuario."""
    print("\n== el pre-commit frena rutas de este Mac ==")
    import os
    import subprocess as sp

    hook = RAIZ / ".githooks" / "pre-commit"
    tmp = Path(tempfile.mkdtemp(prefix="repo_falso_"))
    # Sin las variables GIT_* del entorno: cuando esta prueba corre dentro del
    # propio pre-commit, git exporta GIT_INDEX_FILE, y en un worktree esa ruta es
    # absoluta. El repo falso escribia en el indice del repo real y el commit se
    # detenia con "fallan las pruebas" (15-10-2026).
    sin_git = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        sp.run(["git", "init", "-q"], cwd=tmp, check=True, env=sin_git)

        def commit_con(texto: str) -> sp.CompletedProcess:
            (tmp / "nota.md").write_text(texto)
            sp.run(["git", "add", "nota.md"], cwd=tmp, check=True, env=sin_git)
            return sp.run(["bash", str(hook)], cwd=tmp, capture_output=True, text=True,
                          stdin=sp.DEVNULL, env={**sin_git, "CONFIRMO_COMMIT": "1"})

        # La ruta se arma por partes para que este mismo archivo no frene su commit.
        ruta = "/Users" + "/alguien"
        r = commit_con(f"uno\ndos\nver {ruta}/Documents/clase.txt\n")
        check("una ruta con usuario real frena el commit", r.returncode == 1, r.stdout)
        check("y dice el archivo y la linea", f"nota.md, linea 3: {ruta}" in r.stdout, r.stdout)

        r = commit_con("ver /Users/tu_usuario/Documents/clase.txt, o /Users/tu_usuario.\n")
        check("el ejemplo /Users/tu_usuario pasa, aunque lo siga una coma o un punto",
              r.returncode == 0, r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probar_ingesta_del_wiki_al_terminar_una_clase() -> None:
    print("\n== ingesta del wiki ==")
    from unittest import mock
    from orquestador import finalizar_clase as fc

    with mock.patch.object(fc.subprocess, "Popen") as popen, \
            mock.patch.object(fc, "INGESTA_WIKI", Path(__file__)):
        fc.lanzar_ingesta_wiki({})
        check("sin ingesta_wiki en la configuración no lanza nada", not popen.called)
        fc.lanzar_ingesta_wiki({"ingesta_wiki": False})
        check("con ingesta_wiki apagada tampoco", not popen.called)
        fc.lanzar_ingesta_wiki({"ingesta_wiki": True, ensayo.CLAVE: True})
        check("en un ensayo no lanza la ingesta, aunque esté encendida", not popen.called)

    with mock.patch.object(fc.subprocess, "Popen") as popen, \
            mock.patch.object(fc, "INGESTA_WIKI", Path("/no/existe/memoria-ingesta.py")):
        fc.lanzar_ingesta_wiki({"ingesta_wiki": True})
        check("sin el script no lanza nada y no falla", not popen.called)

    with mock.patch.object(fc.subprocess, "Popen", side_effect=OSError("sin permiso")), \
            mock.patch.object(fc, "INGESTA_WIKI", Path(__file__)):
        fc.lanzar_ingesta_wiki({"ingesta_wiki": True})
        check("si no se puede lanzar, el cierre de la clase sigue", True)

    with mock.patch.object(fc.subprocess, "Popen") as popen, \
            mock.patch.object(fc, "INGESTA_WIKI", Path(__file__)):
        fc.lanzar_ingesta_wiki({"ingesta_wiki": True})
        args, kw = popen.call_args
        check("lo lanza aparte y con --ahora", args[0][-1] == "--ahora" and kw.get("start_new_session") is True)


if __name__ == "__main__":
    # Todas las funciones probar_*, en el orden en que estan escritas. Antes era
    # una lista a mano, y una prueba que no se anotaba ahi no se ejecutaba nunca,
    # sin que nadie se enterara.
    for nombre, prueba in list(globals().items()):
        if nombre.startswith("probar_") and callable(prueba):
            prueba()

    print()
    if fallos:
        print(f"FALLARON {len(fallos)}: " + ", ".join(fallos))
        raise SystemExit(1)
    print("Todas las pruebas pasaron.")
