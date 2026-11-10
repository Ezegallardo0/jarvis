"""Punto de entrada para configurar y usar proveedores de chat."""
import config
from ai_provider_cloudflare import CloudflareClient, _CloudflareCompletions
from ai_provider_config import PROVEEDORES, Proveedor, obtener_modelo
from ai_provider_ensemble import ensemble_activo, proveedores_configurados
from ai_provider_errors import formatear_error_publico


def crear_cliente(proveedor, api_key, timeout=30):
    especificacion = PROVEEDORES.get(proveedor)
    if especificacion is None:
        raise ValueError(f"Proveedor de IA no válido: {proveedor}")
    if not api_key:
        raise ValueError(f"Falta la API key de {especificacion.nombre}.")

    if proveedor == "cloudflare":
        account_id = config.CLOUDFLARE_ACCOUNT_ID.strip()
        if not account_id:
            raise ValueError("Configurá el Account ID de Cloudflare en Conexiones.")
        return CloudflareClient(account_id, api_key, timeout=timeout)

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
    inicio = time.perf_counter()
    cliente.chat.completions.create(**opciones)
    return round((time.perf_counter() - inicio) * 1000)


def error_publico(proveedor, error, api_key=None):
    """Resume un error del proveedor y su causa sin exponer credenciales."""
    return formatear_error_publico(proveedor, error, api_key)
