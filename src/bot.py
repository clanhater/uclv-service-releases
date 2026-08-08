import os
import json
import time
import threading
import subprocess
import sys
import requests
from urllib.parse import urljoin, unquote, urlparse
from src.utils import logger, cargar_config, obtener_proxies_dict, guardar_config, VERSION
from src.engine import DownloadEngine

BASE_URL_DEFAULT = "https://visuales.uclv.cu/"
ITEMS_POR_PAGINA = 8

def formatear_tamano(bytes_val):
    """Convierte bytes a un formato legible (KB, MB, GB)."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024**2:
        return f"{bytes_val / 1024:.2f} KB"
    elif bytes_val < 1024**3:
        return f"{bytes_val / (1024**2):.2f} MB"
    else:
        return f"{bytes_val / (1024**3):.2f} GB"

class TelegramBotService:
    def __init__(self):
        self.config = cargar_config()
        self.config.setdefault("favoritos", {})
        self.engine = DownloadEngine()
        self.token = self.config.get("telegram_token", "").strip()
        self.chat_id_autorizado = str(self.config.get("telegram_chat_id", "")).strip()
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.hilo_descarga = None
        self.activo = False
        self.red_caida = False  # Bandera de diagnóstico de conexión
        
        # Variables de estado
        self.estado_espera = None
        self.url_explorador_actual = BASE_URL_DEFAULT
        self.pagina_explorador_actual = 0
        self.filtro_busqueda = ""
        self.subcarpetas_cache = []

    def recargar_configuracion(self):
        self.config = cargar_config()
        self.config.setdefault("favoritos", {})

    # --- MÉTODOS AUXILIARES TELEGRAM API ---

    def enviar_peticion(self, metodo, payload):
        proxies = obtener_proxies_dict(self.config)
        url = f"{self.api_url}/{metodo}"
        try:
            r = requests.post(url, json=payload, proxies=proxies, timeout=10)
            return r.json()
        except Exception as e:
            logger.error(f"Error en API Telegram ({metodo}): {e}")
            return None

    def registrar_menu_comandos_telegram(self):
        comandos = [
            {"command": "start", "description": "🏠 Abrir Menú Interactivo"},
            {"command": "explorar", "description": "📂 Explorar servidor Visuales UCLV"},
            {"command": "favoritos", "description": "⭐ Mis Carpetas Favoritas"},
            {"command": "estado", "description": "📊 Ver estado de descargas"},
            {"command": "lista", "description": "📋 Ver archivos listos en disco"},
            {"command": "proxy", "description": "🌐 Configuración de Proxy"},
            {"command": "limpiar", "description": "🗑️ Vaciar carpeta de descargas"}
        ]
        self.enviar_peticion("setMyCommands", {"commands": comandos})

    def enviar_mensaje(self, mensaje, keyboard=None):
        payload = {
            "chat_id": self.chat_id_autorizado,
            "text": mensaje,
            "parse_mode": "Markdown"
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self.enviar_peticion("sendMessage", payload)

    def editar_mensaje(self, message_id, mensaje, keyboard=None):
        payload = {
            "chat_id": self.chat_id_autorizado,
            "message_id": message_id,
            "text": mensaje,
            "parse_mode": "Markdown"
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self.enviar_peticion("editMessageText", payload)

    def responder_callback(self, callback_id, texto=""):
        payload = {"callback_query_id": callback_id, "text": texto}
        self.enviar_peticion("answerCallbackQuery", payload)

    # --- NAVEGADOR Y EXPLORADOR DE VISUALES UCLV ---

    def _obtener_vista_explorador(self, pagina=0):
        carpetas, archivos, err = self.engine.obtener_contenido_dir(self.url_explorador_actual)

        if err:
            msg_err = f"⚠️ *Error al explorar directorio:*\n`{err}`"
            keyboard = [[{"text": "🏠 Volver al Inicio", "callback_data": "exp_home"}]]
            return msg_err, keyboard

        if self.filtro_busqueda:
            query = self.filtro_busqueda.lower()
            carpetas = [(n, u) for n, u in carpetas if query in n.lower()]

        self.subcarpetas_cache = carpetas

        total_carpetas = len(carpetas)
        total_paginas = max(1, (total_carpetas + ITEMS_POR_PAGINA - 1) // ITEMS_POR_PAGINA)
        
        if pagina < 0:
            pagina = 0
        elif pagina >= total_paginas:
            pagina = total_paginas - 1
            
        self.pagina_explorador_actual = pagina

        inicio = pagina * ITEMS_POR_PAGINA
        fin = inicio + ITEMS_POR_PAGINA
        carpetas_pagina = carpetas[inicio:fin]

        ruta_desdecodificada = unquote(urlparse(self.url_explorador_actual).path)
        
        msg = f"📂 *EXPLORADOR DE VISUALES UCLV*\n\n"
        msg += f"📍 *Ubicación:* `{ruta_desdecodificada}`\n"
        if self.filtro_busqueda:
            msg += f"🔍 *Filtro Buscador:* `{self.filtro_busqueda}` ({total_carpetas} coincidencias)\n"
        else:
            msg += f"📁 *Carpetas:* {total_carpetas} | 📄 *Archivos:* {len(archivos)}\n"
        
        if total_paginas > 1:
            msg += f"📄 *Página:* `{pagina + 1} de {total_paginas}`\n"
        msg += "\n"
        
        if carpetas:
            msg += "👇 *Toca una subcarpeta para entrar:*\n"
        else:
            msg += "⚠️ _No se encontraron subcarpetas aquí._\n"

        keyboard = []

        for idx_rel, (nombre, _) in enumerate(carpetas_pagina):
            idx_abs = inicio + idx_rel
            keyboard.append([{"text": f"📁 {nombre}/", "callback_data": f"exp_nav_{idx_abs}"}])

        if total_paginas > 1:
            pag_row = []
            if pagina > 0:
                pag_row.append({"text": "◀️ Anterior", "callback_data": f"exp_page_{pagina - 1}"})
            else:
                pag_row.append({"text": "⛔", "callback_data": "nop"})
                
            pag_row.append({"text": f"📌 {pagina + 1}/{total_paginas}", "callback_data": "nop"})
            
            if pagina < total_paginas - 1:
                pag_row.append({"text": "Siguiente ▶️", "callback_data": f"exp_page_{pagina + 1}"})
            else:
                pag_row.append({"text": "⛔", "callback_data": "nop"})
                
            keyboard.append(pag_row)

        feat_row = [{"text": "🔍 Buscar Carpeta", "callback_data": "ask_search"}]
        if self.filtro_busqueda:
            feat_row.append({"text": "❌ Borrar Búsqueda", "callback_data": "clear_search"})
        else:
            feat_row.append({"text": "⭐ Guardar Favorito", "callback_data": "save_favorite"})
        keyboard.append(feat_row)

        keyboard.append([{"text": "⬇️ DESCARGAR ESTA CARPETA AHORA", "callback_data": "exp_dl_current"}])
        
        nav_row = []
        if self.url_explorador_actual.rstrip('/') != BASE_URL_DEFAULT.rstrip('/'):
            nav_row.append({"text": "⬆️ Subir Nivel", "callback_data": "exp_up"})
            nav_row.append({"text": "🏠 Inicio", "callback_data": "exp_home"})
        
        nav_row.append({"text": "❌ Cerrar", "callback_data": "menu_main"})
        keyboard.append(nav_row)

        return msg, keyboard

    def _obtener_menu_favoritos(self):
        favs = self.config.get("favoritos", {})
        
        msg = "⭐ *MIS CARPETAS FAVORITAS*\n\n"
        if not favs:
            msg += "⚠️ _No tienes marcadores guardados aún._\n"
        else:
            msg += f"Tienes *{len(favs)}* favorito(s) guardado(s).\n👇 *Toca para ir directo a la carpeta:*\n"

        keyboard = []
        for idx, (nombre, url) in enumerate(favs.items()):
            keyboard.append([
                {"text": f"📁 {nombre}", "callback_data": f"fav_nav_{idx}"},
                {"text": "🗑️", "callback_data": f"fav_del_{idx}"}
            ])

        keyboard.append([{"text": "⬅️ Volver al Menú Principal", "callback_data": "menu_main"}])
        return msg, keyboard

    def _obtener_menu_principal(self):
        cfg = self.config
        txt_videos = "🎬 Solo Videos: SÍ" if cfg.get("solo_videos") else "📄 Todos los Archivos"
        txt_espacio = "💾 Verificar Espacio: SÍ" if cfg.get("verificar_espacio") else "💾 Verificar Espacio: NO"

        msg = f"⚙️ *PANEL DE CONTROL INTERACTIVO UCLV* `(v{VERSION})`\n\n"
        msg += f"⚡ *Hilos Simultáneos:* `{cfg.get('workers', 3)}`\n"
        msg += f"🚦 *Límite Velocidad:* `{cfg.get('limite_kbps', 0)} KB/s` (0=Ilimitado)\n"
        msg += f"📁 *Carpeta Target USB:* `{cfg.get('usb_target_folder', 'Descargas_UCLV')}`\n"
        msg += f"🌐 *Estado Proxy:* `{'ACTIVO' if cfg.get('proxy_enabled') else 'INACTIVO'}`\n\n"
        msg += "👇 *Selecciona una acción:*"

        keyboard = [
            [
                {"text": "📂 EXPLORAR VISUALES UCLV", "callback_data": "menu_explorar"},
                {"text": "⭐ MIS FAVORITOS", "callback_data": "menu_favoritos"}
            ],
            [
                {"text": "⬆️ BUSCAR ACTUALIZACIÓN", "callback_data": "check_update"}
            ],
            [
                {"text": txt_videos, "callback_data": "toggle_videos"},
                {"text": txt_espacio, "callback_data": "toggle_espacio"}
            ],
            [
                {"text": "⚡ Editar Hilos", "callback_data": "ask_workers"},
                {"text": "🚦 Editar Límite Vel", "callback_data": "ask_limite"}
            ],
            [
                {"text": "📁 Editar Carpeta USB", "callback_data": "ask_usb_dir"},
                {"text": "🌐 Submenú Proxy", "callback_data": "menu_proxy"}
            ],
            [
                {"text": "📊 Estado Actual", "callback_data": "ver_estado"},
                {"text": "📋 Archivos en Disco", "callback_data": "ver_lista"}
            ]
        ]
        return msg, keyboard
        
    def _actualizar_remotamente(self, url_version_json):
        """Descarga e interpreta el version.json remoto, compara versiones y aplica el update si es superior."""
        self.enviar_mensaje(f"🔍 *Consultando servidor de actualizaciones...*\n`{url_version_json}`")
        proxies = obtener_proxies_dict(self.config)

        try:
            # 1. Consultar el archivo version.json en internet (ligero, < 1 KB)
            r = requests.get(url_version_json, proxies=proxies, timeout=15)
            r.raise_for_status()
            data = r.json()

            version_remota = data.get("version", "1.0.0")
            url_installer = data.get("url", "")
            changelog = data.get("changelog", "Sin detalles.")

            # Importar función comparadora de versiones
            from src.usb_monitor import es_version_mayor

            # 2. Comparar versión remota vs versión actual instalada
            if not es_version_mayor(version_remota, VERSION):
                self.enviar_mensaje(f"✅ *El sistema ya está actualizado.*\n• Versión Instalada: `{VERSION}`\n• Versión Servidor: `{version_remota}`")
                return

            if not url_installer:
                self.enviar_mensaje("❌ *Error:* El archivo de versión no contiene una URL de descarga válida.")
                return

            # 3. Notificar inicio de descarga del ejecutable
            msg_upd = f"⬆️ *¡NUEVA VERSIÓN DETECTADA (v{version_remota})!*\n\n"
            msg_upd += f"📌 *Novedades:* {changelog}\n\n"
            msg_upd += f"⬇️ Descargando instalador ejecutable..."
            self.enviar_mensaje(msg_upd)

            # 4. Descargar el archivo .exe pesado en la carpeta Temp
            ruta_temp = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "UCLV_Update.exe")
            r_exe = requests.get(url_installer, stream=True, proxies=proxies, timeout=40)
            r_exe.raise_for_status()

            with open(ruta_temp, 'wb') as f:
                for chunk in r_exe.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

            # 5. Ejecutar la instalación silenciosa y cerrar esta instancia para permitir el reemplazo
            self.enviar_mensaje("🔄 *Instalación descargada.* Aplicando actualización silenciosa y reiniciando servicio...")
            time.sleep(2)

            subprocess.Popen([
                ruta_temp,
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART"
            ])
            sys.exit(0)

        except Exception as e:
            logger.error(f"Error al verificar/aplicar actualización remota: {e}")
            self.enviar_mensaje(f"❌ *Error al verificar actualización:*\n`{str(e)}`")

    def _obtener_menu_proxy(self):
        cfg = self.config
        estado_px = "ACTIVO" if cfg.get("proxy_enabled") else "INACTIVO"
        pass_mask = "*" * len(cfg.get("proxy_pass", "")) if cfg.get("proxy_pass") else "(Sin Contraseña)"

        msg = "🌐 *CONFIGURACIÓN DE PROXY DE RED*\n\n"
        msg += f"• *Estado:* `{estado_px}`\n"
        msg += f"• *Host (IP):* `{cfg.get('proxy_host') or '(Sin Configurar)'}`\n"
        msg += f"• *Puerto:* `{cfg.get('proxy_port') or '(Sin Configurar)'}`\n"
        msg += f"• *Usuario:* `{cfg.get('proxy_user') or '(Sin Usuario)'}`\n"
        msg += f"• *Contraseña:* `{pass_mask}`\n\n"

        btn_px_status = "🔴 Desactivar Proxy" if cfg.get("proxy_enabled") else "🟢 Activar Proxy"

        keyboard = [
            [{"text": btn_px_status, "callback_data": "toggle_proxy"}],
            [
                {"text": "🖥️ Editar Host", "callback_data": "ask_px_host"},
                {"text": "🔌 Editar Puerto", "callback_data": "ask_px_port"}
            ],
            [
                {"text": "👤 Editar Usuario", "callback_data": "ask_px_user"},
                {"text": "🔑 Editar Contraseña", "callback_data": "ask_px_pass"}
            ],
            [{"text": "⬅️ Volver al Menú Principal", "callback_data": "menu_main"}]
        ]
        return msg, keyboard

    # --- MANEJO DE DESCARGAS Y REPORTES DE DIAGNÓSTICO EN TELEGRAM ---

    def _ejecutar_descarga_hilo(self, url):
        """Ejecuta la descarga en un hilo secundario y reporta diagnósticos detallados a Telegram."""
        self.enviar_mensaje(f"🚀 *Inicio de Descarga*\nEscaneando enlace:\n`{url}`")
        
        try:
            exito, mensaje = self.engine.descargar_url(url)
            
            if exito:
                msg_fin = f"🏁 *Descarga Finalizada con Éxito*\n\n"
                msg_fin += f"📊 *Completados:* {self.engine.estado['completados']}/{self.engine.estado['total_archivos']}\n"
                msg_fin += f"💾 *Datos:* {formatear_tamano(self.engine.estado['bytes_descargados'])}\n"
                
                if self.engine.estado['fallidos'] > 0:
                    msg_fin += f"\n⚠️ *{self.engine.estado['fallidos']} archivo(s) dieron error:*\n"
                    for nom, err in self.engine.estado['errores_detalle'][:10]:
                        msg_fin += f"• `{nom}`: _{err}_\n"
                    if len(self.engine.estado['errores_detalle']) > 10:
                        msg_fin += f"• _... y {len(self.engine.estado['errores_detalle'])-10} errores más._\n"
                self.enviar_mensaje(msg_fin)
            else:
                msg_err = f"❌ *No se pudo realizar la descarga*\n\n"
                msg_err += f"📍 *URL:* `{url}`\n\n"
                msg_err += f"⚠️ *Diagnóstico de Error:*\n{mensaje}"
                self.enviar_mensaje(msg_err)
        except Exception as e:
            logger.error(f"Excepción no capturada en hilo de descarga: {e}")
            self.enviar_mensaje(f"🚨 *Excepción Crítica en Descarga:*\n`{str(e)}`")

    # --- PROCESADOR DE BOTONES (CALLBACK QUERIES) ---

    def procesar_callback_query(self, callback_query):
        cb_id = callback_query["id"]
        data = callback_query.get("data")
        message = callback_query.get("message")
        msg_id = message["message_id"] if message else None

        if not data:
            return

        self.responder_callback(cb_id)

        if data == "nop":
            return

        try:
            if data == "toggle_videos":
                self.config["solo_videos"] = not self.config.get("solo_videos", True)
                guardar_config(self.config)
                msg, kb = self._obtener_menu_principal()
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "toggle_espacio":
                self.config["verificar_espacio"] = not self.config.get("verificar_espacio", True)
                guardar_config(self.config)
                msg, kb = self._obtener_menu_principal()
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "toggle_proxy":
                self.config["proxy_enabled"] = not self.config.get("proxy_enabled", False)
                guardar_config(self.config)
                msg, kb = self._obtener_menu_proxy()
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "menu_explorar":
                self.filtro_busqueda = ""
                msg, kb = self._obtener_vista_explorador(pagina=0)
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "menu_favoritos":
                msg, kb = self._obtener_menu_favoritos()
                self.editar_mensaje(msg_id, msg, kb)

            elif data.startswith("fav_nav_"):
                idx = int(data.replace("fav_nav_", ""))
                favs = self.config.get("favoritos", {})
                llaves = list(favs.keys())
                if 0 <= idx < len(llaves):
                    self.url_explorador_actual = favs[llaves[idx]]
                    self.filtro_busqueda = ""
                    msg, kb = self._obtener_vista_explorador(pagina=0)
                    self.editar_mensaje(msg_id, msg, kb)

            elif data.startswith("fav_del_"):
                idx = int(data.replace("fav_del_", ""))
                favs = self.config.get("favoritos", {})
                llaves = list(favs.keys())
                if 0 <= idx < len(llaves):
                    eliminado = llaves[idx]
                    del favs[eliminado]
                    self.config["favoritos"] = favs
                    guardar_config(self.config)
                    self.enviar_mensaje(f"🗑️ Marcador `{eliminado}` eliminado de tus favoritos.")
                    msg, kb = self._obtener_menu_favoritos()
                    self.editar_mensaje(msg_id, msg, kb)

            elif data == "save_favorite":
                nombre_dir = unquote(urlparse(self.url_explorador_actual).path.rstrip('/').split('/')[-1]) or "Inicio_UCLV"
                favs = self.config.get("favoritos", {})
                favs[nombre_dir] = self.url_explorador_actual
                self.config["favoritos"] = favs
                guardar_config(self.config)
                self.enviar_mensaje(f"⭐ *¡Guardado en Favoritos!*\n`{nombre_dir}`")

            elif data == "ask_search":
                self.estado_espera = "buscar_carpeta"
                self.enviar_mensaje("✍️ *Escribe la palabra clave o nombre de la carpeta que buscas:*")

            elif data == "clear_search":
                self.filtro_busqueda = ""
                msg, kb = self._obtener_vista_explorador(pagina=0)
                self.editar_mensaje(msg_id, msg, kb)

            elif data.startswith("exp_page_"):
                pag = int(data.replace("exp_page_", ""))
                msg, kb = self._obtener_vista_explorador(pagina=pag)
                self.editar_mensaje(msg_id, msg, kb)

            elif data.startswith("exp_nav_"):
                idx = int(data.replace("exp_nav_", ""))
                if 0 <= idx < len(self.subcarpetas_cache):
                    self.url_explorador_actual = self.subcarpetas_cache[idx][1]
                    self.filtro_busqueda = ""
                    msg, kb = self._obtener_vista_explorador(pagina=0)
                    self.editar_mensaje(msg_id, msg, kb)

            elif data == "exp_up":
                parsed = urlparse(self.url_explorador_actual)
                path_parts = [p for p in parsed.path.rstrip('/').split('/') if p]
                if path_parts:
                    path_parts.pop()
                    new_path = '/' + '/'.join(path_parts) + '/' if path_parts else '/'
                    self.url_explorador_actual = f"{parsed.scheme}://{parsed.netloc}{new_path}"
                self.filtro_busqueda = ""
                msg, kb = self._obtener_vista_explorador(pagina=0)
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "exp_home":
                self.url_explorador_actual = BASE_URL_DEFAULT
                self.filtro_busqueda = ""
                msg, kb = self._obtener_vista_explorador(pagina=0)
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "exp_dl_current":
                if self.hilo_descarga and self.hilo_descarga.is_alive():
                    self.enviar_mensaje("⚠️ *Ya hay una descarga en proceso.* Espera a que termine.")
                    return

                self.hilo_descarga = threading.Thread(
                    target=self._ejecutar_descarga_hilo, 
                    args=(self.url_explorador_actual,), 
                    daemon=True
                )
                self.hilo_descarga.start()

            elif data == "menu_main":
                self.estado_espera = None
                msg, kb = self._obtener_menu_principal()
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "menu_proxy":
                self.estado_espera = None
                msg, kb = self._obtener_menu_proxy()
                self.editar_mensaje(msg_id, msg, kb)

            elif data == "ver_estado":
                st = self.engine.estado
                msg = f"📊 *Estado Actual del Servicio:*\n"
                msg += f"• *Estado:* `{st['estado']}`\n"
                if st['estado'] in ["ESCANEANDO", "DESCARGANDO"]:
                    msg += f"• *Archivo:* `{st['archivo_actual']}`\n"
                    msg += f"• *Progreso:* {st['completados']}/{st['total_archivos']} archivos\n"
                    msg += f"• *Descargado:* {formatear_tamano(st['bytes_descargados'])} / {formatear_tamano(st['bytes_totales'])}\n"
                elif st['estado'] == "COMPLETADO":
                    msg += f"• *Última descarga:* {st['completados']} completados, {st['fallidos']} fallidos.\n"
                self.enviar_mensaje(msg)

            elif data == "ver_lista":
                download_dir = self.config.get("download_dir", "downloads")
                if not os.path.exists(download_dir) or not os.listdir(download_dir):
                    self.enviar_mensaje("📂 La carpeta de descargas está vacía.")
                    return

                msg = "📂 *Archivos Listos en Disco:*\n\n"
                for item in os.listdir(download_dir):
                    ruta_item = os.path.join(download_dir, item)
                    if os.path.isdir(ruta_item):
                        msg += f"📁 `{item}/`\n"
                    else:
                        tam = os.path.getsize(ruta_item)
                        msg += f"📄 `{item}` ({formatear_tamano(tam)})\n"
                self.enviar_mensaje(msg)

            elif data == "ask_workers":
                self.estado_espera = "workers"
                self.enviar_mensaje("✍️ *Escribe el nuevo número de Hilos (ej. 4):*")

            elif data == "ask_limite":
                self.estado_espera = "limite_kbps"
                self.enviar_mensaje("✍️ *Escribe el Límite de Velocidad en KB/s (ej. 500):*")

            elif data == "ask_usb_dir":
                self.estado_espera = "usb_target_folder"
                self.enviar_mensaje("✍️ *Escribe el nombre de la carpeta destino en la USB:*")

            elif data == "ask_px_host":
                self.estado_espera = "proxy_host"
                self.enviar_mensaje("✍️ *Escribe la dirección IP o Host del Proxy:*")

            elif data == "ask_px_port":
                self.estado_espera = "proxy_port"
                self.enviar_mensaje("✍️ *Escribe el Puerto del Proxy:*")

            elif data == "ask_px_user":
                self.estado_espera = "proxy_user"
                self.enviar_mensaje("✍️ *Escribe el Usuario del Proxy:*")

            elif data == "ask_px_pass":
                self.estado_espera = "proxy_pass"
                self.enviar_mensaje("✍️ *Escribe la Contraseña del Proxy:*")

            elif data == "check_update":
                update_url = self.config.get("update_url")
                if not update_url:
                    self.enviar_mensaje("⚠️ No hay ninguna URL de actualización configurada en `config.json`.")
                    return
                
                # Iniciar verificación y actualización en un hilo en segundo plano
                hilo_upd = threading.Thread(
                    target=self._actualizar_remotamente, 
                    args=(update_url,), 
                    daemon=True
                )
                hilo_upd.start()

        except Exception as e:
            logger.error(f"Error procesando callback '{data}': {e}")
            self.enviar_mensaje(f"🚨 *Excepción en Botón:*\n`{str(e)}`")

    # --- PROCESADOR DE MENSAJES DE TEXTO ---

    def procesar_mensaje_texto(self, texto):
        texto = texto.strip()

        if self.estado_espera:
            clave = self.estado_espera
            self.estado_espera = None

            if clave == "buscar_carpeta":
                self.filtro_busqueda = texto
                msg, kb = self._obtener_vista_explorador(pagina=0)
                self.enviar_mensaje(msg, kb)
                return

            try:
                if clave == "workers":
                    val = int(texto)
                    if val <= 0: raise ValueError()
                    self.config["workers"] = val
                    self.enviar_mensaje(f"✔ *Hilos actualizados a:* `{val}`")

                elif clave == "limite_kbps":
                    val = int(texto)
                    if val < 0: raise ValueError()
                    self.config["limite_kbps"] = val
                    self.enviar_mensaje(f"✔ *Límite de velocidad actualizado a:* `{val} KB/s`")

                elif clave in ["proxy_host", "proxy_port", "proxy_user", "proxy_pass", "usb_target_folder"]:
                    self.config[clave] = texto
                    self.enviar_mensaje(f"✔ *`{clave}` actualizado correctamente.*")

                guardar_config(self.config)
                self.recargar_configuracion()

                if clave.startswith("proxy_"):
                    msg, kb = self._obtener_menu_proxy()
                else:
                    msg, kb = self._obtener_menu_principal()
                self.enviar_mensaje(msg, kb)
                return
            except ValueError:
                self.enviar_mensaje(f"❌ Valor no válido para `{clave}`. Operación cancelada.")
                return

        if texto.startswith("http://") or texto.startswith("https://"):
            if self.hilo_descarga and self.hilo_descarga.is_alive():
                self.enviar_mensaje("⚠️ *Ya hay una descarga en proceso.* Espera a que termine.")
                return

            self.hilo_descarga = threading.Thread(
                target=self._ejecutar_descarga_hilo, 
                args=(texto,), 
                daemon=True
            )
            self.hilo_descarga.start()
            return

        cmd = texto.lower()

        if cmd in ["/start", "/config", "/menu"]:
            msg, kb = self._obtener_menu_principal()
            self.enviar_mensaje(msg, kb)

        elif cmd == "/explorar":
            self.filtro_busqueda = ""
            msg, kb = self._obtener_vista_explorador(pagina=0)
            self.enviar_mensaje(msg, kb)

        elif cmd == "/favoritos":
            msg, kb = self._obtener_menu_favoritos()
            self.enviar_mensaje(msg, kb)

        elif cmd.startswith("/buscar"):
            partes = texto.split(maxsplit=1)
            if len(partes) > 1:
                self.filtro_busqueda = partes[1]
                msg, kb = self._obtener_vista_explorador(pagina=0)
                self.enviar_mensaje(msg, kb)
            else:
                self.estado_espera = "buscar_carpeta"
                self.enviar_mensaje("✍️ *Escribe la palabra clave o nombre de la carpeta que buscas:*")

        elif cmd == "/proxy":
            msg, kb = self._obtener_menu_proxy()
            self.enviar_mensaje(msg, kb)

        elif cmd == "/estado":
            st = self.engine.estado
            msg = f"📊 *Estado Actual del Servicio:*\n"
            msg += f"• *Estado:* `{st['estado']}`\n"
            if st['estado'] in ["ESCANEANDO", "DESCARGANDO"]:
                msg += f"• *Archivo:* `{st['archivo_actual']}`\n"
                msg += f"• *Progreso:* {st['completados']}/{st['total_archivos']} archivos\n"
                msg += f"• *Descargado:* {formatear_tamano(st['bytes_descargados'])} / {formatear_tamano(st['bytes_totales'])}\n"
            self.enviar_mensaje(msg)

        elif cmd == "/limpiar":
            download_dir = self.config.get("download_dir", "downloads")
            if not os.path.exists(download_dir):
                self.enviar_mensaje("📂 La carpeta de descargas no existe.")
                return

            try:
                import shutil
                for item in os.listdir(download_dir):
                    ruta_item = os.path.join(download_dir, item)
                    if os.path.isdir(ruta_item):
                        shutil.rmtree(ruta_item)
                    else:
                        os.remove(ruta_item)
                self.enviar_mensaje("🗑️ *Carpeta de descargas vaciada correctamente.*")
            except Exception as e:
                self.enviar_mensaje(f"❌ Error al limpiar carpeta: {e}")

        elif cmd.startswith("/actualizar"):
            partes = texto.split(maxsplit=1)
            if len(partes) > 1:
                url_inst = partes[1].strip()
                hilo_upd = threading.Thread(target=self._actualizar_remotamente, args=(url_inst,), daemon=True)
                hilo_upd.start()
            else:
                self.enviar_mensaje("⚠️ *Uso correcto:* `/actualizar <URL_DEL_INSTALADOR_EXE>`")

        else:
            msg, kb = self._obtener_menu_principal()
            self.enviar_mensaje("🤖 Usa el menú interactivo para controlar la aplicación:", kb)

    # --- BUCLE PRINCIPAL DE POLLING CON MONITOREO DE RED ---

    def iniciar_escucha(self):
        if not self.token or not self.chat_id_autorizado:
            logger.error("No se puede iniciar el Bot: telegram_token o telegram_chat_id vacíos.")
            print("[!] ERROR: Configura el 'telegram_token' y 'telegram_chat_id' en config/config.json")
            return

        self.activo = True
        offset = None
        proxies = obtener_proxies_dict(self.config)
        logger.info("Iniciando servicio de escucha de Telegram Bot...")
        
        self.registrar_menu_comandos_telegram()

        msg, kb = self._obtener_menu_principal()
        self.enviar_mensaje(f"🟢 *Servicio UCLV Iniciado e Invisible*\n\n{msg}", kb)

        while self.activo:
            try:
                url = f"{self.api_url}/getUpdates"
                params = {"timeout": 20, "offset": offset}
                response = requests.get(url, params=params, proxies=proxies, timeout=25)
                
                # Si se recupera la conexión tras una caída
                if self.red_caida:
                    self.red_caida = False
                    logger.info("Conexión de red restablecida.")
                    self.enviar_mensaje("🟢 *Red Restablecida:* Se recuperó la conexión con Telegram e Internet.")

                if response.status_code == 200:
                    data = response.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        
                        if "callback_query" in update:
                            cb = update["callback_query"]
                            chat_id = str(cb["from"]["id"])
                            if chat_id == self.chat_id_autorizado:
                                self.procesar_callback_query(cb)

                        elif "message" in update and "text" in update["message"]:
                            msg = update["message"]
                            chat_id = str(msg["chat"]["id"])
                            if chat_id == self.chat_id_autorizado:
                                self.procesar_mensaje_texto(msg["text"])

            except requests.exceptions.ProxyError:
                if not self.red_caida:
                    self.red_caida = True
                    logger.warning("Pérdida de conexión con el servidor Proxy...")
                time.sleep(5)
            except requests.exceptions.ConnectionError:
                if not self.red_caida:
                    self.red_caida = True
                    logger.warning("Pérdida de conexión a Internet / Telegram...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Error inesperado en el bucle del Bot: {e}")
                time.sleep(5)