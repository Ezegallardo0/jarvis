"""Habilidad modular de GitHub para JARVIS."""
import tools


def register(contexto):
    def skill_github_resumen_repo(repositorio):
        """Obtiene resumen rápido (estrellas, descripción y lenguaje) de un repositorio de GitHub."""
        return tools.buscar_repositorios_github(repositorio, limite=3)

    contexto.registrar(
        skill_github_resumen_repo,
        descripcion="Obtiene un resumen de un repositorio en GitHub usando la habilidad modular GitHub.",
        parametros={
            "repositorio": {"type": "string", "description": "Nombre o ruta owner/repo"},
        },
        requeridos=["repositorio"],
        permisos=["network"],
    )
