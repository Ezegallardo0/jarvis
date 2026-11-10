"""Catálogo y configuración de proveedores de IA."""
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class Proveedor:
    nombre: str
    variable_entorno: str
    atributo_modelo: str
    base_url: str | None = None
    limite_tokens: str = "max_completion_tokens"


PROVEEDORES = {
    "gemini": Proveedor(
        "Gemini",
        "GEMINI_API_KEY",
        "MODELO_GEMINI",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "max_tokens",
    ),
    "openai": Proveedor(
        "OpenAI",
        "OPENAI_API_KEY",
        "MODELO_OPENAI",
    ),
    "groq": Proveedor(
        "Groq",
        "GROQ_API_KEY",
        "MODELO_GROQ_API",
        "https://api.groq.com/openai/v1",
        "max_tokens",
    ),
    "cloudflare": Proveedor(
        "Cloudflare Workers AI",
        "CLOUDFLARE_API_KEY",
        "MODELO_CLOUDFLARE",
        limite_tokens="max_tokens",
    ),
    "cursor": Proveedor(
        "Cursor",
        "CURSOR_API_KEY",
        "MODELO_CURSOR",
        "https://api.cursor.sh/v1",
        "max_tokens",
    ),
}


def obtener_modelo(proveedor):
    especificacion = PROVEEDORES.get(proveedor)
    if especificacion is None:
        raise ValueError(f"Proveedor de IA no válido: {proveedor}")
    return getattr(config, especificacion.atributo_modelo)
