import os
import sys
import json
import logging
import shutil
from urllib.parse import quote

# VERSION ACTUAL DE LA APLICACIÓN
VERSION = "1.0.0"

def obtener_ruta_base():
    """Obtiene la ruta donde reside el script o el ejecutable .exe compilado."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()

BASE_DIR = obtener_ruta_base()
CONFIG_FILE = os.path.join(BASE_DIR, "config", "config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

# --- CONFIGURACIÓN DE LOGS SILENCIOSOS ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("UCLV_Service")

def cargar_config():
    """Carga la configuración desde el archivo JSON."""
    config = {
        "telegram_token": "",
        "telegram_chat_id": "",
        "download_dir": os.path.join(BASE_DIR, "downloads"),
        "workers": 3,
        "solo_videos": True,
        "verificar_espacio": True,
        "limite_kbps": 0,
        "proxy_enabled": False,
        "proxy_host": "",
        "proxy_port": "",
        "proxy_user": "",
        "proxy_pass": "",
        "usb_secret_key": "LLAVE_SECRETA_UCLV_2026",
        "usb_target_folder": "Descargas_UCLV"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config.update(json.load(f))
        except Exception as e:
            logger.error(f"Error al cargar la configuración: {e}")
            
    # Garantizar que download_dir sea una ruta absoluta válida
    if not os.path.isabs(config["download_dir"]):
        config["download_dir"] = os.path.join(BASE_DIR, config["download_dir"])

    return config

def guardar_config(config):
    """Guarda la configuración en el JSON."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error al guardar la configuración: {e}")

def obtener_proxies_dict(config):
    """Genera el diccionario de proxies para requests si está activo."""
    if not config.get("proxy_enabled", False):
        return None

    host = str(config.get("proxy_host", "")).strip()
    port = str(config.get("proxy_port", "")).strip()
    user = str(config.get("proxy_user", "")).strip()
    password = str(config.get("proxy_pass", "")).strip()

    if not host or not port:
        return None

    if user and password:
        user_enc = quote(user)
        pass_enc = quote(password)
        proxy_str = f"http://{user_enc}:{pass_enc}@{host}:{port}"
    else:
        proxy_str = f"http://{host}:{port}"

    return {
        "http": proxy_str,
        "https": proxy_str
    }

def verificar_espacio_disco(destino_base, bytes_requeridos):
    """Verifica si hay espacio suficiente en el disco de destino."""
    os.makedirs(destino_base, exist_ok=True)
    _, _, libre = shutil.disk_usage(destino_base)

    gb_libres = libre / (1024**3)
    gb_necesarios = bytes_requeridos / (1024**3)

    suficiente = libre >= bytes_requeridos
    return suficiente, gb_libres, gb_necesarios