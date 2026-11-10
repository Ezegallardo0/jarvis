"""Conversión de errores de proveedores a mensajes seguros para la interfaz."""
import re

from ai_provider_config import PROVEEDORES, Proveedor


def formatear_error_publico(proveedor, error, api_key=None):
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
