"""Compartir pantalla: Jarvis mira tu pantalla con un modelo de visión local."""
import io

import ollama

import config
from tools import herramienta


def capturar_jpeg(ancho_max=1280):
    from PIL import ImageGrab

    img = ImageGrab.grab()
    img.thumbnail((ancho_max, ancho_max))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


@herramienta(
    "Mira la pantalla del usuario en este momento y responde una pregunta sobre lo que se ve "
    "(describir, leer texto, explicar un error, ayudar con lo que tiene abierto).",
    {"pregunta": {"type": "string", "description": "Qué querés saber de la pantalla"}},
    ["pregunta"],
)
def ver_pantalla(pregunta="Describí brevemente lo que hay en la pantalla."):
    try:
        resp = ollama.Client(host=config.OLLAMA_HOST).chat(
            model=config.MODELO_VISION,
            messages=[{
                "role": "user",
                "content": f"Esta es una captura de la pantalla de mi PC. Respondé en español, breve y claro. {pregunta}",
                "images": [capturar_jpeg()],
            }],
        )
    except Exception as e:
        if "not found" in str(e).lower():
            return f"Falta el modelo de visión. Ejecutá en una terminal: ollama pull {config.MODELO_VISION}"
        raise
    return (resp.message.content or "").strip() or "No pude interpretar la captura."