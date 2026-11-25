# Arquitectura, Pruebas y Distribución de JARVIS

## 1. Arquitectura Modular

- **HUD (`ui.py`, `hud/index.html`)**: Interfaz web futurista local sobre HTTP/SSE + token de sesión.
- **Brain (`brain.py`, `ai_providers.py`, `ai_provider_*.py`)**: Razonamiento conversacional, Ensemble multi-IA y selección dinámica de memoria relevante.
- **Core Engine (`core_engine.py`, `tools.py`)**: Registro de herramientas, validación de argumentos y reconocimiento determinista local.
- **Memory System (`memory_system.py`)**: Base SQLite (`~/.jarvis/memoria.db`) categorizada (`perfil`, `preferencias`, `proyectos`, `tareas`, `conversaciones`, `contexto_temporal`) con detección de secretos y contradicciones.
- **Skills Manager (`skills_manager.py`, `skills/`)**: Habilidades modulares con `manifest.json`, permisos verificados, configuración independiente y logs.
- **Integrations (`google_integration.py`, `spotyfyTools.py`, `connections_catalog.py`)**: OAuth 2.0 PKCE para Google Workspace (Calendar, Gmail, Drive) con tokens en Windows Credential Manager.
- **Automation Service (`automation_service.py`)**: Servicio persistente independiente del HUD con SQLite (`~/.jarvis/automations.db`) e IPC seguro vía Windows Named Pipe (`\\.\pipe\JarvisAutomationService`).
- **Distribution (`model_manager.py`, `Jarvis.spec`, `build_exe.bat`, `.gitignore`)**: Descarga de modelos bajo demanda con SHA-256 y separación de datos de usuario (`JARVIS_DATA_DIR`, `JARVIS_MODELS_DIR`).

## 2. Ejecución de Pruebas Automatizadas

```powershell
# Con pytest (si está instalado):
.\venv\Scripts\python.exe -m pytest tests/ -v

# Con unittest (incluido en Python estándar):
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

## 3. Plan Seguro para Limpiar el Repositorio Git

Actualmente el repositorio tiene archivos generados y entornos trackeados (`venv/`, `build/`, `__pycache__/`).
**Sin reescribir el historial automáticamente**, podés limpiar el índice actual de forma segura con:

```powershell
git rm -r --cached venv build __pycache__ scratch
git add .gitignore
git commit -m "Stop tracking venv, build artifacts and caches"
```

Si en el futuro deseás reducir el tamaño histórico en Git, usá `git filter-repo --invert-paths --path venv/ --path build/` en un clon limpio y coordinado.

## 4. Compilación Portable (Windows)

```powershell
.\build_exe.bat
```

Los datos del usuario y modelos descargados se almacenan por defecto en `%USERPROFILE%\.jarvis` (o en las rutas definidas por `JARVIS_DATA_DIR` y `JARVIS_MODELS_DIR`), fuera de la carpeta del ejecutable.
