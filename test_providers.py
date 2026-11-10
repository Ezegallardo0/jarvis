"""Prueba rápida de proveedores de IA configurados (sin mostrar API keys).

Uso:
    venv\\Scripts\\python.exe test_providers.py
    venv\\Scripts\\python.exe test_providers.py gemini groq
"""
import os
import sys

import ai_providers
import config

try:
    import keyring
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring
        keyring.set_keyring(WinVaultKeyring())
except Exception:
    keyring = None


def obtener_clave(proveedor):
    env_var = getattr(ai_providers.PROVEEDORES.get(proveedor), "variable_entorno", "")
    if env_var:
        valor = os.environ.get(env_var, "")
        if valor:
            return valor
    if keyring is None:
        return ""
    try:
        return keyring.get_password("Jarvis", proveedor) or ""
    except Exception:
        return ""


def main():
    proveedores = sys.argv[1:] or list(ai_providers.PROVEEDORES.keys())
    for proveedor in proveedores:
        if proveedor not in ai_providers.PROVEEDORES:
            print(f"[{proveedor}] desconocido")
            continue
        clave = obtener_clave(proveedor)
        modelo = ai_providers.obtener_modelo(proveedor)
        if not clave:
            print(f"[{proveedor}] SIN CLAVE | modelo={modelo}")
            continue
        extra = ""
        if proveedor == "cloudflare":
            extra = f" | account_id={'configurado' if config.CLOUDFLARE_ACCOUNT_ID.strip() else 'FALTA'}"
        try:
            ms = ai_providers.probar_conexion(proveedor, clave, modelo=modelo, timeout=20)
            print(f"[{proveedor}] OK | modelo={modelo} | {ms} ms{extra}")
        except Exception as error:
            detalle = ai_providers.error_publico(proveedor, error, clave)
            print(f"[{proveedor}] ERROR | modelo={modelo}{extra}\n  -> {detalle}")


if __name__ == "__main__":
    main()
