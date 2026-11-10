"""Jarvis: asistente local y autónomo para tu PC.

Uso:
    python main.py            # modo voz (di "Jarvis, ...")
"""
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    proyecto = Path(__file__).resolve().parent
    entorno = proyecto / "venv"
    if Path(sys.prefix).resolve() != entorno.resolve():
        nombre_python = "pythonw.exe" if Path(sys.executable).name.lower() == "pythonw.exe" else "python.exe"
        ejecutable = entorno / "Scripts" / nombre_python
        if not ejecutable.is_file():
            raise RuntimeError(
                f"No encontré el entorno virtual del proyecto en {entorno}. "
                "Crealo e instalá requirements.txt antes de iniciar Jarvis."
            )
        os.execv(
            str(ejecutable),
            [str(ejecutable), str(Path(__file__).resolve()), *sys.argv[1:]],
        )

import argparse

import config
import tools
import visionTools
import voice
from brain import Cerebro, crear_cerebro

_ = visionTools

SALIDAS = ("salir", "adios", "apagate", "hasta luego", "chau")


def _obtener_clave(proveedor):
    try:
        import keyring
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            keyring.set_keyring(WinVaultKeyring())
        guardada = keyring.get_password("Jarvis", proveedor)
        if guardada:
            return guardada
    except Exception:
        pass
    env_var = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "cloudflare": "CLOUDFLARE_API_KEY",
        "cursor": "CURSOR_API_KEY",
        "elevenlabs": "ELEVENLABS_API_KEY",
    }.get(proveedor, "")
    return os.environ.get(env_var, "") if env_var else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probar-microfono", action="store_true", help="medir el nivel de entrada del micrófono")
    ap.add_argument("--microfono", type=int, help="índice del dispositivo de entrada de audio")
    ap.add_argument("--salida-audio", type=int, help="índice del dispositivo de salida de audio")
    args = ap.parse_args()
    if args.microfono is not None:
        config.MICROFONO = args.microfono
    if args.salida_audio is not None:
        config.AUDIO_SALIDA = args.salida_audio
    if args.probar_microfono:
        voice.probar_microfono()
        return
    config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)

    try:
        cerebro = crear_cerebro(_obtener_clave)
    except Exception as e:
        print(f"Aviso: {e}")
        cerebro = Cerebro(provider="ollama")

    error = cerebro.verificar()
    if error:
        print(error)
        sys.exit(1)

    try:
        oido = voice.Oido()
    except Exception as error:
        print(f"No pude iniciar el micrófono/reconocimiento: {error}")
        print("Revisá el micrófono y sus permisos de Windows antes de iniciar Jarvis.")
        sys.exit(1)

    clave_elevenlabs = _obtener_clave("elevenlabs")

    def hablar(texto):
        return voice.hablar(texto, api_key=clave_elevenlabs, oido=oido)

    def confirmar(texto):
        hablar(texto)
        if oido:
            respuesta = voice.normalizar(oido.transcribir(oido.grabar_frase(espera_max=8)))
            print(f"Tú (confirmación): {respuesta}")
            return any(p in respuesta.split() for p in ("si", "dale", "confirmo", "ok", "adelante", "sip"))
        return input("[s/n]: ").strip().lower().startswith(("s", "y"))

    tools.NOTIFICAR = hablar
    tools.CONFIRMAR = confirmar

    if tools.permiso_control_pc() is None:
        autorizacion = confirmar(
            "¿Autorizás a Jarvis a controlar este ordenador cuando se lo pidas? "
            "Podrá abrir aplicaciones y leer, crear, modificar o eliminar archivos "
            "en ubicaciones accesibles para tu usuario, usar integraciones conectadas "
            "y analizar capturas de pantalla necesarias para tus tareas. El análisis "
            "visual corre localmente con Ollama y no es una grabación continua. La "
            "decisión se guarda para próximos inicios y se puede revocar en Configuración. "
            "Decí sí o no."
        )
        tools.establecer_permiso_control(autorizacion)
    config.COMPARTIENDO_PANTALLA = tools.permiso_control_pc() is True

    def atender(pedido):
        """Procesa un pedido. Devuelve False si hay que salir."""
        if any(s in voice.normalizar(pedido) for s in SALIDAS):
            hablar("Hasta luego.")
            return False
        respuesta_modo = voice.cambiar_modo_silencio(pedido)
        if respuesta_modo:
            hablar(respuesta_modo)
            return True
        try:
            hablar(cerebro.responder(pedido))
        except Exception as e:
            hablar(f"Tuve un problema: {e}")
        return True

    hablar(f"Hola, soy {config.NOMBRE}. Ya estoy en línea y te escucho. ¿Qué necesitás?")
    try:
        while True:
            pedido = oido.escuchar_continuo()
            if not pedido:
                continue
            print(f"Tú: {pedido}")
            if not atender(pedido):
                break
    except KeyboardInterrupt:
        print("\nChau.")


if __name__ == "__main__":
    main()
