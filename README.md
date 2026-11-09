# Jarvis

Asistente para Windows. El reconocimiento de voz corre localmente con Whisper. Las respuestas habladas usan por defecto Piper, una voz masculina neuronal gratuita en español mexicano que funciona localmente; también podés elegir voces de Windows o ElevenLabs. Podés elegir Ollama local, Gemini o OpenAI/ChatGPT como modelo conversacional.

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
5. La voz predeterminada es Claude (español mexicano) de [Piper](https://rhasspy.github.io/piper-samples/). Jarvis descarga el modelo de voz (~63 MB) la primera vez que se reproduce; luego funciona sin internet. El modelo está bajo licencia Apache 2.0.
6. Opcional, para usar ElevenLabs: obtené una API key desde tu cuenta y copiá el Voice ID de la voz elegida en el campo correspondiente de Jarvis. El ID se guarda en `~/.jarvis/dispositivos_audio.json`; la API key se almacena en el Administrador de credenciales de Windows.
7. Opcional, para voz local de Windows en español: Configuración > Hora e idioma > Voz > agregar voces (por ejemplo "Spanish (Argentina)").

## Uso

- Doble clic en `run.bat` para abrir la interfaz. Elegí proveedor, pegá su API key si corresponde, seleccioná micrófono, salida y voz; después iniciá con voz o texto. Piper Claude es la voz masculina gratuita predeterminada; su modelo se descarga al probarla o al recibir la primera respuesta. Para ElevenLabs, ingresá la API key y el Voice ID de una voz disponible en tu cuenta. El botón "Probar ElevenLabs" usa esos campos directamente, muestra el código HTTP/detalle de error y reproduce el audio si la solicitud tiene éxito, aunque la voz seleccionada para conversar sea otra. Para usar ElevenLabs en la conversación, seleccioná esa voz en "Voz de respuesta". En modo voz escucha continuamente y responde sin esperar la palabra "Jarvis". Si hablás mientras Jarvis responde, detiene el audio, transcribe tu interrupción y te contesta. "Probar reconocimiento" muestra lo que Whisper transcribe.
- ElevenLabs puede rechazar voces de biblioteca con HTTP 402 en cuentas gratuitas. En ese caso no es un error de Jarvis ni de la API key: necesitás un plan con acceso a síntesis API para esa voz. Piper sigue disponible sin costo ni API.
- También podés ejecutar `python main.py --texto` para iniciar directamente en modo texto o `python main.py --probar-microfono` para medir la entrada predeterminada.
- Podés pedir recordatorios por minutos (por ejemplo, "recordame en 10 minutos") o alertas por hora (por ejemplo, "avisame a las 18:30"). Jarvis debe quedar abierto para avisarte.
- Puede buscar información actual en internet y consultar el clima de hoy o mañana; si falta la ciudad, te la pregunta.
- Para que Jarvis vea la pantalla: en el inicio o en el chat, activá "Permitir análisis de pantalla", descargá el modelo visual (~3 GB) con el botón y después preguntale qué aparece en pantalla. El análisis corre localmente con Ollama; la captura solo se toma cuando pedís explícitamente que la mire.
- Para salir: decí "salir" o Ctrl+C.

La primera vez, Whisper descarga su modelo (unos cientos de MB). Gemini usa `gemini-3.5-flash-lite` y OpenAI usa `gpt-6-luna`, modelos rápidos que se pueden cambiar en `config.py`.

Al seleccionar Gemini u OpenAI, los mensajes y la memoria que Jarvis use para responder se envían al proveedor elegido y pueden generar cargos según tu cuenta/API. Al elegir ElevenLabs, el texto de cada respuesta se envía a ElevenLabs para sintetizar el audio, lo que puede generar cargos según tu plan. Piper sintetiza la voz localmente; el modelo Claude es de español mexicano y su voz está publicada bajo Apache 2.0. La suscripción de ChatGPT no incluye el uso de la API de OpenAI; se administra y factura por separado. Las API keys ingresadas en la interfaz se guardan en el Administrador de credenciales de Windows (no en texto plano ni en `dispositivos_audio.json`) y se vuelven a cargar al abrir Jarvis. También se aceptan las variables de entorno `GEMINI_API_KEY`, `OPENAI_API_KEY` y `ELEVENLABS_API_KEY` cuando no hay una clave guardada para ese proveedor. El audio del micrófono se transcribe localmente y no se envía como audio.
Las búsquedas web envían la consulta al buscador; el pronóstico se obtiene de Open-Meteo.

## Visión y privacidad

La herramienta de visión captura la pantalla bajo demanda y la analiza localmente con el modelo `MODELO_VISION` de Ollama. El modelo se descarga a Ollama con el botón de preparación. El permiso de pantalla empieza desactivado en cada inicio, se puede revocar desde el chat y la herramienta no se ofrece al modelo mientras esté apagado.

## Qué puede hacer

Abrir y cerrar apps permitidas, abrir enlaces en el navegador predeterminado, buscar en Google, programar recordatorios por minutos o alertas para una hora, estado del sistema (CPU, RAM, disco, batería), volumen, capturas de pantalla, listar y leer archivos de tu carpeta de usuario, crear notas, memoria permanente de datos tuyos, bloquear la pantalla y ejecutar comandos de consola.

## Seguridad

- Solo abre apps de la lista `APPS` en `config.py`.
- Solo lee archivos dentro de `CARPETAS_PERMITIDAS`.
- **Bloquear pantalla y ejecutar comandos piden confirmación por voz (sí/no) antes de actuar.**
- **Cerrar una aplicación pide confirmación; Jarvis solicita el cierre normal de sus ventanas para permitir guardar cambios.**
- **Abrir una aplicación de la lista permitida no pide confirmación, pero Jarvis solo la abre cuando se lo solicitás explícitamente.**
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
- `ELEVENLABS_API_KEY`: alternativa a pegar la clave de ElevenLabs en la interfaz.
- `JARVIS_TTS_PROVIDER`: voz inicial, `piper` (predeterminada), `windows` o `elevenlabs`.
- Las claves guardadas se pueden administrar desde el Administrador de credenciales de Windows.
- `MODELO_VISION`: modelo de Ollama usado para analizar capturas de pantalla.
