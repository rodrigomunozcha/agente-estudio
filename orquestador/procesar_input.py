"""
Punto de entrada para la app-boton "Procesar clases" (ver boton_app/).
Reemplaza al agente de launchd: en vez de un ciclo corriendo todo el dia
cada 25 segundos, el estudiante hace clic (o arrastra audios sobre el icono)
cuando tiene algo que procesar. Cero consumo en reposo, cero riesgo de que
algo corra o gaste tokens sin que el lo dispare, y desaparece la clase de
bugs de concurrencia entre el ciclo automatico y el procesamiento manual
que ya nos mordio una vez (ver lock.py).

Pensado para correr SIN Terminal (lanzado por una app compilada con
osacompile): toda la comunicacion con el estudiante pasa por notificaciones
nativas de macOS (ver notificaciones.py), nunca por texto en una consola
que nadie va a ver.
"""
# Homebrew entra al PATH en orquestador/__init__.py, que corre antes que este modulo.
import sys
import time
from pathlib import Path

import anyio

from . import bitacora, cancelacion, deteccion, estado_vivo, lock, notificaciones, pregunta_ramo
from .config import cargar_config
from .finalizar_clase import procesar_pendientes_reconocidos
from .notificaciones import notificar_aviso, notificar_inicio
from .transcripcion import procesar_pendientes

ESPERA_ESTABILIDAD_SEGUNDOS = 4


def _asegurar_estabilidad(config: dict) -> None:
    """Un clic no tiene el beneficio de los ticks periodicos de antes para
    confirmar que un archivo recien llegado ya termino de copiarse. Se
    resuelve adentro del mismo clic: un escaneo aca, una espera corta, y
    el segundo escaneo lo hace procesar_pendientes() como siempre."""
    input_dir = Path(config["rutas"]["input"])
    if deteccion.listar_audios(input_dir):
        deteccion.actualizar_estables(input_dir)
        time.sleep(ESPERA_ESTABILIDAD_SEGUNDOS)


async def _procesar_async(config: dict, registro) -> bool:
    trabajos = procesar_pendientes(config, registro)
    generados = await procesar_pendientes_reconocidos(config, registro)
    return bool(trabajos) or bool(generados)


def main(avisar_si_espera: bool = False) -> None:
    """avisar_si_espera es para el clic en la app: si todo lo de Input espera una
    respuesta por Telegram, se lo dice. Cuando lo lanzan el vigilante o el bot, eso
    mismo termina en silencio: un aviso cada 10 minutos se aprende a ignorar."""
    lock_file = lock.adquirir_no_bloqueante()
    if lock_file is None:
        notificar_aviso("Ya hay algo corriendo", "Espera a que termine antes de hacer clic de nuevo.")
        return

    try:
        config = cargar_config()
        input_dir = Path(config["rutas"]["input"])
        pendientes = deteccion.listar_audios(input_dir)
        if not pendientes:
            estado_vivo.limpiar()
            notificar_aviso(
                "Nada que procesar",
                "Deja tus grabaciones en la carpeta Input y vuelve a hacer clic.",
            )
            return

        # Lo que espera una respuesta por Telegram no se vuelve a preguntar ni se toca:
        # si no hay nada mas, no hay corrida (pregunta_ramo.py).
        pendientes = [a for a in pendientes if not pregunta_ramo.esperando(a)]
        if not pendientes:
            estado_vivo.limpiar()
            if avisar_si_espera:
                notificar_aviso("Esperando tu respuesta",
                                "Las grabaciones de Input esperan que me digas por Telegram de qué son.")
            return

        notificar_inicio(len(pendientes))
        # El icono de la barra de menu es la unica senal visible de que algo
        # esta pasando mientras el Mac queda solo. Se abre aca y se cierra al
        # terminar, para que exista exactamente mientras hay trabajo.
        estado_vivo.iniciar()
        estado_vivo.lanzar_visor()

        # Desde aca la corrida se puede abortar: todo lo que cambie queda
        # anotado, y el aborto lo deshace (ver bitacora.py y cancelacion.py).
        registro = bitacora.Bitacora()
        cancelacion.instalar(registro)

        _asegurar_estabilidad(config)
        errores_antes = notificaciones.errores_avisados
        try:
            hubo_trabajo = anyio.run(_procesar_async, config, registro)
        except cancelacion.Abortado:
            revertido = cancelacion.deshacer_todo()
            estado_vivo.cancelado(revertido)
            notificar_aviso(
                "Procesamiento detenido",
                "Se deshizo todo: tus grabaciones y tus notas quedaron como estaban. "
                "Si alcanzaron a escribirse notas nuevas, quedaron en la carpeta "
                "Para revisar y borrar.",
            )
            return
        except Exception:
            estado_vivo.terminar("El procesamiento se interrumpió", error=True)
            raise

        # Terminó bien: lo anotado ya no hace falta, y conservarlo permitiria
        # deshacer despues una clase que si se quiso procesar.
        registro.limpiar()

        if notificaciones.errores_avisados > errores_antes:
            estado_vivo.terminar("Terminó con errores", error=True)
        elif not hubo_trabajo:
            estado_vivo.terminar("Los archivos parecían seguir copiándose")
            notificar_aviso(
                "Todavía copiando",
                "Los archivos parecen seguir copiándose. Espera un momento y vuelve a hacer clic.",
            )
        else:
            estado_vivo.terminar("Listo, revisa la carpeta Output")
    finally:
        lock.liberar(lock_file)


if __name__ == "__main__":
    # --esperar: lo usa el bot despues de una respuesta por Telegram. Espera a que
    # termine lo que este corriendo, igual que el vigilante, en vez de rendirse.
    if "--esperar" in sys.argv:
        lock.liberar(lock.adquirir_bloqueante())
        main()
    else:
        main(avisar_si_espera=True)
