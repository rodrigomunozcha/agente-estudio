"""
Chequeo de las 18:00, antes de clases: si algo impediria procesar la clase de hoy,
llega por Telegram con tiempo para arreglarlo.

Por que existe. En el inventario de fallas del 21-10-2026, 6 de 14 fueron del entorno
(sesion de Claude vencida, programa desactualizado, OneDrive con el transcriptor en la
nube) y todas se podian ver antes de la clase. Usa las comprobaciones de verificar.py,
menos la de notificaciones, que muestra un aviso de prueba en el Mac y a diario molesta.
Si todo esta bien, no escribe.
"""
from . import telegram, verificar


def main() -> None:
    for comprobar in (verificar._modulos_del_pipeline, verificar._cli_de_claude, verificar._sesion_de_claude,
                      verificar._configuracion, verificar._transcriptor_legible, verificar._espacio_en_disco):
        try:
            comprobar()
        except Exception as e:
            verificar._anotar(verificar.FALLA, f"no se pudo comprobar {comprobar.__name__}", type(e).__name__)
    fallas = [(que, detalle) for estado, que, detalle in verificar._resultados if estado == verificar.FALLA]
    if fallas:
        telegram.avisar("🩺 Chequeo antes de clases: algo impediría procesar la clase de hoy\n"
                        + "\n".join(f"• {que}" + (f": {detalle}" if detalle else "") for que, detalle in fallas)
                        + "\n👉 Revísalo en una sesión de Claude antes de clase.")


if __name__ == "__main__":
    main()
