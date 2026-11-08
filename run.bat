@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: No se encontro el Python del entorno virtual.
    echo Ejecuta primero: python -m venv venv
    echo Luego instala las dependencias con: venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

set "JARVIS_ARGS=%*"
if "%~1"=="" (
    echo.
    echo Selecciona el modo de Jarvis:
    choice /C VT /N /M "[V]oz o [T]exto: "
    if errorlevel 2 (
        set "JARVIS_ARGS=--texto"
    ) else (
        set "JARVIS_ARGS="
    )
)

"venv\Scripts\python.exe" main.py %JARVIS_ARGS%
set "JARVIS_EXIT_CODE=%ERRORLEVEL%"
if not "%JARVIS_EXIT_CODE%"=="0" echo Jarvis termino con un error. Revisa el mensaje anterior.
pause
exit /b %JARVIS_EXIT_CODE%
