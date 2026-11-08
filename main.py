"""Jarvis: asistente local y autónomo para tu PC.

Uso:
    python main.py            # modo voz (di "Jarvis, ...")
    python main.py --texto    # modo texto (teclado)
"""
import argparse
import sys

import config
import tools
import voice
from brain import Cerebro

SALIDAS = ("salir", "adios", "apagate", "hasta luego", "chau")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--texto", action="store_true", help="usar solo teclado")
    args = ap.parse_args()
    if args.texto:
        config.USAR_VOZ = False
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
                pedido = oido.esperar_activacion()
                if not pedido:
                    voice.hablar("Decime.")
                    pedido = oido.transcribir(oido.grabar_frase(espera_max=8))
                    if not pedido:
                        continue
                print(f"Tú: {pedido}")
                if not atender(pedido):
                    break
                # Ventana de conversación: seguimiento sin repetir la palabra de activación
                while True:
                    audio = oido.grabar_frase(espera_max=config.VENTANA_CONVERSACION_SEG)
                    pedido = oido.transcribir(audio)
                    if not pedido:
                        break
                    print(f"Tú: {pedido}")
                    if not atender(pedido):
                        return
        else:
            while True:
                pedido = input("Tú: ").strip()
                if pedido and not atender(pedido):
                    break
    except KeyboardInterrupt:
        print("\nChau.")


if __name__ == "__main__":
    main()
