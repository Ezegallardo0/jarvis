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

- Doble clic en `run.vbs` para abrir directamente el HUD, sin mostrar CMD ni una segunda interfaz. El botón **Configurar sistema** reúne el micrófono, parlantes, pruebas de audio y reconocimiento, proveedor y modelos de IA, claves, voz, apariencia, sonidos, GitHub, Spotify, ubicación y modelo visual. Las claves se guardan localmente en el Administrador de credenciales de Windows; al guardar se confirma que quedaron protegidas localmente y el campo las conserva mientras el panel permanece abierto. En **Apariencia y sonidos** podés elegir el tema cian, violeta o verde, ajustar volumen y velocidad de la voz, configurar el volumen de efectos y activar o desactivar los sonidos de hover, clic, órdenes, respuestas y confirmaciones. Esas preferencias se guardan en este equipo. Podés escribir en el chat desde el primer momento; si Jarvis aún no está iniciado, el primer mensaje lo inicia en modo texto. Para hablar, seleccioná el micrófono, probalo en Ajustes y elegí **Iniciar con voz**. Mientras el modo de voz esté activo, Jarvis escucha continuamente: hablá normalmente, sin decir «Jarvis», y esperá su respuesta antes de continuar. Decí **«modo silencio»** para apagar las respuestas habladas sin desactivar el micrófono, y **«modo voz»** para volver a oírlas. El botón **Cambiar a voz/texto** permite alternar sin reiniciar y pausar el micrófono. El reactor pulsa y se expande mientras Jarvis responde; Jarvis conversa con un tono más relajado y humor ligero cuando encaja. Jarvis prueba primero MME y DirectSound para el micrófono, con WASAPI como alternativa y evita WDM-KS. Piper Claude es la voz gratuita predeterminada y descarga su modelo la primera vez. Si cambiás micrófono, Whisper o proveedor/modelo de IA mientras Jarvis está activo, reinicialo para aplicar esos cambios.
- En **Configurar sistema** podés configurar GitHub con un token fine-grained limitado a los repositorios necesarios y permisos Metadata, Issues, Pull requests y Contents de solo lectura. Jarvis permite buscar repositorios, issues, pull requests y código, pero no modifica GitHub. Google Drive se muestra como pendiente: todavía no tiene conexión OAuth activa.
- En **Configurar sistema** podés escribir tu ciudad manualmente o pedir una detección aproximada por IP. Jarvis solicita confirmación antes de consultar ipapi.co; la ciudad/región resultante se guarda localmente y se usa como referencia para el clima.
- ElevenLabs puede rechazar voces de biblioteca con HTTP 402 en cuentas gratuitas. En ese caso no es un error de Jarvis ni de la API key: necesitás un plan con acceso a síntesis API para esa voz. Piper sigue disponible sin costo ni API.
- También podés ejecutar `python main.py --texto` para iniciar directamente en modo texto o `python main.py --probar-microfono` para medir la entrada predeterminada.
- Podés pedir recordatorios por minutos (por ejemplo, "recordame en 10 minutos") o alertas por hora (por ejemplo, "avisame a las 18:30"). Jarvis debe quedar abierto para avisarte.
- Puede buscar información actual en internet y consultar el clima de hoy o mañana; si falta la ciudad, te la pregunta.

Para pedidos con varios pasos, Jarvis ejecuta primero los pasos necesarios y usa las respuestas de sus herramientas para decidir cómo seguir. Cuando es posible, comprueba los cambios mediante herramientas de lectura o listado; si un paso falla, evita continuar con los que dependan de él y comunica el resultado parcial. Las confirmaciones de seguridad existentes siguen aplicándose a cada acción sensible.
- Para que Jarvis vea la pantalla: en el inicio o en el chat, activá "Permitir análisis de pantalla", descargá el modelo visual (~3 GB) con el botón y después preguntale qué aparece en pantalla. El análisis corre localmente con Ollama; la captura solo se toma cuando pedís explícitamente que la mire.
- Para salir: decí "salir" o Ctrl+C.

Jarvis puede crear documentos Word `.docx` con el contenido que le dictes y abrirlos; generar dibujos PNG con formas y texto y abrirlos en Paint; y preparar correos en tu aplicación predeterminada. Por ejemplo: "creá un Word llamado Saludo y escribí Hola", "dibujá una casa en Paint" o "redactá un correo para ana@example.com". También puede enviar correos mediante Outlook clásico si tiene una cuenta configurada, pero siempre pide confirmación mostrando destinatario, asunto y cuerpo. Los archivos que crea se guardan bajo tu carpeta de usuario; las rutas indicadas por vos se validan contra `CARPETAS_PERMITIDAS`.

La primera vez, Whisper descarga su modelo (unos cientos de MB). Los modelos predeterminados son `gemini-3.5-flash-lite`, `grok-4.7` y `gpt-6-luna`; el proveedor y el modelo activos se cambian desde **Configurar sistema** sin editar el código ni reiniciar.

Al seleccionar Gemini, Grok (xAI) u OpenAI, los mensajes y la memoria que Jarvis use para responder se envían al proveedor elegido y pueden generar cargos según tu cuenta/API. Las claves de cada proveedor se guardan por separado en el Administrador de credenciales de Windows (no en texto plano ni en `dispositivos_audio.json`) y se vuelven a cargar al abrir Jarvis. También se aceptan `GEMINI_API_KEY`, `XAI_API_KEY`, `OPENAI_API_KEY` y `ELEVENLABS_API_KEY` cuando no hay una clave guardada para ese proveedor. El proveedor y modelo activos se pueden cambiar desde Configuración; el próximo pedido usa la selección sin reiniciar Jarvis. El selector alternativo es opcional: solo se intenta una vez ante un error de disponibilidad/cuota antes de ejecutar herramientas, y no reintenta una cadena después de que ya se ejecutó una acción.

En **Inteligencia Artificial**, podés probar el proveedor activo o comparar Gemini, Grok y OpenAI con el mismo mensaje breve. La medición es el tiempo de ida y vuelta (red más generación), no una métrica interna del servidor del proveedor. Cada prueba consume solicitudes/tokens de API; la comparación omite proveedores sin clave guardada. Las claves se muestran parcialmente enmascaradas en la interfaz. Los modelos sugeridos se mantienen en `MODELOS_IA` de `config.py`; el campo del modelo también se puede editar, y el acceso depende de la cuenta.

Los modelos sugeridos se basan en los catálogos oficiales: [Gemini](https://ai.google.dev/gemini-api/docs/models), [Grok](https://docs.x.ai/developers/models) y [OpenAI](https://developers.openai.com/api/docs/models). El audio del micrófono se transcribe localmente y no se envía como audio.
Las búsquedas web envían la consulta al buscador; el pronóstico se obtiene de Open-Meteo. La geolocalización aproximada por IP solo se solicita después de aceptar el diálogo y usa ipapi.co. Las búsquedas de GitHub envían la consulta y el token al API oficial de GitHub por HTTPS; el token se guarda en el Administrador de credenciales de Windows.

## Visión y privacidad

La herramienta de visión captura la pantalla bajo demanda y la analiza localmente con el modelo `MODELO_VISION` de Ollama. El modelo se descarga a Ollama con el botón de preparación. El permiso de pantalla empieza desactivado en cada inicio, se puede revocar desde el chat y la herramienta no se ofrece al modelo mientras esté apagado.

## Qué puede hacer

Abrir y cerrar apps permitidas, cambiar/minimizar/maximizar ventanas por título, mover y hacer clic con el mouse, arrastrar, desplazar, escribir texto y usar atajos limitados de teclado, abrir enlaces en el navegador predeterminado, buscar en Google, crear documentos Word, dibujar en Paint, redactar y enviar correos con confirmación, programar recordatorios por minutos o alertas para una hora, estado del sistema (CPU, RAM, disco, batería), volumen, capturas de pantalla, buscar/listar/leer archivos dentro de las carpetas permitidas, crear carpetas y archivos de texto, editar, renombrar y mover elementos, borrar archivos o carpetas vacías con confirmación, consultar el registro de acciones, crear notas, memoria permanente de datos tuyos, bloquear la pantalla y ejecutar consultas de diagnóstico permitidas.

## Seguridad

- Solo abre apps de la lista `APPS` en `config.py`.
- Las búsquedas, lecturas y modificaciones de archivos se limitan a `CARPETAS_PERMITIDAS`; se comprueba la ruta resuelta y se rechazan enlaces simbólicos como elemento final. La búsqueda omite carpetas de caché/dependencias y se limita a 20.000 elementos o 50 resultados. La edición/escritura se limita a archivos de texto de hasta 500.000 bytes; no sobrescribe al crear. Solo elimina carpetas vacías.
- **Editar, renombrar, mover o eliminar un elemento requiere confirmación explícita.** Las eliminaciones son permanentes; las carpetas no vacías se rechazan.
- Jarvis guarda en `~/.jarvis/registro_acciones.jsonl` la fecha, la herramienta y el resultado de cada acción, incluidas las solicitudes de detención, sin guardar argumentos ni contenido de documentos. Si no puede registrar una acción, no la inicia; la detención se aplica igualmente y avisa si no pudo registrarla.
- **Los clics, arrastres, desplazamientos, escritura de texto, teclas y atajos piden confirmación.** Las coordenadas usan píxeles del escritorio virtual; escribir y pulsar teclas requiere indicar el título exacto o único de la ventana para activarla primero. Los atajos se limitan a una lista permitida.
- **No se ejecutan comandos arbitrarios ni scripts:** la consola solo permite las consultas `hostname`, `tasklist` y `whoami`, sin argumentos, mediante ejecutables de `System32`, y siempre pide confirmación.
- El botón **Detener automatización** descarta los pedidos en cola y detiene cadenas entre acciones; las secuencias de escritura y arrastre también comprueban la cancelación. La solicitud se aplica al volver de una llamada a la IA, un programa externo u otra operación que Windows no permita interrumpir.
- **Bloquear pantalla pide confirmación por voz (sí/no) antes de actuar.**
- **Cerrar una aplicación pide confirmación; Jarvis solicita el cierre normal de sus ventanas para permitir guardar cambios.**
- **Abrir una aplicación de la lista permitida no pide confirmación, pero Jarvis solo la abre cuando se lo solicitás explícitamente.**
- **Los correos se pueden dejar como borrador; para enviarlos desde Outlook clásico siempre pide confirmación explícita.**
- Las alertas y recordatorios avisan mientras Jarvis está abierto; no se guardan al cerrar el programa.

## Agregar habilidades

En `tools.py`, escribí una función y decorala con `@herramienta(...)`. El modelo la descubre automáticamente.

## Ajustes avanzados

- Las preferencias de audio, modelos, proveedores, idioma, ubicación e integraciones se gestionan desde **Configurar sistema** en el HUD y se guardan en `~/.jarvis/dispositivos_audio.json`.
- Las claves guardadas se administran desde el Administrador de credenciales de Windows. También se aceptan las variables de entorno `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` y `GITHUB_TOKEN`.
- `config.py` queda disponible únicamente para opciones avanzadas no expuestas en la interfaz.

## HUD holográfico

Al iniciar Jarvis se abre únicamente una ventana tipo aplicación con el HUD: reactor animado que reacciona al estado (escuchando, procesando, respondiendo), medidores de CPU/memoria/disco/batería, subsistemas, chat, Spotify y pantalla compartida. Funciona sin internet (salvo la portada de Spotify).

- Se abre con Edge o Chrome en modo aplicación. La interfaz nativa anterior ya no se muestra.
- Las confirmaciones (ejecutar comandos, bloquear pantalla) aparecen como cuadro Sí/Cancelar dentro del HUD.
- El interruptor "Pantalla compartida" muestra una vista previa en vivo y habilita `ver_pantalla`.
- **Spotify:** creá una app en https://developer.spotify.com/dashboard, agregá la redirect URI `http://127.0.0.1:8888/callback` y pegá el Client ID en **Configurar sistema**. No hace falta el secret. Controlar la reproducción requiere cuenta Premium.
- Seguridad: el HUD solo escucha en `127.0.0.1` y exige un token aleatorio por sesión; ninguna página web externa puede mandarle órdenes.
