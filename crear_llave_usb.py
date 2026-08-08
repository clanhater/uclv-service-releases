import os
import json

CONFIG_FILE = os.path.join("config", "config.json")

def cargar_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"usb_secret_key": "LLAVE_SECRETA_UCLV_2026"}

def preparar_usb():
    print("=== PREPARADOR DE MEMORIA USB AUTORIZADA ===")
    letra_unidad = input("Ingresa la letra de tu USB (ejemplo: E o F): ").strip().upper()
    if not letra_unidad.endswith(":"):
        letra_unidad += ":"

    ruta_usb = f"{letra_unidad}\\"

    if not os.path.exists(ruta_usb):
        print(f"[!] Error: La unidad {ruta_usb} no existe o no está conectada.")
        return

    config = cargar_config()
    clave_secreta = config.get("usb_secret_key", "LLAVE_SECRETA_UCLV_2026")
    archivo_token = os.path.join(ruta_usb, ".llave_uclv.id")

    try:
        with open(archivo_token, "w", encoding="utf-8") as f:
            f.write(clave_secreta)
        
        # Ocultar el archivo en Windows
        os.system(f'attrib +h "{archivo_token}"')

        print(f"\n[✔] ¡Éxito! Tu USB ({letra_unidad}) ha sido firmada correctamente.")
        print(f"    Archivo creado: {archivo_token}")
        print("    Ahora esta memoria será reconocida de forma exclusiva por el servicio.")
    except Exception as e:
        print(f"[!] Error al escribir en la USB: {e}")

if __name__ == "__main__":
    preparar_usb()