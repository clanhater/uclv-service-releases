; =====================================================================
; SCRIPT DE INNO SETUP PARA SERVICIO SILENCIOSO UCLV
; =====================================================================

#define MyAppName "UCLV Background Service"
#define MyAppVersion "1.0"
#define MyAppPublisher "ClanHater"
#define MyAppExeName "UCLV_Service.exe"

[Setup]
; ID Único para la aplicación
AppId={{D3E8F4A1-5678-4321-9ABC-UCLVSERVICE2026}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}

; Ruta de instalación discreta en la carpeta de AppData del usuario (No pide permisos de Admin)
DefaultDirName={localappdata}\UCLV_Service
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputBaseFilename=UCLV_Service_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; Privilegios normales (Para no levantar sospechas ni avisos de UAC/Administrador)
PrivilegesRequired=lowest

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Dirs]
; Crear carpetas internas necesarias
Name: "{app}\config"
Name: "{app}\downloads"
Name: "{app}\logs"

[Files]
; Copiar el ejecutable principal
Source: "deploy\UCLV_Service.exe"; DestDir: "{app}"; Flags: ignoreversion

; Copiar la configuración inicial (solo si no existe previamente)
Source: "deploy\config\config.json"; DestDir: "{app}\config"; Flags: onlyifdoesntexist

[Registry]
; Agregar el ejecutable al Registro de Windows para ARRANQUE AUTOMÁTICO al encender la PC
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "UCLV_Background_Service"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue

[Run]
; Ejecutar el servicio inmediatamente al finalizar la instalación
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar servicio"; Flags: nowait