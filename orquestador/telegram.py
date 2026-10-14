"""
Cliente minimo de la API de bots de Telegram, sin dependencias: urllib de la
biblioteca estandar. Lo usan el bot (bot.py), la secretaria (secretaria.py) y
los avisos del pipeline (notificaciones.py).

El token vive en el Llavero de macOS, servicio "agente-estudio-telegram", y lo
guardo el estudiante con `security add-generic-password ... -w`. Nunca va en un
archivo, en el codigo ni en un mensaje de error: quien lo tiene controla el bot.
El unico chat que el bot escucha esta en config.json, en telegram.chat_id.

Limites verificados el 29-09-2026 en core.telegram.org/bots/api: mensaje de 1 a
4096 caracteres, callback_data de 1 a 64 bytes, leyenda de documento hasta 1024,
descarga de archivos hasta 20 MB, y el "escribiendo..." dura 5 segundos o menos.
"""
import json
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .config import cargar_config

SERVICIO_LLAVERO = "agente-estudio-telegram"
MAX_TEXTO = 4096
MAX_LEYENDA = 1024
TIMEOUT_SEGUNDOS = 15

_token: str | None = None


def token() -> str | None:
    global _token
    if _token is None:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICIO_LLAVERO, "-w"],
                           capture_output=True, text=True)
        _token = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    return _token


def chat_autorizado() -> int | None:
    try:
        return (cargar_config().get("telegram") or {}).get("chat_id")
    except Exception:
        return None


def _abrir(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # Telegram contesta los errores con JSON y una descripcion legible.
        # El HTTPError en si no se propaga: su URL lleva el token.
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "description": f"sin conexión ({e.reason})"}
    except OSError as e:
        # Un corte por tiempo a mitad de la respuesta no llega como URLError sino
        # como TimeoutError (un OSError), y no lo atrapaba nadie. Pasaba sobre
        # todo en la espera larga de getUpdates: el bot se caia y launchd lo
        # levantaba de nuevo, 13 veces hasta el 29-09-2026. Es la misma falta de
        # conexion y se trata igual.
        return {"ok": False, "description": f"sin conexión ({type(e).__name__})"}


def llamar(metodo: str, *, espera: float = TIMEOUT_SEGUNDOS, **params):
    # "espera" y no "timeout": getUpdates tiene su propio parametro timeout (los
    # segundos de espera larga), y ese tiene que llegar a Telegram.
    t = token()
    if not t:
        raise RuntimeError("falta el token del bot en el Llavero de macOS")
    req = urllib.request.Request(f"https://api.telegram.org/bot{t}/{metodo}",
                                 data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = _abrir(req, espera)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram rechazó {metodo}: {resp.get('description')}")
    return resp.get("result")


def _trozos(texto: str) -> list[str]:
    """Parte un texto largo en mensajes de hasta 4096, cortando en un salto de
    linea cuando se puede para no partir una frase por la mitad."""
    trozos, resto = [], texto
    while len(resto) > MAX_TEXTO:
        corte = resto.rfind("\n", 0, MAX_TEXTO)
        corte = corte if corte > MAX_TEXTO // 2 else MAX_TEXTO
        trozos.append(resto[:corte])
        resto = resto[corte:].lstrip("\n")
    return trozos + [resto]


def botones(opciones: list[tuple[str, str]], por_fila: int = 2) -> dict:
    """[(texto, dato)] a un teclado en linea. El dato viaja de vuelta al tocar."""
    filas = [opciones[i:i + por_fila] for i in range(0, len(opciones), por_fila)]
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in filas]}


def boton_enlace(texto: str, url: str) -> dict:
    """Un boton que abre un enlace al tocarlo (campo url de InlineKeyboardButton)."""
    return {"inline_keyboard": [[{"text": texto, "url": url}]]}


def enviar(texto: str, chat_id: int | None = None, teclado: dict | None = None) -> dict:
    """Envia un mensaje (partido si es largo). Devuelve el ultimo enviado."""
    chat_id = chat_id or chat_autorizado()
    trozos = _trozos(texto or "(vacío)")
    ultimo = None
    for i, trozo in enumerate(trozos):
        extra = {"reply_markup": teclado} if teclado and i == len(trozos) - 1 else {}
        ultimo = llamar("sendMessage", chat_id=chat_id, text=trozo, **extra)
    return ultimo


def editar(message_id: int, texto: str, chat_id: int | None = None) -> None:
    llamar("editMessageText", chat_id=chat_id or chat_autorizado(), message_id=message_id,
           text=texto[:MAX_TEXTO])


def quitar_botones(message_id: int, chat_id: int | None = None) -> None:
    """Deja el mensaje como esta pero sin botones, para que una propuesta retirada o
    reemplazada no se pueda confirmar. Si Telegram no deja (mensaje muy viejo), el bot
    igual ignora ese botón: ver bot.atender_boton."""
    try:
        llamar("editMessageReplyMarkup", chat_id=chat_id or chat_autorizado(), message_id=message_id,
               reply_markup={"inline_keyboard": []})
    except (RuntimeError, OSError):
        pass


def escribiendo(chat_id: int | None = None) -> None:
    llamar("sendChatAction", chat_id=chat_id or chat_autorizado(), action="typing")


def responder_boton(callback_query_id: str, texto: str = "") -> None:
    # Solo apaga el relojito del boton. Si el toque llego tarde (el bot estaba
    # ocupado o caido), Telegram lo rechaza con "query is too old", y eso
    # cortaba la accion entera: el Si no se anotaba (30-09 y 01-10-2026).
    try:
        llamar("answerCallbackQuery", callback_query_id=callback_query_id, text=texto[:200])
    except (RuntimeError, OSError):
        pass


def enviar_documento(ruta: Path, leyenda: str = "", chat_id: int | None = None) -> dict:
    """Sube un archivo como documento (multipart/form-data, que es lo que pide
    Telegram para subir archivos)."""
    t = token()
    if not t:
        raise RuntimeError("falta el token del bot en el Llavero de macOS")
    limite = f"----agente{uuid.uuid4().hex}"
    partes = []
    for nombre, valor in (("chat_id", str(chat_id or chat_autorizado())), ("caption", leyenda[:MAX_LEYENDA])):
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n{valor}\r\n'.encode())
    nombre_archivo = Path(ruta).name.replace('"', "'")
    partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="document"; filename="{nombre_archivo}"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n".encode() + Path(ruta).read_bytes() + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{t}/sendDocument", data=b"".join(partes),
                                 headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
    resp = _abrir(req, 60)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram rechazó sendDocument: {resp.get('description')}")
    return resp.get("result")


def descargar(file_id: str, destino: Path) -> Path:
    """Baja un archivo que mando el estudiante (hasta 20 MB, limite de Telegram)."""
    info = llamar("getFile", file_id=file_id)
    req = urllib.request.Request(f"https://api.telegram.org/file/bot{token()}/{info['file_path']}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            datos = r.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"no se pudo bajar el archivo ({getattr(e, 'reason', e)})") from None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)
    return destino


def avisar(texto: str, adjunto: Path | None = None, teclado: dict | None = None) -> bool:
    """
    Copia al iPhone de un aviso del pipeline. Nunca lanza ni demora el
    procesamiento: sin token, sin chat emparejado o sin red, se omite y queda
    el aviso de macOS, que ya salio.
    """
    try:
        if not token() or not chat_autorizado():
            return False
        if adjunto is not None and Path(adjunto).is_file():
            enviar_documento(Path(adjunto), texto)
        else:
            enviar(texto, teclado=teclado)
        return True
    except Exception:
        return False
