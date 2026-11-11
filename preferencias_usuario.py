"""Persistencia local de preferencias de identidad del usuario."""
import json
from pathlib import Path


def cargar_nombre_usuario(ruta):
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    if not isinstance(datos, dict):
        raise ValueError("El archivo de preferencias no contiene un objeto JSON.")
    nombre = datos.get("nombre_usuario", "")
    if not isinstance(nombre, str):
        return ""
    return nombre.strip()[:60]


def guardar_nombre_usuario(ruta, nombre):
    nombre = nombre.strip()
    if not nombre or len(nombre) > 60:
        raise ValueError("El nombre debe tener entre 1 y 60 caracteres.")
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(
        json.dumps({"nombre_usuario": nombre}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporal.replace(ruta)
    return nombre
