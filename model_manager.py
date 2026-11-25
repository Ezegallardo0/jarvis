"""Gestión y descarga bajo demanda de modelos de IA con verificación de integridad."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

import httpx

import config

CATALOGO_MODELOS: dict[str, dict[str, Any]] = {
    "piper-es_MX-claude-high": {
        "nombre": "Piper Voz Español (es_MX-claude-high)",
        "tipo": "tts",
        "archivo": "es_MX-claude-high.onnx",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high/es_MX-claude-high.onnx",
        "sha256": "",  # Se verifica cuando se provee hash esperado; descarga atómica
        "tamano_aprox_mb": 63,
    },
    "piper-es_MX-claude-high-json": {
        "nombre": "Piper Config Voz (es_MX-claude-high.onnx.json)",
        "tipo": "tts_config",
        "archivo": "es_MX-claude-high.onnx.json",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high/es_MX-claude-high.onnx.json",
        "sha256": "",
        "tamano_aprox_mb": 1,
    },
}


def carpeta_modelos() -> Path:
    ruta = getattr(config, "CARPETA_MODELOS", None) or (config.CARPETA_DATOS / "models")
    return Path(ruta)


def establecer_carpeta_almacenamiento(nueva_ruta_datos: str | Path | None = None, nueva_ruta_modelos: str | Path | None = None) -> dict[str, str]:
    """Permite configurar las carpetas de datos y modelos fuera del ejecutable."""
    if nueva_ruta_datos:
        p = Path(nueva_ruta_datos).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        config.CARPETA_DATOS = p
        os.environ["JARVIS_DATA_DIR"] = str(p)
    if nueva_ruta_modelos:
        pm = Path(nueva_ruta_modelos).expanduser().resolve()
        pm.mkdir(parents=True, exist_ok=True)
        config.CARPETA_MODELOS = pm
        os.environ["JARVIS_MODELS_DIR"] = str(pm)
    return {
        "data_dir": str(config.CARPETA_DATOS),
        "models_dir": str(carpeta_modelos()),
    }


def calcular_sha256(ruta: str | Path) -> str:
    h = hashlib.sha256()
    with Path(ruta).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verificar_integridad(ruta: str | Path, sha256_esperado: str = "") -> bool:
    archivo = Path(ruta)
    if not archivo.is_file() or archivo.stat().st_size == 0:
        return False
    if not sha256_esperado:
        return True
    return calcular_sha256(archivo).casefold() == sha256_esperado.strip().casefold()


def listar_modelos() -> list[dict[str, Any]]:
    base = carpeta_modelos()
    salida = []
    for mid, meta in CATALOGO_MODELOS.items():
        destino = base / meta["archivo"]
        salida.append(
            {
                "id": mid,
                "nombre": meta["nombre"],
                "tipo": meta["tipo"],
                "archivo": meta["archivo"],
                "ruta": str(destino),
                "descargado": destino.is_file() and destino.stat().st_size > 0,
                "tamano_aprox_mb": meta["tamano_aprox_mb"],
            }
        )
    return salida


def descargar_modelo(
    modelo_id: str,
    sha256_esperado: str = "",
    al_progreso: Callable[[int, int], Any] | None = None,
) -> Path:
    """Descarga bajo demanda un modelo del catálogo de forma atómica y verifica su integridad."""
    if modelo_id not in CATALOGO_MODELOS:
        raise KeyError(f"Modelo desconocido en catálogo: {modelo_id}")
    meta = CATALOGO_MODELOS[modelo_id]
    base = carpeta_modelos()
    base.mkdir(parents=True, exist_ok=True)
    destino = base / meta["archivo"]
    temporal = destino.with_suffix(destino.suffix + ".part")

    esperado = sha256_esperado or meta.get("sha256", "")
    if destino.is_file() and verificar_integridad(destino, esperado):
        return destino

    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        with client.stream("GET", meta["url"]) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            descargado = 0
            with temporal.open("wb") as out:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    out.write(chunk)
                    descargado += len(chunk)
                    if al_progreso:
                        al_progreso(descargado, total)

    if not verificar_integridad(temporal, esperado):
        temporal.unlink(missing_ok=True)
        raise ValueError(f"La verificación de integridad falló para {modelo_id}.")
    temporal.replace(destino)
    return destino
