"""Configuración compartida para proveedores de chat compatibles con OpenAI."""
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
    "grok": Proveedor(
        "Grok",
        "XAI_API_KEY",
        "MODELO_GROK",
        "https://api.x.ai/v1",
    ),
    "openai": Proveedor(
        "OpenAI",
        "OPENAI_API_KEY",
        "MODELO_OPENAI",
    ),
    "cursor": Proveedor(
        "Cursor (Grok Free)",
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


def crear_cliente(proveedor, api_key, timeout=30):
    especificacion = PROVEEDORES.get(proveedor)
    if especificacion is None:
        raise ValueError(f"Proveedor de IA no válido: {proveedor}")
    if not api_key:
        raise ValueError(f"Falta la API key de {especificacion.nombre}.")

    from openai import OpenAI

    opciones = {
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": 0,
    }
    if especificacion.base_url:
        opciones["base_url"] = especificacion.base_url
    return OpenAI(**opciones)


def crear_opciones_solicitud(proveedor, modelo, mensajes, herramientas=None):
    especificacion = PROVEEDORES.get(proveedor)
    if especificacion is None:
        raise ValueError(f"Proveedor de IA no válido: {proveedor}")
    opciones = {
        "model": modelo,
        "messages": mensajes,
        especificacion.limite_tokens: 400,
    }
    if herramientas is not None:
        opciones["tools"] = herramientas
        opciones["tool_choice"] = "auto"
    if proveedor == "openai":
        opciones["reasoning_effort"] = "none"
    return opciones


def probar_conexion(proveedor, api_key, modelo=None, timeout=15):
    """Envía un prompt fijo y acotado; devuelve solo latencia, nunca la respuesta."""
    import time

    cliente = crear_cliente(proveedor, api_key, timeout=timeout)
    modelo = modelo or obtener_modelo(proveedor)
    especificacion = PROVEEDORES[proveedor]
    opciones = {
        "model": modelo,
        "messages": [{"role": "user", "content": "Respondé solo: OK"}],
        especificacion.limite_tokens: 8,
        "timeout": timeout,
    }
    if proveedor == "openai":
        opciones["reasoning_effort"] = "none"
    inicio = time.perf_counter()
    cliente.chat.completions.create(**opciones)
    return round((time.perf_counter() - inicio) * 1000)


def error_publico(proveedor, error):
    """Resume el error sin incluir texto del servidor ni datos de autenticación."""
    nombre = PROVEEDORES.get(proveedor, Proveedor(proveedor, "", "")).nombre
    codigo = getattr(error, "status_code", None)
    if codigo == 401:
        detalle = "API key rechazada; revisá la clave guardada para este proveedor."
    elif codigo == 403:
        detalle = "Acceso denegado; revisá permisos, facturación o disponibilidad regional."
    elif codigo == 404:
        detalle = "No se encontró el modelo o endpoint; revisá el ID y el acceso del proyecto."
    elif codigo == 429:
        detalle = "Se alcanzó un límite o cuota; revisá el plan y volvé a intentar."
    elif codigo is not None and codigo >= 500:
        detalle = "El proveedor no está disponible temporalmente."
    elif "timeout" in type(error).__name__.casefold():
        detalle = "La solicitud superó el tiempo de espera."
    elif "connection" in type(error).__name__.casefold():
        detalle = "No se pudo establecer conexión con el proveedor."
    else:
        detalle = "La prueba de conexión falló."
    sufijo = f" (HTTP {codigo})" if codigo is not None else ""
    return f"{nombre}: {detalle}{sufijo}"


# ---------------------------------------------------------------------------
# Utilidades para modo Ensemble (múltiples IAs en paralelo)
# ---------------------------------------------------------------------------

def proveedores_configurados(obtener_clave_fn):
    """Devuelve la lista de proveedores que tienen API key disponible.

    Args:
        obtener_clave_fn: función(proveedor) -> str|"" que lee la API key.

    Returns:
        Lista de códigos de proveedor con clave válida, en orden: primero el
        PROVEEDOR_IA principal (si tiene clave), luego los demás.
    """
    principal = config.PROVEEDOR_IA
    orden = [principal] + [p for p in PROVEEDORES if p != principal]
    resultado = []
    for codigo in orden:
        if codigo not in PROVEEDORES:
            continue
        try:
            clave = obtener_clave_fn(codigo)
        except Exception:
            clave = ""
        if clave:
            resultado.append(codigo)
    return resultado


def ensemble_activo(obtener_clave_fn):
    """Indica si el modo ensemble debe activarse según configuración y claves.

    Returns:
        (activo: bool, proveedores: list[str])
    """
    modo = getattr(config, "MODO_ENSEMBLE", "auto").lower()
    if modo == "off":
        return False, []
    activos = proveedores_configurados(obtener_clave_fn)
    if modo == "on":
        return len(activos) >= 1, activos
    # auto: ensemble solo cuando hay 2 o más proveedores con clave
    return len(activos) >= 2, activos
