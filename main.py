import time
import threading
from src.utils import logger, cargar_config
from src.bot import TelegramBotService
from src.usb_monitor import USBMonitorService

def main():
    logger.info("==========================================")
    logger.info("Iniciando Servicio UCLV (Modo Silencioso)")
    logger.info("==========================================")

    # 1. Instanciar servicio del Bot de Telegram
    bot_service = TelegramBotService()

    # 2. Instanciar servicio de Monitoreo USB pasando la referencia del Bot
    usb_service = USBMonitorService(bot_service=bot_service)

    # 3. Arrancar el Monitor USB en un Hilo secundario (Daemon)
    hilo_usb = threading.Thread(target=usb_service.iniciar_monitoreo, daemon=True)
    hilo_usb.start()

    # 4. Iniciar el servicio de escucha del Bot en el Hilo principal
    try:
        bot_service.iniciar_escucha()
    except Exception as e:
        logger.critical(f"Error fatal en el servicio principal: {e}")

if __name__ == "__main__":
    main()