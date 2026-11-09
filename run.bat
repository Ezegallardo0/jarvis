@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
    echo ERROR: No se encontro el Python del entorno virtual.
    echo Ejecuta primero: python -m venv venv
    echo Luego instala las dependencias con: venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

start "" "venv\Scripts\pythonw.exe" "launcher.py" %*
exit /b 0
