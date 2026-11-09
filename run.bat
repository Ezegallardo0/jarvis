@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
    echo ERROR: No se encontro el Python del entorno virtual.
    echo Ejecuta primero: python -m venv venv
    echo Luego instala las dependencias con: venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

venv\Scripts\python.exe -c "import openai, ddgs, httpx, keyring, piper, spotipy" >nul 2>nul
if errorlevel 1 (
    echo Instalando las dependencias de Jarvis en su entorno virtual...
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: No se pudieron instalar las dependencias. Revisa la conexion y vuelve a intentar.
        exit /b 1
    )
)

start "" "venv\Scripts\pythonw.exe" "launcher.py" %*
exit /b 0
