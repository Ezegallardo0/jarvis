# Jarvis

Asistente para Windows. El reconocimiento de voz corre localmente con Whisper y habla con las voces de Windows. Podés elegir Ollama local, Gemini o OpenAI/ChatGPT como modelo conversacional.

## Instalación (una sola vez)

1. Instalá **Python 3.10 o superior** desde python.org (tildá "Add Python to PATH").
2. Solo si vas a usar el proveedor local, instalá **Ollama** desde https://ollama.com y descargá el modelo:
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
4. Para usar Gemini, obtené una API key en [Google AI Studio](https://aistudio.google.com/apikey). Para OpenAI, creá una en la [plataforma de OpenAI](https://platform.openai.com/api-keys). También podés seguir usando Ollama local sin API key.
5. Opcional, para voz en español: Configuración > Hora e idioma > Voz > agregar voces (por ejemplo "Spanish (Argentina)").

## Uso

- Doble clic en `run.bat` para abrir la interfaz. Elegí proveedor, pegá su API key si corresponde, seleccioná micrófono y salida; después iniciá con voz o texto. La conversación ocurre dentro de la misma interfaz, sin una consola aparte. "Probar reconocimiento" muestra lo que Whisper transcribe, y en modo voz el estado muestra lo último que oyó mientras espera "Jarvis".
- También podés ejecutar `python main.py --texto` para iniciar directamente en modo texto o `python main.py --probar-microfono` para medir la entrada predeterminada.
- Podés pedir recordatorios por minutos (por ejemplo, "recordame en 10 minutos") o alertas por hora (por ejemplo, "avisame a las 18:30"). Jarvis debe quedar abierto para avisarte.
- Después de cada respuesta te escucha unos segundos sin necesidad de repetir "Jarvis".
- Para salir: decí "salir" o Ctrl+C.

La primera vez, Whisper descarga su modelo (unos cientos de MB). Gemini usa `gemini-3.5-flash-lite` y OpenAI usa `gpt-6-luna`, modelos rápidos que se pueden cambiar en `config.py`.

Al seleccionar Gemini u OpenAI, los mensajes y la memoria que Jarvis use para responder se envían al proveedor elegido y pueden generar cargos según tu cuenta/API. La suscripción de ChatGPT no incluye el uso de la API de OpenAI; se administra y factura por separado. La clave ingresada en la interfaz solo permanece en memoria durante esa ejecución; no se guarda en el archivo de configuración. El audio del micrófono se transcribe localmente y no se envía como audio.

## Qué puede hacer

Abrir y cerrar apps permitidas, abrir enlaces en el navegador predeterminado, buscar en Google, programar recordatorios por minutos o alertas para una hora, estado del sistema (CPU, RAM, disco, batería), volumen, capturas de pantalla, listar y leer archivos de tu carpeta de usuario, crear notas, memoria permanente de datos tuyos, bloquear la pantalla y ejecutar comandos de consola.

## Seguridad

- Solo abre apps de la lista `APPS` en `config.py`.
- Solo lee archivos dentro de `CARPETAS_PERMITIDAS`.
- **Bloquear pantalla y ejecutar comandos piden confirmación por voz (sí/no) antes de actuar.**
- **Cerrar una aplicación pide confirmación; Jarvis solicita el cierre normal de sus ventanas para permitir guardar cambios.**
- Las alertas y recordatorios avisan mientras Jarvis está abierto; no se guardan al cerrar el programa.
- No puede borrar archivos por sí solo.

## Agregar habilidades

En `tools.py`, escribí una función y decorala con `@herramienta(...)`. El modelo la descubre automáticamente.

## Ajustes útiles (`config.py`)

- `MODELO`: modelo de Ollama. Más grande = más inteligente, más lento.
- `WHISPER_MODELO`: `base` si el reconocimiento va lento, `medium` si querés más precisión.
- `PALABRAS_ACTIVACION`: variantes de cómo Whisper puede escribir "Jarvis".
- `MICROFONO`: vacío para usar el dispositivo predeterminado de Windows; también podés indicar parte del nombre o el índice de otro micrófono.
- La interfaz guarda la selección de dispositivos en `~/.jarvis/dispositivos_audio.json`.
- `JARVIS_AI_PROVIDER`: proveedor inicial, `gemini`, `openai` u `ollama`.
- `GEMINI_API_KEY` / `OPENAI_API_KEY`: alternativa a pegar la clave en la interfaz.
