import os
import json
import subprocess
import re

GITHUB_USER = "clanhater"
GITHUB_REPO = "uclv-service-releases"

def actualizar_version_en_utils(nueva_version):
    utils_path = os.path.join("src", "utils.py")
    if os.path.exists(utils_path):
        with open(utils_path, "r", encoding="utf-8") as f:
            contenido = f.read()

        nuevo_contenido = re.sub(
            r'VERSION\s*=\s*"[^"]+"', 
            f'VERSION = "{nueva_version}"', 
            contenido
        )

        with open(utils_path, "w", encoding="utf-8") as f:
            f.write(nuevo_contenido)
        print(f"[✔] VERSION actualizada a '{nueva_version}' en src/utils.py")

def publicar():
    print("=================================================")
    print("  PUBLICADOR AUTOMÁTICO DE ACTUALIZACIONES UCLV  ")
    print("=================================================\n")

    nueva_version = input("Ingresa el número de la nueva versión (ej. 1.1.0): ").strip()
    changelog = input("Escribe el changelog / novedades: ").strip()

    if not nueva_version or not changelog:
        print("[!] Error: Debes ingresar la versión y el changelog. Cancelando.")
        return

    # PASO 1: Actualizar versión en código fuente
    actualizar_version_en_utils(nueva_version)

    # PASO 2: Generar el nuevo version.json
    url_descarga_estatica = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/latest/download/UCLV_Service_Setup.exe"
    
    version_data = {
        "version": nueva_version,
        "url": url_descarga_estatica,
        "changelog": changelog
    }

    with open("version.json", "w", encoding="utf-8") as f:
        json.dump(version_data, f, indent=4, ensure_ascii=False)
    print("[✔] Archivo 'version.json' generado.")

    # PASO 3: Compilar con PyInstaller
    print("\n[1/3] Compilando ejecutable con PyInstaller...")
    os.system("pyinstaller --noconsole --onefile --name UCLV_Service main.py")

    # PASO 4: Copiar a carpeta deploy/
    print("[2/3] Copiando archivos a carpeta deploy/...")
    os.makedirs(os.path.join("deploy", "config"), exist_ok=True)
    os.system("copy dist\\UCLV_Service.exe deploy\\ /Y")
    os.system("copy config\\config.json deploy\\config\\ /Y")

    # PASO 5: Compilar Inno Setup automáticamente
    print("[3/3] Compilando instalador Inno Setup...")
    iscc_path = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    
    if os.path.exists(iscc_path):
        os.system(f'"{iscc_path}" setup_script.iss')
        print("[✔] Instalador generado automáticamente en 'Output/UCLV_Service_Setup.exe'")
    else:
        print("⚠️ Inno Setup no se encontró en la ruta estándar. Compila 'setup_script.iss' manualmente.")
        input("Presiona Enter cuando hayas generado 'Output/UCLV_Service_Setup.exe'...")

    # PASO 6: Subir version.json a GitHub
    print("\n[i] Actualizando version.json en GitHub...")
    os.system("git add version.json")
    os.system(f'git commit -m "Actualización v{nueva_version}"')
    os.system("git push origin main")

    print("\n" + "="*50)
    print(f"🎉 ¡COMPILACIÓN EXITOSA!")
    print("="*50)
    print(f"1. Ve a GitHub: https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/new")
    print(f"2. Crea una Release llamada 'v{nueva_version}'")
    print(f"3. ARRASTRA Y SUBE EL ARCHIVO: Output/UCLV_Service_Setup.exe")
    print(f"4. En Telegram presiona: [ ⬆️ BUSCAR ACTUALIZACIÓN ]")
    print("="*50)

if __name__ == "__main__":
    publicar()