"""Habilidad modular de gestión segura de archivos para JARVIS."""


def register(contexto):
    def skill_resumen_carpeta(ruta):
        """Resume archivos y subcarpetas en una ruta autorizada."""
        objetivo = contexto.verificar_ruta_permitida(ruta)
        if not objetivo.is_dir():
            return f"La ruta {objetivo} no es una carpeta válida."
        archivos = 0
        carpetas = 0
        ejemplos = []
        for entrada in sorted(objetivo.iterdir(), key=lambda p: p.name.casefold()):
            if entrada.is_dir():
                carpetas += 1
            else:
                archivos += 1
            if len(ejemplos) < 10:
                ejemplos.append(f"{'[DIR]' if entrada.is_dir() else '[FILE]'} {entrada.name}")
        detalle = "\n".join(ejemplos) if ejemplos else "(carpeta vacía)"
        return f"Carpeta {objetivo}: {carpetas} carpetas, {archivos} archivos.\n{detalle}"

    contexto.registrar(
        skill_resumen_carpeta,
        descripcion="Resume el contenido de una carpeta dentro de las rutas permitidas.",
        parametros={
            "ruta": {"type": "string", "description": "Ruta de la carpeta a inspeccionar"},
        },
        requeridos=["ruta"],
        permisos=["filesystem_read"],
    )
