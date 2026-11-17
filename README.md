# Jarvis

Asistente para Windows. El reconocimiento de voz corre localmente con Whisper. Las respuestas habladas usan Cartesia por defecto; si no está disponible, Jarvis intenta Piper local y luego la voz de Windows. También podés elegir ElevenLabs. Podés elegir Ollama local, Gemini, OpenAI, Groq, Cursor o Cloudflare Workers AI como modelo conversacional.

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
4. Para usar los proveedores conversacionales cloud, configurá las claves desde **Ajustes > API Keys**. Se admiten Gemini, OpenAI, Groq, Cloudflare Workers AI y Cursor; Ollama funciona localmente sin API key. El Core local puede iniciar y ejecutar sus comandos deterministas sin ninguna clave de IA. Cloudflare también requiere el Account ID en **Conexiones**.
5. La voz predeterminada es Claude (español mexicano) de [Piper](https://rhasspy.github.io/piper-samples/). Jarvis descarga el modelo de voz (~63 MB) la primera vez que se reproduce; luego funciona sin internet. El modelo está bajo licencia Apache 2.0.
6. Opcional, para usar ElevenLabs: obtené una API key desde tu cuenta y copiá el Voice ID de la voz elegida en el campo correspondiente de Jarvis. El ID se guarda en `~/.jarvis/dispositivos_audio.json`; la API key se almacena en el Administrador de credenciales de Windows.
7. Opcional, para voz local de Windows en español: Configuración > Hora e idioma > Voz > agregar voces (por ejemplo "Spanish (Argentina)").
8. Para generar el ejecutable de Windows, ejecutá `build_exe.bat`. El archivo se crea en `dist\Jarvis.exe`.

## Uso

- Doble clic en `run.vbs` para abrir el HUD. El micrófono permanece inactivo hasta que pulses **JARVIS LIVE**; desde entonces podés hablar en turnos consecutivos, interrumpir una respuesta y continuar con el mismo contexto. El chat de texto sigue disponible sin activar captura de audio. Si el micrófono se interrumpe, Jarvis informa el error y reintenta conectarlo; si el problema continúa, revisá la entrada en Ajustes. El indicador de IA permanece «Sin verificar» hasta que una solicitud o prueba confirme la conexión; si el proveedor falla, Jarvis muestra el error y mantiene activo el Core local. El panel **Configuración** organiza los ajustes en General, Personalización, Inteligencia Artificial, Conexiones, Control del Sistema, Motor JARVIS y API Keys. Las claves de IA se guardan en el Administrador de credenciales de Windows. Cloudflare requiere un Account ID en Conexiones. Decí **«modo silencio»** para apagar las respuestas habladas sin desactivar el micrófono. Se prueban MME y DirectSound para el micrófono, con WASAPI como alternativa y evitando WDM-KS. En Personalización se puede ajustar la sensibilidad de detección y elegir dispositivos de entrada y salida. Si cambiás micrófono, Whisper o proveedor/modelo de IA mientras Jarvis está activo, reinicialo para aplicar esos cambios.

**Arquitectura de voz:** el modo de voz tradicional sigue disponible con Whisper local y los proveedores de TTS configurables. **JARVIS LIVE** usa por defecto Gemini Live nativo: captura PCM16 mono a 16 kHz, mantiene una sesión WebSocket, recibe PCM16 a 24 kHz y lo reproduce en bloques de 20 ms. Gemini gestiona audio-a-audio, VAD del proveedor, transcripciones opcionales, interrupciones, contexto comprimido y llamadas a las herramientas registradas. El VAD local limpia la cola de reproducción al detectar que el usuario empieza a hablar; la sesión del proveedor cancela su turno según su soporte. OpenAI GPT-Live es seleccionable como alternativa, con PCM16 a 24 kHz y delegación cliente para ejecutar el Core Engine. En OpenAI, el fin de audio se estima tras 600 ms sin nuevos fragmentos; es una heurística local, no una confirmación del proveedor. Cambiar de proveedor reinicia la sesión nativa, pero conserva el historial textual disponible.

El HUD muestra proveedor/modelo, conexión, transcripción y TTFA aproximado desde el último audio de voz detectado hasta el primer bloque escrito al dispositivo de salida, además de promedio, mediana, mejor/peor, muestras, errores e intentos de interrupción. La marca es del callback de reproducción, no una medición acústica física del parlante. Las métricas se calculan con turnos reales y todavía no constituyen una comparación controlada entre proveedores. Para comparar, se deben ejecutar al menos diez frases por proveedor con la misma red, hardware y dispositivos; no se muestran resultados inventados. Google Live admite audio PCM16 de entrada a 16 kHz y salida a 24 kHz en WebSocket; su API se documenta como preview. GPT-Live admite audio bidireccional y WebSocket o WebRTC; OpenAI recomienda WebRTC para conexiones de audio. Los precios cambian: consultá [precios de Gemini](https://ai.google.dev/gemini-api/docs/pricing) y [GPT-Live](https://developers.openai.com/api/docs/models/gpt-live-1); los modelos de backend usados en delegaciones pueden cobrarse aparte. El modo LIVE nativo requiere una clave Gemini u OpenAI guardada en el Administrador de credenciales de Windows; sin clave, informa el error y no finge iniciar audio nativo.
- En el primer inicio, Jarvis te pregunta cómo querés que te llame y guarda el nombre localmente en `~/.jarvis/preferencias_usuario.json`; en los siguientes inicios te saluda por ese nombre.
- También podés iniciar `dist\Jarvis.exe`. No requiere instalar Python, pero sí conserva los requisitos externos de la aplicación: Windows, un navegador compatible, conexión y las API/Ollama que configures.
- Cartesia es el proveedor de voz predeterminado. En **Configuración > Personalización**, ingresá tu API key y el Voice ID elegido en [Cartesia Voice Library](https://play.cartesia.ai/voices); la clave se guarda en el Administrador de credenciales de Windows. Sin credenciales o ante un error del servicio, Jarvis usa Piper y luego la voz local de Windows como respaldo.
- Para actualizar el ejecutable tras un cambio, ejecutá `build_exe.bat`. La compilación incluye también los recursos de VAD de `faster-whisper` que necesita el reconocimiento local.
- En **Configurar sistema** podés configurar GitHub con un token fine-grained limitado a los repositorios necesarios y permisos Metadata, Issues, Pull requests y Contents de solo lectura. Jarvis permite buscar repositorios, issues, pull requests y código, pero no modifica GitHub. Google Drive se muestra como pendiente: todavía no tiene conexión OAuth activa.
- El centro de Conexiones distingue servicios disponibles, credenciales configuradas, pendientes y limitaciones; tener una credencial guardada no implica que la conexión se haya verificado. Calendar, Gmail y Google Tasks todavía requieren implementar OAuth. La API oficial de WhatsApp es para WhatsApp Business y no permite leer conversaciones de una cuenta personal; Jarvis no usa automatización no oficial de WhatsApp Web. Consultá [Google OAuth para aplicaciones instaladas](https://developers.google.com/identity/protocols/oauth2/native-app), [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) y [WhatsApp Business Platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform) para conocer requisitos y permisos.
- En **Configurar sistema** podés escribir tu ciudad manualmente o pedir una detección aproximada por IP. Jarvis solicita confirmación antes de consultar ipapi.co; la ciudad/región resultante se guarda localmente y se usa como referencia para el clima.
- ElevenLabs puede rechazar voces de biblioteca con HTTP 402 en cuentas gratuitas. En ese caso no es un error de Jarvis ni de la API key: necesitás un plan con acceso a síntesis API para esa voz. Piper sigue disponible sin costo ni API.
- También podés ejecutar `python main.py` para iniciar en modo voz o `python main.py --probar-microfono` para medir la entrada predeterminada.
- Podés pedir recordatorios por minutos (por ejemplo, "recordame en 10 minutos") o alertas por hora (por ejemplo, "avisame a las 18:30"). Jarvis debe quedar abierto para avisarte.
- Puede buscar información actual en internet y consultar el clima de hoy o mañana; si falta la ciudad, te la pregunta.

La primera vez que inicies el asistente, Jarvis pregunta por voz si autorizás el control del ordenador. Si aceptás, también habilita el acceso a pantalla para que pueda reconocer interfaces cuando una tarea lo necesite. El análisis visual corre localmente con Ollama y solo toma capturas puntuales, no es una grabación continua ni se envía al proveedor de chat. El modelo visual (~3 GB) puede descargarse desde Ajustes. Si rechazás el permiso, el acceso a pantalla y las acciones sensibles quedan bloqueados; podés cambiar o revocar la decisión desde Configuración. Jarvis actúa únicamente ante pedidos tuyos y solo usa integraciones conectadas.
- **Motor JARVIS** reconoce primero comandos locales deterministas para abrir aplicaciones y carpetas, consultar hora/fecha, CPU/RAM/batería/disco, ajustar volumen, buscar archivos por tipo, listar aplicaciones y programar recordatorios. Estas órdenes usan el registro validado de herramientas y pueden ejecutarse aunque falte la clave o falle el proveedor de IA. Las conversaciones y órdenes complejas siguen necesitando un proveedor de IA; el modo que se muestra refleja el estado de ese proveedor, no una prueba independiente de toda la conexión a Internet.
- La vista previa de pantalla del HUD toma una captura al activarla; los análisis posteriores se hacen solo cuando hacen falta para una tarea solicitada.
- Para salir: decí "salir" o Ctrl+C.

Jarvis puede crear documentos Word `.docx` con el contenido que le dictes y abrirlos; generar dibujos PNG con formas y texto y abrirlos en Paint; y preparar correos en tu aplicación predeterminada. Por ejemplo: "creá un Word llamado Saludo y escribí Hola", "dibujá una casa en Paint" o "redactá un correo para ana@example.com". También puede enviar correos mediante Outlook clásico si tiene una cuenta configurada y se lo pedís explícitamente. Con el permiso de control habilitado, puede trabajar con archivos de texto en ubicaciones accesibles para tu cuenta de Windows; Windows sigue aplicando los permisos de tu usuario.

Las órdenes explícitas para abrir aplicaciones (por ejemplo, «abrí Chrome», «¿podés abrirme VS Code?» o «abrí WhatsApp») se reconocen directamente. Jarvis busca coincidencias en los accesos del menú Inicio y en las rutas de aplicaciones registradas en Windows, y acepta pequeñas diferencias de transcripción; YouTube también se abre como sitio web. Las acciones directas quedan en el historial de la conversación activa para que puedas continuar con referencias como «ahora buscá el video que te dije». Ese contexto se conserva mientras Jarvis siga abierto y no se comparte entre reinicios. También puede encadenar la apertura de una aplicación con una tarea posterior: por ejemplo, «abrí Chrome y poné un video aleatorio en YouTube» busca videos públicos y reproduce uno al azar entre los resultados, en Chrome si lo especificás. Para operar una interfaz desconocida, activá «Permitir análisis de pantalla»; Jarvis usa su modelo visual local para inspeccionar lo que se ve y decidir pasos con las herramientas de ventana/teclado disponibles. Si el análisis está desactivado o no reconoce la pantalla, no debe adivinar controles. Desde el botón del clip del HUD podés adjuntar hasta cinco archivos, con un límite conjunto de 10 MB. El motor local valida el formato real y extrae texto de PDF, DOCX/XLSX/PPTX, ODT/ODS/ODP, RTF, texto/código UTF-8 y ZIP de código fuente; también valida imágenes raster y procesa SVG como XML sin renderizarlo. Los formatos antiguos DOC/XLS/PPT, PDF escaneados sin texto y tipos no compatibles se informan explícitamente. Se guarda una copia local en `CARPETA_DATOS/adjuntos`; no se ejecutan código ni macros. Los fragmentos seleccionados para la consulta pueden enviarse al proveedor de chat configurado, previa confirmación. Las imágenes se analizan solo con Ollama local y la imagen original no se envía al proveedor de chat. El análisis se limita a fragmentos para proteger el contexto; si falta texto o visión, Jarvis debe indicarlo y no inventar resultados. Por seguridad, el contenido de los archivos se trata como información no confiable y no habilita herramientas en la consulta de análisis. El texto extraído no se conserva en el historial del modelo; para consultar detalles adicionales, volvé a adjuntar el archivo. Gmail se abre en el navegador para que uses tu sesión: no hay acceso directo a la bandeja ni lectura/envío automático. Para desinstalar, Jarvis puede abrir la pantalla de Aplicaciones instaladas de Windows, pero la selección final queda a tu cargo.

Las API keys se guardan en el Administrador de credenciales de Windows; el HUD solo recibe indicadores de configuración y máscaras parciales, nunca los valores guardados. Al guardar, los campos quedan vacíos hasta que pegues una clave nueva. El HUD se sirve solo en `127.0.0.1`, exige un token local y no almacena la respuesta de configuración en caché.

Si Piper o ElevenLabs no pueden reproducir una respuesta, Jarvis intenta usar la voz local de Windows; si también falla, muestra el error en el HUD.

La primera vez, Whisper descarga su modelo (unos cientos de MB). Los modelos predeterminados son `gemini-3.5-flash-lite`, y `gpt-4o-mini`; el proveedor y el modelo activos se cambian desde **Configurar sistema** sin editar el código ni reiniciar.

Al seleccionar un proveedor, los mensajes y la memoria que Jarvis use para responder se envían a ese servicio y pueden generar cargos según tu cuenta/API. Las claves se guardan por separado en el Administrador de credenciales de Windows (no en texto plano ni en `dispositivos_audio.json`) y se vuelven a cargar al abrir Jarvis. También se aceptan `GEMINI_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `CLOUDFLARE_API_KEY` y `CURSOR_API_KEY` cuando no hay una clave guardada para ese proveedor. El proveedor y modelo activos se pueden cambiar desde Configuración; el próximo pedido usa la selección sin reiniciar Jarvis. El selector alternativo es opcional: solo se intenta una vez ante un error de disponibilidad/cuota antes de ejecutar herramientas, y no reintenta una cadena después de que ya se ejecutó una acción.

En **Configuración**, podés probar el proveedor activo o comparar los proveedores configurados con el mismo mensaje breve. La medición es el tiempo de ida y vuelta (red más generación), no una métrica interna del servidor del proveedor. Cada prueba consume solicitudes/tokens de API; la comparación omite proveedores sin clave guardada. Las claves se muestran parcialmente enmascaradas en la interfaz. Los modelos sugeridos se mantienen en `MODELOS_IA` de `config.py`; el campo del modelo también se puede editar, y el acceso depende de la cuenta.

Los modelos sugeridos se basan en los catálogos oficiales: [Gemini](https://ai.google.dev/gemini-api/docs/models), [Grok](https://docs.x.ai/developers/models) y [OpenAI](https://developers.openai.com/api/docs/models). En el modo de voz tradicional, el audio se transcribe localmente; en LIVE nativo, el audio se transmite al proveedor seleccionado para procesarlo y generar la respuesta.
Las búsquedas web envían la consulta al buscador; el pronóstico se obtiene de Open-Meteo. La geolocalización aproximada por IP solo se solicita después de aceptar el diálogo y usa ipapi.co. Las búsquedas de GitHub envían la consulta y el token al API oficial de GitHub por HTTPS; el token se guarda en el Administrador de credenciales de Windows.

## Visión y privacidad

La herramienta de visión captura la pantalla bajo demanda y la analiza localmente con el modelo `MODELO_VISION` de Ollama. El modelo se descarga a Ollama con el botón de preparación. El permiso de pantalla empieza desactivado en cada inicio, se puede revocar desde el chat y la herramienta no se ofrece al modelo mientras esté apagado.

## Qué puede hacer

Abrir y cerrar apps permitidas, cambiar/minimizar/maximizar ventanas por título, mover y hacer clic con el mouse, arrastrar, desplazar, escribir texto y usar atajos limitados de teclado, abrir enlaces en el navegador predeterminado, buscar en Google, crear documentos Word, dibujar en Paint, redactar y enviar correos con confirmación, programar recordatorios por minutos o alertas para una hora, estado del sistema (CPU, RAM, disco, batería), volumen, capturas de pantalla, buscar/listar/leer archivos dentro de las carpetas permitidas, crear carpetas y archivos de texto, editar, renombrar y mover elementos, borrar archivos o carpetas vacías con confirmación, consultar el registro de acciones, crear notas, memoria permanente de datos tuyos, bloquear la pantalla y ejecutar consultas de diagnóstico permitidas.

## Seguridad

- Solo abre apps de la lista `APPS` en `config.py`.
- Las búsquedas, lecturas y modificaciones de archivos se limitan a `CARPETAS_PERMITIDAS`; se comprueba la ruta resuelta y se rechazan enlaces simbólicos como elemento final. La búsqueda omite carpetas de caché/dependencias y se limita a 20.000 elementos o 50 resultados. La edición/escritura se limita a archivos de texto de hasta 500.000 bytes; no sobrescribe al crear. Solo elimina carpetas vacías.
- **Crear, editar, renombrar, mover o eliminar archivos y carpetas requiere confirmación explícita por acción.** También se confirma antes de enviar correos, ejecutar consultas de sistema permitidas, leer/compartir contenido de archivos y guardar datos en la memoria. Las eliminaciones son permanentes; las carpetas no vacías se rechazan.
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
