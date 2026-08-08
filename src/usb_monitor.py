import os
import time
import shutil
import subprocess
import sys
import psutil
from src.utils import logger, cargar_config, VERSION

def formatear_tamano(bytes_val):
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024**2:
        return f"{bytes_val / 1024:.2f} KB"
    elif bytes_val < 1024**3:
        return f"{bytes_val / (1024**2):.2f} MB"
    else:
        return f"{bytes_val / (1024**3):.2f} GB"

def es_version_mayor(v_nueva, v_actual):
    """Compara si v_nueva es numéricamente mayor que v_actual (ej. '1.1.0' > '1.0.0')."""
    try:
        parts_n = [int(x) for x in v_nueva.split('.')]
        parts_a = [int(x) for x in v_actual.split('.')]
        return parts_n > parts_a
    except Exception:
        return False

class USBMonitorService:
    def __init__(self, bot_service=None):
        self.config = cargar_config()
        self.bot_service = bot_service
        self.activo = False
        self.usbs_procesadas = set()

    def actualizar_config(self):
        self.config = cargar_config()

    def _notificar(self, mensaje):
        logger.info(f"[USB Monitor] {mensaje}")
        if self.bot_service:
            self.bot_service.enviar_mensaje(mensaje)

    def _es_usb_autorizada(self, letra_unidad):
        archivo_token = os.path.join(letra_unidad, ".llave_uclv.id")
        if not os.path.exists(archivo_token):
            return False

        try:
            with open(archivo_token, "r", encoding="utf-8") as f:
                contenido_clave = f.read().strip()
            
            clave_config = self.config.get("usb_secret_key", "LLAVE_SECRETA_UCLV_2026")
            return contenido_clave == clave_config
        except Exception as e:
            logger.error(f"Error al leer token USB en {letra_unidad}: {e}")
            return False

    def _verificar_actualizacion_usb(self, letra_unidad):
        """Comprueba si la USB tiene un instalador/actualización con versión superior."""
        ruta_upd_dir = os.path.join(letra_unidad, "actualizacion")
        ruta_upd_json = os.path.join(ruta_upd_dir, "version.json")
        ruta_installer = os.path.join(ruta_upd_dir, "UCLV_Service_Setup.exe")

        if os.path.exists(ruta_upd_json) and os.path.exists(ruta_installer):
            try:
                with open(ruta_upd_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    version_usb = data.get("version", "1.0.0")

                if es_version_mayor(version_usb, VERSION):
                    self._notificar(
                        f"⬆️ *ACTUALIZACIÓN DETECTADA EN USB*\n\n"
                        f"• *Versión Instalada:* `{VERSION}`\n"
                        f"• *Nueva Versión:* `{version_usb}`\n\n"
                        f"🔄 Aplicando actualización silenciosa y reiniciando servicio..."
                    )
                    time.sleep(2)
                    
                    # Ejecutar el instalador en modo silencioso
                    subprocess.Popen([
                        ruta_installer,
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART"
                    ])
                    
                    # Finalizar el proceso actual para liberar archivos e instalar
                    sys.exit(0)
            except Exception as e:
                logger.error(f"Error al procesar actualización por USB: {e}")

    def _contiene_archivos_part(self, ruta):
        if os.path.isfile(ruta):
            return ruta.endswith(".part")

        for root, _, files in os.walk(ruta):
            for file in files:
                if file.endswith(".part"):
                    return True
        return False

    def _obtener_tamano_item(self, ruta):
        if os.path.isfile(ruta):
            return os.path.getsize(ruta)
        
        total = 0
        for root, _, files in os.walk(ruta):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total

    def procesar_transferencia_usb(self, letra_unidad):
        # 1. Primero verificar si hay una actualización pendiente en la USB
        self._verificar_actualizacion_usb(letra_unidad)

        # 2. Transferir archivos descargados
        download_dir = self.config.get("download_dir", "downloads")
        if not os.path.exists(download_dir) or not os.listdir(download_dir):
            logger.info("USB detectada, pero la carpeta de descargas está vacía.")
            return

        items = os.listdir(download_dir)
        items_a_mover = []
        bytes_totales_a_mover = 0

        for item in items:
            ruta_item = os.path.join(download_dir, item)
            if self._contiene_archivos_part(ruta_item):
                logger.info(f"Omitiendo '{item}' por tener descargas en progreso (.part)")
                continue

            tam_item = self._obtener_tamano_item(ruta_item)
            items_a_mover.append((item, ruta_item, tam_item))
            bytes_totales_a_mover += tam_item

        if not items_a_mover:
            self._notificar("🟢 *USB Autorizada Conectada*\nNo hay archivos completados listos para transferir.")
            return

        _, _, libre_usb = shutil.disk_usage(letra_unidad)
        if libre_usb < bytes_totales_a_mover:
            msg_err = f"⚠️ *USB con Espacio Insuficiente*\n\n"
            msg_err += f"• *Requerido:* {formatear_tamano(bytes_totales_a_mover)}\n"
            msg_err += f"• *Disponible en USB:* {formatear_tamano(libre_usb)}"
            self._notificar(msg_err)
            return

        carpeta_destino_usb = os.path.join(
            letra_unidad, 
            self.config.get("usb_target_folder", "Descargas_UCLV")
        )
        os.makedirs(carpeta_destino_usb, exist_ok=True)

        self._notificar(
            f"🟢 *USB Autorizada Detectada ({letra_unidad})*\n"
            f"📦 Moviendo {len(items_a_mover)} elemento(s) ({formatear_tamano(bytes_totales_a_mover)})..."
        )

        movidos_exitosos = 0
        for nombre_item, ruta_origen, tam_item in items_a_mover:
            ruta_destino = os.path.join(carpeta_destino_usb, nombre_item)
            try:
                if os.path.exists(ruta_destino):
                    if os.path.isdir(ruta_destino):
                        shutil.rmtree(ruta_destino)
                    else:
                        os.remove(ruta_destino)

                shutil.move(ruta_origen, ruta_destino)
                movidos_exitosos += 1
                logger.info(f"Movido exitosamente a USB: {nombre_item}")
            except Exception as e:
                logger.error(f"Error al mover {nombre_item} a USB: {e}")

        msg_fin = f"🏁 *Transferencia a USB Completada*\n\n"
        msg_fin += f"• *Ubicación:* `{carpeta_destino_usb}`\n"
        msg_fin += f"• *Elementos movidos:* {movidos_exitosos}/{len(items_a_mover)}\n"
        msg_fin += f"• *Datos transferidos:* {formatear_tamano(bytes_totales_a_mover)}"
        self._notificar(msg_fin)

    def iniciar_monitoreo(self):
        self.activo = True
        logger.info("Iniciando servicio de monitoreo USB...")

        while self.activo:
            try:
                self.actualizar_config()
                unidades_actuales = set()

                for particion in psutil.disk_partitions(all=False):
                    letra = particion.mountpoint
                    if letra.upper().startswith("C:"):
                        continue

                    if os.path.exists(letra):
                        unidades_actuales.add(letra)

                        if letra not in self.usbs_procesadas:
                            if self._es_usb_autorizada(letra):
                                logger.info(f"Detectada USB AUTORIZADA en {letra}")
                                self.usbs_procesadas.add(letra)
                                self.procesar_transferencia_usb(letra)

                desconectadas = self.usbs_procesadas - unidades_actuales
                for u in desconectadas:
                    logger.info(f"USB retirada de {u}. Reseteando estado.")
                    self.usbs_procesadas.remove(u)

            except Exception as e:
                logger.error(f"Error en el bucle del monitor USB: {e}")

            time.sleep(5)