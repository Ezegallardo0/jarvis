@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: No se encontro el Python del entorno virtual.
    echo Ejecuta primero: python -m venv venv
    echo Luego instala las dependencias con: venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

"venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name Jarvis --icon jarvis.ico --add-data "jarvis.ico;." --add-data "hud;hud" --add-data "skills;skills" --collect-data faster_whisper launcher.py
if errorlevel 1 (
    echo ERROR: No se pudo crear dist\Jarvis.exe.
    exit /b 1
)

echo Ejecutable creado: dist\Jarvis.exe
