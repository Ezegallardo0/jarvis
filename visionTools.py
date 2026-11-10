"""Análisis de pantalla bajo demanda con un modelo de visión local."""
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
    "Analiza una captura actual de la pantalla cuando el usuario pide una tarea que requiere conocer "
    "la interfaz visible o pregunta qué aparece en ella. El análisis corre localmente con Ollama; "
    "no captura continuamente ni realiza acciones por sí solo.",
    {"pregunta": {"type": "string", "description": "Qué querés saber de la pantalla"}},
    ["pregunta"],
    requiere_pantalla=True,
)
def ver_pantalla(pregunta="Describí brevemente lo que hay en la pantalla."):
    if not config.COMPARTIENDO_PANTALLA:
        return "El acceso a la pantalla está desactivado. Pedile al usuario que lo habilite en Jarvis."
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
        if "connect" in str(e).lower() or "refused" in str(e).lower():
            return "No pude conectar con Ollama. Abrilo y volvé a pedirme que mire la pantalla."
        raise
    return (resp.message.content or "").strip() or "No pude interpretar la captura."