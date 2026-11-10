"""Valores predeterminados y ajustes avanzados de Jarvis.

Las preferencias de uso se cambian desde la interfaz. config_local.py sigue
disponible para quienes quieran sobrescribir estos valores predeterminados.
"""
import os
from pathlib import Path

NOMBRE = "Jarvis"

# Palabras que activan al asistente (Whisper a veces transcribe distinto)
PALABRAS_ACTIVACION = ["jarvis", "yarvis", "jarbis", "charvis", "jarvi"]

# --- Cerebro (Ollama) ---
OLLAMA_HOST = "http://localhost:11434"
# Con poca RAM/sin GPU probá "qwen2.5:3b". Con buena GPU, "qwen2.5:14b".
MODELO = "qwen2.5:7b"
PROVEEDOR_IA = os.environ.get("JARVIS_AI_PROVIDER", "gemini").lower()
MODELO_GEMINI = "gemini-1.5-flash"
MODELO_OPENAI = "gpt-4o-mini"
MODELO_CURSOR = "grok-3-mini"
MODELO_GROQ_API = "llama-3.1-8b-instant"
MODELO_CLOUDFLARE = "@cf/zai-org/glm-4.7-flash"
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
PROVEEDOR_IA_ALTERNATIVO = ""

# --- Ensemble multi-IA ---
# Cuando hay 2+ API keys configuradas, Jarvis puede usarlas en conjunto.
# MODO_ENSEMBLE: "auto" (detecta automáticamente), "off" (desactivado), "on" (siempre activo)
MODO_ENSEMBLE = os.environ.get("JARVIS_ENSEMBLE", "auto").lower()
# ESTRATEGIA_ENSEMBLE: "mejor" (elige la mejor respuesta), "rapido" (usa el primero en responder),
# "verificar" (el principal responde, los otros verifican), "votar" (mayoría entre modelos)
ESTRATEGIA_ENSEMBLE = os.environ.get("JARVIS_ENSEMBLE_ESTRATEGIA", "mejor").lower()

# Modelos sugeridos para el HUD. Podés editarlos: el acceso depende de cada cuenta/API.
MODELOS_IA = {
    "gemini": (
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash-8b",
    ),
    "openai": ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
    "groq": ("llama-3.1-8b-instant", "llama-3.3-70b-versatile", "mixtral-8x7b-32768"),
    "cloudflare": (
        "@cf/zai-org/glm-4.7-flash",
        "@cf/openai/gpt-oss-20b",
        "@cf/meta/llama-3.1-8b-instruct-fp8",
        "@cf/google/gemma-4-26b-a4b-it",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    ),
    "cursor": ("grok-3-mini", "grok-3"),
}

# --- Voz ---
USAR_VOZ = True            # main.py --texto lo pone en False
VOZ_SILENCIADA = False     # modo silencio: conserva el micrófono, desactiva las respuestas habladas
PROVEEDOR_TTS = os.environ.get("JARVIS_TTS_PROVIDER", "piper").lower()
ELEVENLABS_VOICE_ID = os.environ.get(
    "ELEVENLABS_VOICE_ID", "wBXNqKUATyqu0RtYt25i"
).strip()
VOLUMEN_VOZ = 100
VELOCIDAD_VOZ = 100
PIPER_MODELO_VOZ = "es_MX-claude-high"
WHISPER_MODELO = "small"   # tiny / base / small / medium (más grande = más preciso y lento)
IDIOMA = "es"
VENTANA_CONVERSACION_SEG = 6   # tras responder, escucha seguimiento sin decir "Jarvis"
MICROFONO = ""             # vacío = predeterminado; también acepta parte del nombre o índice
AUDIO_SALIDA = None        # None = salida predeterminada de Windows; también acepta índice
UBICACION = ""             # Ciudad/región aproximada, configurada por el usuario
GITHUB_TOKEN = ""          # Se carga desde el Administrador de credenciales de Windows

# --- Interfaz (HUD) ---
HUD_PUERTO = 8765
HUD_ABRIR_NAVEGADOR = True     # abre el HUD solo al iniciar
VOLUMEN_EFECTOS = 45
TEMA_HUD = "cian"

# --- Visión / compartir pantalla ---
# Modelo que "mira" tu pantalla. Descargalo con: ollama pull qwen2.5vl:3b  (3,2 GB)
# Con buena GPU podés usar qwen2.5vl:7b (6 GB), que lee mejor el texto chico.
MODELO_VISION = "qwen2.5vl:3b"
COMPARTIENDO_PANTALLA = False  # lo cambia el botón del HUD

# --- Spotify ---
# Client ID de tu app en https://developer.spotify.com/dashboard (no hace falta el secret).
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# --- Datos y seguridad ---
CARPETA_DATOS = Path.home() / ".jarvis"
# Solo podrá buscar, leer y modificar archivos dentro de estas carpetas.
CARPETAS_PERMITIDAS = [Path.home()]

# Aplicaciones que puede abrir (nombre que decís -> comando de Windows).
# Solo se pueden abrir las que figuren acá.
APPS = {
    "bloc de notas": "notepad.exe",
    "calculadora": "calc.exe",
    "explorador": "explorer.exe",
    "paint": "mspaint.exe",
    "administrador de tareas": "taskmgr.exe",
    "configuracion": "ms-settings:",
    "terminal": "wt.exe",
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "spotify": "spotify",
    "word": "winword",
    "excel": "excel",
    "vscode": "code"
}

# Configuración personal opcional (no se pisa al actualizar el proyecto)
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass