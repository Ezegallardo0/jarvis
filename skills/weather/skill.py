"""Habilidad modular de clima para JARVIS."""
import config
import tools


def register(contexto):
    def skill_clima_ciudad(ciudad="", cuando="hoy"):
        """Consulta el clima para una ciudad o usa la ubicación configurada."""
        destino = (ciudad or "").strip() or (config.UBICACION or "").strip()
        if not destino:
            return "Indicame una ciudad o configurá tu ubicación en JARVIS."
        return tools.consultar_clima(destino, cuando=cuando)

    contexto.registrar(
        skill_clima_ciudad,
        descripcion="Consulta el pronóstico del clima usando la habilidad modular de clima.",
        parametros={
            "ciudad": {"type": "string", "description": "Ciudad a consultar (opcional si hay ubicación)"},
            "cuando": {"type": "string", "enum": ["hoy", "manana", "mañana"], "description": "Día"},
        },
        requeridos=[],
        permisos=["network"],
    )
