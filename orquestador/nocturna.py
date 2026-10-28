"""
Las clases de audio dificil se vuelven a transcribir a las 4:00 con large-v3.

Por que de madrugada: el 27-10-2026 large-v3 entendio bastante mas que turbo en la
clase de peor audio del semestre, pero tarda unas cuatro veces mas y ocupa la GPU que
dibuja la pantalla. Una clase de 98 minutos dejo el Mac casi inusable 89 minutos con
el estudiante encima (ver el comentario de transcripcion.transcribir_trabajo). A las
4:00 no molesta a nadie. El estudiante deja el Mac enchufado y despierto con una app
que lo mantiene asi con la tapa cerrada: macOS no deja que un programa sin permisos de
administrador lo despierte solo.

El camino:
1. La transcripcion de siempre, con turbo, mide la clase al terminar (palabras_dudosas).
   Si es dificil, la ficha queda con `nocturna` (la hora limite), la clase no se
   procesa todavia y se avisa por Telegram para que deje el Mac listo.
2. A las 4:00 el LaunchAgent local.agente-estudio.nocturna corre este modulo: vuelve a
   transcribir con large-v3, cambia el texto y procesa la clase como siempre.
3. El seguro: el mismo LaunchAgent corre otra vez a las 7:05. Si la clase sigue
   esperando (el Mac se durmio, algo fallo), se procesa con el texto de turbo, que ya
   estaba guardado. Lo mismo hace cualquier corrida despues de la hora limite.

launchd, si el Mac dormia a la hora, corre la tarea al despertar. Por eso large-v3
solo arranca en los primeros MINUTOS_VENTANA despues de las 4:00: transcribir con el
estudiante usando el Mac es justo lo que se quiso evitar. Fuera de esa ventana solo
corre el seguro.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import estado_vivo, lock, telegram
from .config import cargar_config, dir_pendientes

MODELO = "mlx-community/whisper-large-v3-mlx"
HORA = 4
MINUTOS_VENTANA = 30
HORA_LIMITE = 7

# Una palabra es dudosa si Whisper le dio menos de esta probabilidad. Medido sobre las
# clases del 19 al 26-10-2026 con turbo: entre 5,8% y 23,4% de palabras dudosas. La del
# 20-10-2026, de buen audio, quedo en 5,8%. Empezo en 20%, que dejaba pasar solo la
# peor. El estudiante lo bajo a 10% el 28-10-2026: prefiere mas clases con large-v3,
# aunque lleguen en la mañana. Con 10%, de esas cinco solo la del 20-10 no espera.
PROBABILIDAD_DUDOSA = 0.5
UMBRAL_DUDOSAS = 0.10
# Con pocas palabras el porcentaje no dice nada: un audio de 2 minutos no se juzga.
MINIMO_PALABRAS = 500


def palabras_dudosas(crudos: list[dict]) -> float | None:
    """La fraccion de palabras con probabilidad baja, o None si no hay con que medir."""
    probabilidades = [p[3] for s in crudos for p in s.get("words") or []]
    if len(probabilidades) < MINIMO_PALABRAS:
        return None
    return sum(1 for p in probabilidades if p < PROBABILIDAD_DUDOSA) / len(probabilidades)


def hora_limite(ahora: datetime) -> datetime:
    """Las 7:00 del proximo amanecer: el de hoy si todavia no son las 4:00."""
    dia = ahora.date() if ahora.hour < HORA else ahora.date() + timedelta(days=1)
    return datetime.combine(dia, datetime.min.time()).replace(hour=HORA_LIMITE)


def espera_la_noche(ficha: dict, ahora: datetime | None = None) -> bool:
    limite = ficha.get("nocturna")
    return bool(limite) and (ahora or datetime.now()) < datetime.fromisoformat(limite)


def _fichas() -> list[tuple[Path, dict]]:
    if not dir_pendientes().exists():
        return []
    fichas = []
    for ruta in sorted(dir_pendientes().glob("*.json")):
        if not ruta.with_suffix(".txt").exists():
            continue  # _skill, _whisper y los demas secundarios no son fichas
        try:
            fichas.append((ruta, json.loads(ruta.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return fichas


def audios_en_espera(ahora: datetime | None = None) -> set[str]:
    """Los audios de las clases que esperan la noche: siguen en Input a proposito."""
    return {a for _, f in _fichas() if espera_la_noche(f, ahora) for a in f.get("archivos_originales", [])}


def aviso(ramo: str, fecha: str, dudosas: float) -> str:
    return (f"🌙 La clase de {ramo} del {fecha} tiene audio difícil ({dudosas:.0%} de palabras dudosas).\n"
            f"La transcribo de nuevo a las {HORA}:00 con un modelo más preciso y la tienes en la mañana.\n\n"
            "👉 Antes de dormir: deja el Mac enchufado y activa la app que lo mantiene despierto.\n"
            f"Si a esa hora está dormido, a las {HORA_LIMITE}:05 o cuando lo abras la proceso con el texto de siempre.")


def _rehacer(ruta: Path, ficha: dict, config: dict) -> None:
    from .transcripcion import transcribir_trabajo
    trabajo = {**ficha, "archivos": ficha["archivos_originales"]}
    texto = transcribir_trabajo(trabajo, config, modelo=MODELO)
    Path(ficha["archivo_texto"]).write_text(texto, encoding="utf-8")
    crudos = trabajo.get("segmentos_whisper")
    if crudos:
        (dir_pendientes() / f"{ficha['slug']}_whisper.json").write_text(
            json.dumps(crudos, ensure_ascii=False), encoding="utf-8")
    ficha["transcrito_con"] = MODELO


def main(ahora: datetime | None = None) -> None:
    ahora = ahora or datetime.now()
    inicio_ventana = ahora.replace(hour=HORA, minute=0, second=0, microsecond=0)
    en_ventana = inicio_ventana <= ahora < inicio_ventana + timedelta(minutes=MINUTOS_VENTANA)
    fichas = _fichas()
    esperan = [(r, f) for r, f in fichas if espera_la_noche(f, ahora)]
    vencidas = [(r, f) for r, f in fichas if f.get("nocturna") and not espera_la_noche(f, ahora)]
    if not (esperan and en_ventana) and not vencidas:
        print(f"{ahora:%d-%m %H:%M} nada que hacer (esperan {len(esperan)}, en ventana: {en_ventana})", flush=True)
        return

    lock_file = lock.adquirir_no_bloqueante()
    if lock_file is None:
        # Lo que este corriendo procesa las vencidas, y las que esperan las toma el seguro.
        print(f"{ahora:%d-%m %H:%M} hay otra corrida, no hago nada", flush=True)
        return
    # De madrugada nada suena en el iPhone: el aviso de clase lista se lee al despertar.
    telegram.silencio = True
    try:
        import anyio
        from .finalizar_clase import procesar_pendientes_reconocidos
        from .notificaciones import notificar_error
        config = cargar_config()
        estado_vivo.iniciar()
        ahora_si = esperan if en_ventana else []
        for ruta, ficha in ahora_si:
            try:
                _rehacer(ruta, ficha, config)
            except Exception as e:
                notificar_error(f"{ficha.get('ramo')} - {ficha.get('fecha')}",
                                f"Falló la transcripción de madrugada: {type(e).__name__}: {e}\n"
                                "La clase se procesa igual, con la transcripción de siempre.")
        for ruta, ficha in ahora_si + vencidas:
            ficha.pop("nocturna", None)
            ruta.write_text(json.dumps(ficha, indent=2, ensure_ascii=False), encoding="utf-8")
        anyio.run(procesar_pendientes_reconocidos, config)
        estado_vivo.terminar("Listo, revisa la carpeta Output")
    finally:
        lock.liberar(lock_file)


if __name__ == "__main__":
    main()
