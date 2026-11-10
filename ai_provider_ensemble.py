"""Selección de proveedores habilitados para el modo ensemble."""
import config

from ai_provider_config import PROVEEDORES


def proveedores_configurados(obtener_clave_fn):
    """Devuelve proveedores con API key disponible en orden de prioridad."""
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
    """Devuelve si corresponde activar ensemble y los proveedores disponibles."""
    modo = getattr(config, "MODO_ENSEMBLE", "auto").lower()
    if modo == "off":
        return False, []
    activos = proveedores_configurados(obtener_clave_fn)
    if modo == "on":
        return len(activos) >= 1, activos
    return len(activos) >= 2, activos
