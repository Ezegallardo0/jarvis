"""Habilidad modular de Spotify para JARVIS."""


def register(contexto):
    def skill_spotify_estado():
        """Consulta el estado actual de reproducción en Spotify si está conectado."""
        try:
            import spotyfyTools
        except Exception as exc:
            return f"Módulo Spotify no disponible: {exc}"
        if not spotyfyTools.configurado():
            return "Spotify no está configurado todavía. Configurá el Client ID en Conexiones."
        estado = spotyfyTools.estado_actual()
        if not estado:
            return "No hay reproducción activa o falta autorizar Spotify."
        titulo = estado.get("titulo") or "Sin título"
        artista = estado.get("artista") or "Desconocido"
        reproduciendo = "reproduciendo" if estado.get("reproduciendo") else "pausado"
        return f"Spotify ({reproduciendo}): {titulo} - {artista}."

    contexto.registrar(
        skill_spotify_estado,
        descripcion="Consulta qué está sonando actualmente en Spotify.",
        parametros={},
        requeridos=[],
        permisos=["media_control", "network"],
    )
