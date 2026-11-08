"""Voz: reconocimiento (faster-whisper, local) y síntesis (pyttsx3, local)."""
import re
import time
import unicodedata

import config

SR = 16000


def normalizar(texto):
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", texto)


def hablar(texto):
    print(f"{config.NOMBRE}: {texto}")
    if not config.USAR_VOZ:
        return
    limpio = re.sub(r"[*#`_]", "", texto)
    try:
        import pyttsx3

        motor = pyttsx3.init()  # un motor nuevo por frase evita cuelgues en Windows
        for v in motor.getProperty("voices"):
            nombre = (v.name + v.id).lower()
            if "spanish" in nombre or "español" in nombre or "es-" in nombre or "_es_" in nombre:
                motor.setProperty("voice", v.id)
                break
        motor.setProperty("rate", 175)
        motor.say(limpio)
        motor.runAndWait()
        motor.stop()
    except Exception as e:
        print(f"(voz no disponible: {e})")


def probar_microfono():
    """Muestra el nivel de entrada del micrófono seleccionado durante cinco segundos."""
    import numpy as np
    import sounddevice as sd

    dispositivo = Oido._elegir_microfono_con(sd)
    info = sd.query_devices(dispositivo)
    frecuencia = int(info["default_samplerate"])
    print(f"Probando: {info['name']} (dispositivo {dispositivo}, {frecuencia} Hz).")
    print("Hablá durante la prueba; voy a medir cinco segundos.")
    niveles = []
    with sd.InputStream(
        samplerate=frecuencia,
        channels=1,
        dtype="float32",
        blocksize=frecuencia,
        device=dispositivo,
    ) as stream:
        for segundo in range(1, 6):
            audio, desbordado = stream.read(frecuencia)
            nivel = float(np.sqrt(np.mean(audio**2)))
            niveles.append(nivel)
            marca = " (buffer saturado)" if desbordado else ""
            print(f"  {segundo}/5: nivel {nivel:.5f}{marca}")
    if max(niveles, default=0) < 0.001:
        print("No se detectó señal. Revisá el permiso y el nivel de entrada en Windows, o probá otro índice MICROFONO en config.py.")
        print("Dispositivos de entrada disponibles:")
        for indice, dispositivo_info in enumerate(sd.query_devices()):
            if dispositivo_info["max_input_channels"]:
                api = sd.query_hostapis(dispositivo_info["hostapi"])["name"]
                print(f"  {indice}: {dispositivo_info['name']} ({api})")
        print("Para probar otro, poné MICROFONO = índice en config.py y volvé a ejecutar esta prueba.")
    else:
        print("Se detectó señal de audio en el micrófono.")


class Oido:
    def __init__(self):
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel

        self.np, self.sd = np, sd
        self.dispositivo = self._elegir_microfono()
        info = self.sd.query_devices(self.dispositivo)
        print(f"Micrófono: {info['name']} (dispositivo {self.dispositivo})")
        print("Cargando modelo de voz (la primera vez descarga unos cientos de MB)...")
        self.modelo = WhisperModel(config.WHISPER_MODELO, device="auto", compute_type="int8")
        self.umbral = 0.004
        self.calibrar()

    def _elegir_microfono(self):
        return self._elegir_microfono_con(self.sd)

    @staticmethod
    def _elegir_microfono_con(sd):
        dispositivos = sd.query_devices()
        if config.MICROFONO != "":
            if isinstance(config.MICROFONO, int):
                indice = config.MICROFONO
                if 0 <= indice < len(dispositivos) and dispositivos[indice]["max_input_channels"]:
                    return indice
            else:
                buscado = config.MICROFONO.casefold()
                for indice, dispositivo in enumerate(dispositivos):
                    if dispositivo["max_input_channels"] and buscado in dispositivo["name"].casefold():
                        return indice
            disponibles = ", ".join(
                f"{indice}: {dispositivo['name']}"
                for indice, dispositivo in enumerate(dispositivos)
                if dispositivo["max_input_channels"]
            )
            raise RuntimeError(
                f"No encontré el micrófono '{config.MICROFONO}'. Entradas disponibles: {disponibles}"
            )
        predeterminado = sd.default.device[0]
        if predeterminado is None or predeterminado < 0:
            predeterminado = next(
                (indice for indice, dispositivo in enumerate(dispositivos)
                 if dispositivo["max_input_channels"]),
                None,
            )
        if predeterminado is None:
            raise RuntimeError("Windows no detecta ningún dispositivo de entrada de audio.")
        return predeterminado

    def calibrar(self):
        """Mide el ruido ambiente para fijar el umbral de detección de voz."""
        audio = self.sd.rec(
            int(SR * 1.0),
            samplerate=SR,
            channels=1,
            dtype="float32",
            device=self.dispositivo,
        )
        self.sd.wait()
        ruido = float(self.np.sqrt(self.np.mean(audio**2)))
        self.umbral = max(0.004, ruido * 3)
        print(f"Nivel ambiente: {ruido:.4f}; umbral de voz: {self.umbral:.4f}")

    def grabar_frase(self, espera_max=None, max_seg=15):
        """Graba hasta detectar una frase completa. Devuelve audio o None si pasó espera_max sin hablar."""
        np = self.np
        bloque = int(SR * 0.1)
        pre, buf = [], []
        hablando, silencio = False, 0
        inicio = time.time()
        with self.sd.InputStream(
            samplerate=SR,
            channels=1,
            dtype="float32",
            blocksize=bloque,
            device=self.dispositivo,
        ) as s:
            while True:
                datos, _ = s.read(bloque)
                nivel = float(np.sqrt(np.mean(datos**2)))
                if nivel > self.umbral:
                    if not hablando:
                        buf = list(pre)
                    hablando, silencio = True, 0
                    buf.append(datos)
                elif hablando:
                    buf.append(datos)
                    silencio += 1
                    if silencio >= 8:  # ~0,8 s de silencio = fin de frase
                        break
                else:
                    pre = (pre + [datos])[-3:]
                    if espera_max and time.time() - inicio > espera_max:
                        return None
                if hablando and len(buf) * 0.1 > max_seg:
                    break
        audio = np.concatenate(buf).flatten()
        return audio if len(audio) > SR * 0.4 else None

    def transcribir(self, audio):
        if audio is None:
            return ""
        segmentos, _ = self.modelo.transcribe(audio, language=config.IDIOMA, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segmentos).strip()

    def esperar_activacion(self):
        """Bloquea hasta oír la palabra de activación. Devuelve lo que se dijo después (puede ser '')."""
        while True:
            texto = self.transcribir(self.grabar_frase())
            if not texto:
                continue
            norm = normalizar(texto)
            for palabra in config.PALABRAS_ACTIVACION:
                if palabra in norm:
                    resto = norm.split(palabra, 1)[1].strip(" ,.")
                    return resto
