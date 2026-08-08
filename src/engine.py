import os
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote, urlparse
from concurrent.futures import ThreadPoolExecutor
from src.utils import logger, cargar_config, obtener_proxies_dict, verificar_espacio_disco

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
MAX_RETRIES = 3

class DownloadEngine:
    def __init__(self):
        self.config = cargar_config()
        self.estado = {
            "estado": "INACTIVO",  # INACTIVO, ESCANEANDO, DESCARGANDO, COMPLETADO, ERROR
            "url_actual": "",
            "total_archivos": 0,
            "completados": 0,
            "fallidos": 0,
            "bytes_descargados": 0,
            "bytes_totales": 0,
            "archivo_actual": "",
            "detalles_error": "",
            "errores_detalle": []  # Lista de tuplas (archivo, motivo_error)
        }

    def actualizar_config(self):
        self.config = cargar_config()

    def obtener_contenido_dir(self, url):
        """Escanea una URL de directorio en UCLV y devuelve carpetas, archivos y posible error explícito."""
        proxies = obtener_proxies_dict(self.config)
        try:
            response = requests.get(url, headers=HEADERS, proxies=proxies, timeout=15)
            response.raise_for_status()
        except requests.exceptions.ProxyError as e:
            msg_err = f"Error de Proxy: No se pudo conectar al servidor Proxy ({self.config.get('proxy_host')}:{self.config.get('proxy_port')})."
            logger.error(f"{msg_err} | {e}")
            return [], [], msg_err
        except requests.exceptions.ConnectionError as e:
            msg_err = "Error de Conexión: La PC no tiene acceso al servidor UCLV o perdió la conexión a Internet."
            logger.error(f"{msg_err} | {e}")
            return [], [], msg_err
        except requests.exceptions.Timeout as e:
            msg_err = "Tiempo de espera agotado (Timeout 15s): El servidor UCLV está tardando demasiado en responder."
            logger.error(f"{msg_err} | {e}")
            return [], [], msg_err
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "Desconocido"
            msg_err = f"Error HTTP {status}: El servidor UCLV devolvió una respuesta de error."
            logger.error(f"{msg_err} | {e}")
            return [], [], msg_err
        except Exception as e:
            msg_err = f"Error Inesperado al escanear: {str(e)}"
            logger.error(f"{msg_err} | {e}")
            return [], [], msg_err

        soup = BeautifulSoup(response.text, 'html.parser')
        folders, files = [], []

        for link in soup.find_all('a'):
            href = link.get('href')
            if not href:
                continue

            decoded_href = unquote(href)

            if decoded_href.startswith('?') or href in ['../', '/']:
                continue
            if link.text.strip().lower() in ['name', 'last modified', 'size', 'description', 'parent directory']:
                continue

            full_url = urljoin(url, href)

            if href.endswith('/'):
                folders.append((decoded_href.rstrip('/'), full_url))
            else:
                files.append((decoded_href, full_url))

        return folders, files, None

    def recolectar_archivos_recursivo(self, url_actual, url_base, extensiones=None):
        """Recorre recursivamente las subcarpetas recopilando archivos."""
        archivos_totales = []
        carpetas, archivos, err = self.obtener_contenido_dir(url_actual)
        
        if err:
            raise Exception(err)

        parsed_base = urlparse(url_base).path
        parsed_current = urlparse(url_actual).path
        rel_path = os.path.relpath(parsed_current, parsed_base)
        if rel_path == '.':
            rel_path = ''

        for nombre_archivo, file_url in archivos:
            if extensiones:
                ext = os.path.splitext(nombre_archivo)[1].lower()
                if ext not in extensiones:
                    continue

            ruta_relativa = os.path.join(rel_path, nombre_archivo)
            archivos_totales.append((file_url, ruta_relativa))

        for _, folder_url in carpetas:
            archivos_totales.extend(
                self.recolectar_archivos_recursivo(folder_url, url_base, extensiones)
            )

        return archivos_totales

    def _descargar_archivo_individual(self, info_archivo, destino_base):
        """Descarga un archivo soportando reanudación .part y captura motivos de fallos detallados."""
        file_url, ruta_relativa = info_archivo
        ruta_final = os.path.join(destino_base, ruta_relativa)
        ruta_part = ruta_final + ".part"
        nombre_mostrar = os.path.basename(ruta_relativa)

        proxies = obtener_proxies_dict(self.config)
        limite_kbps = self.config.get("limite_kbps", 0)
        max_hilos = self.config.get("workers", 3)

        os.makedirs(os.path.dirname(ruta_final), exist_ok=True)

        tamano_remoto = 0
        try:
            head_resp = requests.head(file_url, headers=HEADERS, proxies=proxies, timeout=10)
            tamano_remoto = int(head_resp.headers.get('content-length', 0))

            if os.path.exists(ruta_final) and not os.path.exists(ruta_part):
                if tamano_remoto > 0 and os.path.getsize(ruta_final) < tamano_remoto:
                    os.rename(ruta_final, ruta_part)

            if os.path.exists(ruta_final):
                if tamano_remoto > 0 and os.path.getsize(ruta_final) == tamano_remoto:
                    logger.info(f"[OMITIDO] {nombre_mostrar} (Ya existe completo)")
                    self.estado["completados"] += 1
                    return True, info_archivo
        except Exception:
            pass

        ultimo_motivo_error = "Error desconocido"

        for intento in range(1, MAX_RETRIES + 1):
            try:
                self.estado["archivo_actual"] = nombre_mostrar
                bytes_iniciales = 0
                headers_peticion = HEADERS.copy()

                if os.path.exists(ruta_part):
                    bytes_iniciales = os.path.getsize(ruta_part)
                    if tamano_remoto > 0 and bytes_iniciales < tamano_remoto:
                        headers_peticion['Range'] = f'bytes={bytes_iniciales}-'
                    elif bytes_iniciales >= tamano_remoto and tamano_remoto > 0:
                        os.rename(ruta_part, ruta_final)
                        logger.info(f"[✔ OK] {nombre_mostrar}")
                        self.estado["completados"] += 1
                        return True, info_archivo

                modo = 'ab' if bytes_iniciales > 0 and 'Range' in headers_peticion else 'wb'

                chunk_bytes = 1024 * 64
                target_time_per_chunk = 0
                if limite_kbps > 0:
                    bytes_per_sec_per_worker = (limite_kbps * 1024) / max_hilos
                    target_time_per_chunk = chunk_bytes / bytes_per_sec_per_worker

                with requests.get(file_url, headers=headers_peticion, stream=True, proxies=proxies, timeout=25) as r:
                    content_type = r.headers.get('content-type', '').lower()
                    if 'text/html' in content_type and not nombre_mostrar.endswith(('.html', '.htm')):
                        raise Exception(f"Servidor devolvió página HTML de error (HTTP {r.status_code}).")

                    if r.status_code not in [200, 206]:
                        r.raise_for_status()

                    if r.status_code == 200:
                        bytes_iniciales = 0
                        modo = 'wb'

                    with open(ruta_part, modo) as f:
                        for chunk in r.iter_content(chunk_size=chunk_bytes):
                            if chunk:
                                t_start = time.time()
                                f.write(chunk)
                                self.estado["bytes_descargados"] += len(chunk)

                                if target_time_per_chunk > 0:
                                    elapsed = time.time() - t_start
                                    if elapsed < target_time_per_chunk:
                                        time.sleep(target_time_per_chunk - elapsed)

                if os.path.exists(ruta_part):
                    os.rename(ruta_part, ruta_final)

                logger.info(f"[✔ OK] Descargado: {nombre_mostrar}")
                self.estado["completados"] += 1
                return True, info_archivo

            except requests.exceptions.ProxyError:
                ultimo_motivo_error = f"Error de Proxy ({self.config.get('proxy_host')}:{self.config.get('proxy_port')})"
            except requests.exceptions.ConnectionError:
                ultimo_motivo_error = "Sin conexión / Servidor UCLV no responde"
            except requests.exceptions.Timeout:
                ultimo_motivo_error = "Tiempo de espera agotado (Timeout 25s)"
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else "Desconocido"
                ultimo_motivo_error = f"Error HTTP {status}"
            except Exception as e:
                ultimo_motivo_error = str(e)

            if intento < MAX_RETRIES:
                logger.warning(f"[⚠️ REINTENTO {intento}/{MAX_RETRIES}] {nombre_mostrar}: {ultimo_motivo_error}")
                time.sleep(3)
            else:
                logger.error(f"[❌ FALLÓ] {nombre_mostrar}: {ultimo_motivo_error}")
                self.estado["fallidos"] += 1
                self.estado["errores_detalle"].append((nombre_mostrar, ultimo_motivo_error))
                return False, info_archivo

    def descargar_url(self, url, destino_personalizado=None):
        """
        Función principal invocable externamente.
        Escanea la URL y ejecuta las descargas reportando diagnósticos detallados.
        """
        self.actualizar_config()
        self.estado["estado"] = "ESCANEANDO"
        self.estado["url_actual"] = url
        self.estado["completados"] = 0
        self.estado["fallidos"] = 0
        self.estado["bytes_descargados"] = 0
        self.estado["detalles_error"] = ""
        self.estado["errores_detalle"] = []

        logger.info(f"Iniciando escaneo de URL: {url}")

        exts = ['.mkv', '.mp4', '.avi'] if self.config.get("solo_videos", True) else None

        # Recolectar archivos con manejo de errores explícito
        try:
            archivos = self.recolectar_archivos_recursivo(url, url, exts)
        except Exception as e:
            msg_err = f"No se pudo escanear el enlace:\n_{str(e)}_"
            logger.error(msg_err)
            self.estado["estado"] = "ERROR"
            self.estado["detalles_error"] = msg_err
            return False, msg_err

        if not archivos:
            msg_err = "No se encontraron archivos válidos o con las extensiones permitidas en esta carpeta."
            logger.warning(f"{msg_err} URL: {url}")
            self.estado["estado"] = "COMPLETADO"
            self.estado["detalles_error"] = msg_err
            return False, msg_err

        # Definir carpeta destino
        nombre_carpeta = unquote(urlparse(url).path.rstrip('/').split('/')[-1]) or "Descargas_UCLV"
        destino_base = destino_personalizado or os.path.join(self.config["download_dir"], nombre_carpeta)

        # Calcular tamaño total aproximado
        proxies = obtener_proxies_dict(self.config)
        bytes_totales = 0
        for file_url, _ in archivos:
            try:
                r = requests.head(file_url, headers=HEADERS, proxies=proxies, timeout=5)
                bytes_totales += int(r.headers.get('content-length', 0))
            except Exception:
                pass

        self.estado["total_archivos"] = len(archivos)
        self.estado["bytes_totales"] = bytes_totales

        # Verificar espacio en disco
        if self.config.get("verificar_espacio", True):
            suficiente, libres, requeridos = verificar_espacio_disco(destino_base, bytes_totales)
            if not suficiente:
                msg_err = f"Espacio insuficiente en disco.\n• *Disponibles:* `{libres:.2f} GB`\n• *Requeridos:* `{requeridos:.2f} GB`"
                logger.error(msg_err)
                self.estado["estado"] = "ERROR"
                self.estado["detalles_error"] = msg_err
                return False, msg_err

        # Iniciar Descargas Multihilo
        self.estado["estado"] = "DESCARGANDO"
        max_hilos = self.config.get("workers", 3)
        logger.info(f"Iniciando descarga de {len(archivos)} archivos usando {max_hilos} hilos...")

        fallidos = []
        with ThreadPoolExecutor(max_workers=max_hilos) as executor:
            futuros = [executor.submit(self._descargar_archivo_individual, info, destino_base) for info in archivos]
            for futuro in futuros:
                ok, info = futuro.result()
                if not ok:
                    fallidos.append(info)

        # Pasada 2 de Reintento para archivos fallidos
        if fallidos:
            logger.info(f"Reintentando {len(fallidos)} archivos que dieron error en la primera pasada...")
            with ThreadPoolExecutor(max_workers=max_hilos) as executor:
                futuros_re = [executor.submit(self._descargar_archivo_individual, info, destino_base) for info in fallidos]
                for futuro in futuros_re:
                    futuro.result()

        self.estado["estado"] = "COMPLETADO"
        logger.info(f"Proceso finalizado. Exitosos: {self.estado['completados']}/{len(archivos)} | Fallidos: {self.estado['fallidos']}")
        return True, f"Descarga finalizada. Completados: {self.estado['completados']}/{len(archivos)}"