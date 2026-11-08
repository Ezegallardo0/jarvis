# Jarvis (local)

Asistente autónomo que corre 100% en tu PC con Windows: el cerebro es un modelo de Ollama, la voz se reconoce con Whisper (faster-whisper) y habla con las voces de Windows. Nada se envía a la nube.

## Instalación (una sola vez)

1. Instalá **Python 3.10 o superior** desde python.org (tildá "Add Python to PATH").
2. Instalá **Ollama** desde https://ollama.com y descargá el modelo:
   ```
   ollama pull qwen2.5:7b
   ```
   (Con poca RAM o sin GPU usá `qwen2.5:3b` y cambialo en `config.py`.)
3. En una terminal, dentro de esta carpeta:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Opcional, para voz en español: Configuración > Hora e idioma > Voz > agregar voces (por ejemplo "Spanish (Argentina)").

## Uso

- Modo voz: doble clic en `run.bat` y elegí `Voz` (o `python main.py`). Decí "Jarvis, abrí la calculadora".
- Modo texto: elegí `Texto` al iniciar `run.bat` (o ejecutá `python main.py --texto`).
- Modo texto: `python main.py --texto`
- Después de cada respuesta te escucha unos segundos sin necesidad de repetir "Jarvis".
- Para salir: decí "salir" o Ctrl+C.

La primera vez, Whisper descarga su modelo (unos cientos de MB).

## Qué puede hacer

Abrir apps y webs, buscar en Google, estado del sistema (CPU, RAM, disco, batería), volumen, capturas de pantalla, listar y leer archivos de tu carpeta de usuario, crear notas, recordatorios por voz, memoria permanente de datos tuyos, bloquear la pantalla y ejecutar comandos de consola.

## Seguridad

- Solo abre apps de la lista `APPS` en `config.py`.
- Solo lee archivos dentro de `CARPETAS_PERMITIDAS`.
- **Bloquear pantalla y ejecutar comandos piden confirmación por voz (sí/no) antes de actuar.**
- No puede borrar archivos por sí solo.

## Agregar habilidades

En `tools.py`, escribí una función y decorala con `@herramienta(...)`. El modelo la descubre automáticamente.

## Ajustes útiles (`config.py`)

- `MODELO`: modelo de Ollama. Más grande = más inteligente, más lento.
- `WHISPER_MODELO`: `base` si el reconocimiento va lento, `medium` si querés más precisión.
- `PALABRAS_ACTIVACION`: variantes de cómo Whisper puede escribir "Jarvis".
- `MICROFONO`: vacío para usar el dispositivo predeterminado de Windows; también podés indicar parte del nombre de otro micrófono.
