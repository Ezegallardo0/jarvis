"""Configuración compartida para proveedores de chat compatibles con OpenAI."""
from dataclasses import dataclass
import json
import re
import uuid

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


class _ObjetoSimple:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self, exclude_none=True):
        def convertir(valor):
            if isinstance(valor, _ObjetoSimple):
                return valor.model_dump(exclude_none=exclude_none)
            if isinstance(valor, list):
                return [convertir(v) for v in valor]
            if isinstance(valor, dict):
                return {k: convertir(v) for k, v in valor.items() if not (exclude_none and v is None)}
            return valor
        datos = convertir(self.__dict__)
        if exclude_none:
            return {k: v for k, v in datos.items() if v is not None}
        return datos


class _CloudflareCompletions:
    def __init__(self, account_id, api_token, timeout=30):
        self.account_id = account_id
        self.api_token = api_token
        self.timeout = timeout

    def create(self, **opciones):
        import httpx

        modelo = opciones.get("model") or config.MODELO_CLOUDFLARE
        mensajes = self._convertir_mensajes(opciones.get("messages", []))
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{modelo}"
        headers = {
            "Authorization": "Bearer " + self.api_token,
            "Content-Type": "application/json",
        }
        payload = {"messages": mensajes}
        for clave in ("max_tokens", "temperature", "stream"):
            if clave in opciones and opciones[clave] is not None:
                payload[clave] = opciones[clave]
        if opciones.get("tools") is not None:
            payload["tools"] = self._convertir_herramientas(opciones["tools"])

        respuesta = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
        try:
            datos = respuesta.json()
        except Exception:
            datos = {"errors": [{"message": respuesta.text}]}
        if respuesta.status_code >= 400 or not datos.get("success", True):
            mensaje = None
            errores = datos.get("errors") or []
            if errores and isinstance(errores[0], dict):
                mensaje = errores[0].get("message")
            mensaje = mensaje or datos.get("result") or respuesta.text or f"HTTP {respuesta.status_code}"
            error = RuntimeError(str(mensaje))
            error.status_code = respuesta.status_code
            error.body = datos
            raise error

        resultado = datos.get("result", {}) or {}
        contenido = (
            resultado.get("response")
            or resultado.get("result")
            or resultado.get("text")
            or ""
        )
        if isinstance(contenido, dict):
            contenido = json.dumps(contenido, ensure_ascii=False)
        llamadas = []
        for llamada in resultado.get("tool_calls") or []:
            nombre = llamada.get("name")
            if not nombre:
                continue
            argumentos = llamada.get("arguments") or {}
            if isinstance(argumentos, str):
                try:
                    argumentos = json.loads(argumentos)
                except json.JSONDecodeError:
                    pass
            if not isinstance(argumentos, str):
                argumentos = json.dumps(argumentos, ensure_ascii=False)
            funcion = _ObjetoSimple(name=nombre, arguments=argumentos)
            llamadas.append(
                _ObjetoSimple(
                    id=f"call_{uuid.uuid4().hex}",
                    type="function",
                    function=funcion,
                )
            )
        mensaje = _ObjetoSimple(
            role="assistant",
            content=str(contenido).strip(),
            tool_calls=llamadas or None,
        )
        eleccion = _ObjetoSimple(
            message=mensaje,
            finish_reason="tool_calls" if llamadas else "stop",
        )
        return _ObjetoSimple(choices=[eleccion])

    @staticmethod
    def _convertir_mensajes(mensajes):
        convertidos = []
        for mensaje in mensajes:
            llamadas = mensaje.get("tool_calls") or []
            if mensaje.get("role") == "assistant" and llamadas:
                for llamada in llamadas:
                    funcion = llamada.get("function", {})
                    argumentos = funcion.get("arguments") or {}
                    if isinstance(argumentos, str):
                        try:
                            argumentos = json.loads(argumentos)
                        except json.JSONDecodeError:
                            pass
                    convertidos.append(
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "name": funcion.get("name", ""),
                                    "arguments": argumentos,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
            elif mensaje.get("role") == "tool":
                convertidos.append(
                    {"role": "tool", "content": mensaje.get("content", "")}
                )
            else:
                convertidos.append(
                    {
                        "role": mensaje.get("role", "user"),
                        "content": mensaje.get("content", ""),
                    }
                )
        return convertidos

    @staticmethod
    def _convertir_herramientas(herramientas):
        convertidas = []
        for herramienta in herramientas:
            funcion = herramienta.get("function")
            if herramienta.get("type") == "function" and isinstance(funcion, dict):
                convertidas.append(funcion)
            else:
                convertidas.append(herramienta)
        return convertidas


class _CloudflareChat:
    def __init__(self, account_id, api_token, timeout=30):
        self.completions = _CloudflareCompletions(account_id, api_token, timeout=timeout)


class CloudflareClient:
    def __init__(self, account_id, api_token, timeout=30):
        self.chat = _CloudflareChat(account_id, api_token, timeout=timeout)


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
    nombre = PROVEEDORES.get(proveedor, Proveedor(proveedor, "", "")).nombre
    codigo = getattr(error, "status_code", None)
    partes = []
    cuerpo = getattr(error, "body", None)
    if isinstance(cuerpo, dict):
        causa = cuerpo.get("error", cuerpo)
        if isinstance(causa, dict):
            msg = causa.get("message") or causa.get("status") or causa.get("code") or causa
            partes.append(str(msg))
            for clave in ("type", "param", "code"):
                valor = causa.get(clave)
                if valor and str(valor) not in str(msg):
                    partes.append(f"{clave}={valor}")
        else:
            partes.append(str(causa))
    elif cuerpo not in (None, ""):
        partes.append(str(cuerpo))

    for attr in ("message",):
        valor = getattr(error, attr, None)
        if valor and str(valor) not in " ".join(partes):
            partes.append(str(valor))

    respuesta = getattr(error, "response", None)
    if respuesta is not None:
        try:
            texto_resp = respuesta.text
        except Exception:
            texto_resp = ""
        if texto_resp and texto_resp not in " ".join(partes):
            partes.append(str(texto_resp))

    if not partes:
        partes.append(str(error))

    causa = " | ".join(p for p in partes if p)
    if api_key:
        causa = causa.replace(api_key, "[API key oculta]")
    causa = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[API key oculta]", causa)
    causa = re.sub(r"\bxai-[A-Za-z0-9_-]{12,}\b", "[API key oculta]", causa)
    causa = re.sub(r"\bgsk_[A-Za-z0-9_-]{12,}\b", "[API key oculta]", causa)
    causa = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[API key oculta]", causa)
    causa = re.sub(r"\s+", " ", causa).strip()[:500]
    detalle = f" Detalle: {causa}" if causa else ""

    if codigo == 401:
        detalle = "API key rechazada; revisá la clave guardada para este proveedor." + detalle
    elif codigo == 403:
        detalle = (
            "Acceso denegado; revisá permisos, facturación o disponibilidad regional."
            + detalle
        )
    elif codigo == 404:
        detalle = (
            "No se encontró el modelo o endpoint; revisá el ID y el acceso del proyecto."
            + detalle
        )
    elif codigo == 429:
        detalle = (
            "Se alcanzó un límite o cuota; revisá el plan y volvé a intentar."
            + detalle
        )
    elif codigo is not None and codigo >= 500:
        detalle = (
            "El proveedor no está disponible temporalmente."
            + detalle
        )
    elif "timeout" in type(error).__name__.casefold():
        detalle = "La solicitud superó el tiempo de espera."
        if causa:
            detalle += f" Detalle: {causa}"
    elif "connection" in type(error).__name__.casefold():
        detalle = "No se pudo establecer conexión con el proveedor."
        if causa:
            detalle += f" Detalle: {causa}"
    elif codigo is not None:
        detalle = (
            f"El proveedor rechazó la solicitud (HTTP {codigo})."
            + detalle
        )
    else:
        detalle = causa or "La prueba de conexión falló."
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
