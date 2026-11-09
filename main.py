"""Jarvis: asistente local y autónomo para tu PC.

Uso:
    python main.py            # modo voz (di "Jarvis, ...")
    python main.py --texto    # modo texto (teclado)
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
from brain import Cerebro

_ = visionTools

SALIDAS = ("salir", "adios", "apagate", "hasta luego", "chau")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--texto", action="store_true", help="usar solo teclado")
    ap.add_argument("--probar-microfono", action="store_true", help="medir el nivel de entrada del micrófono")
    ap.add_argument("--microfono", type=int, help="índice del dispositivo de entrada de audio")
    ap.add_argument("--salida-audio", type=int, help="índice del dispositivo de salida de audio")
    args = ap.parse_args()
    if args.microfono is not None:
        config.MICROFONO = args.microfono
    if args.salida_audio is not None:
        config.AUDIO_SALIDA = args.salida_audio
    if args.texto:
        config.USAR_VOZ = False
    if args.probar_microfono:
        voice.probar_microfono()
        return
    config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)

    cerebro = Cerebro()
    error = cerebro.verificar()
    if error:
        print(error)
        sys.exit(1)

    oido = None
    if config.USAR_VOZ:
        try:
            oido = voice.Oido()
        except Exception as e:
            print(f"No pude iniciar el micrófono/reconocimiento ({e}). Sigo en modo texto.")
            config.USAR_VOZ = False

    def confirmar(texto):
        voice.hablar(texto)
        if oido:
            respuesta = voice.normalizar(oido.transcribir(oido.grabar_frase(espera_max=8)))
            print(f"Tú (confirmación): {respuesta}")
            return any(p in respuesta.split() for p in ("si", "dale", "confirmo", "ok", "adelante", "sip"))
        return input("[s/n]: ").strip().lower().startswith(("s", "y"))

    tools.NOTIFICAR = voice.hablar
    tools.CONFIRMAR = confirmar

    def atender(pedido):
        """Procesa un pedido. Devuelve False si hay que salir."""
        if any(s in voice.normalizar(pedido) for s in SALIDAS):
            voice.hablar("Hasta luego.")
            return False
        try:
            voice.hablar(cerebro.responder(pedido))
        except Exception as e:
            voice.hablar(f"Tuve un problema: {e}")
        return True

    voice.hablar(f"{config.NOMBRE} en línea.")
    try:
        if oido:
            while True:
                pedido = oido.escuchar_continuo()
                if not pedido:
                    continue
                print(f"Tú: {pedido}")
                if not atender(pedido):
                    break
        else:
            while True:
                pedido = input("Tú: ").strip()
                if pedido and not atender(pedido):
                    break
    except KeyboardInterrupt:
        print("\nChau.")


if __name__ == "__main__":
    main()
