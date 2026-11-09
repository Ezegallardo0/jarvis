"""Voz: reconocimiento (faster-whisper, local) y síntesis (pyttsx3, local)."""
import re
import tempfile
import threading
import time
import unicodedata
import wave
from pathlib import Path

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
        if config.AUDIO_SALIDA is None:
            motor.say(limpio)
            motor.runAndWait()
            motor.stop()
        else:
            import numpy as np
            import sounddevice as sd

            with tempfile.TemporaryDirectory() as carpeta:
                ruta = Path(carpeta) / "respuesta.wav"
                motor.save_to_file(limpio, str(ruta))
                motor.runAndWait()
                motor.stop()
                with wave.open(str(ruta), "rb") as archivo:
                    if archivo.getsampwidth() != 2:
                        raise ValueError("El formato de audio de voz no es PCM de 16 bits.")
                    canales = archivo.getnchannels()
                    frecuencia = archivo.getframerate()
                    audio = np.frombuffer(
                        archivo.readframes(archivo.getnframes()), dtype="<i2"
                    ).astype(np.float32) / 32768
                if canales > 1:
                    audio = audio.reshape(-1, canales)
                salida = sd.query_devices(config.AUDIO_SALIDA)
                frecuencia_salida = int(salida["default_samplerate"])
                if frecuencia != frecuencia_salida:
                    cantidad = round(len(audio) * frecuencia_salida / frecuencia)
                    posiciones = np.linspace(0, len(audio) - 1, cantidad)
                    audio = np.stack(
                        [np.interp(posiciones, np.arange(len(audio)), audio[:, canal]) for canal in range(canales)],
                        axis=1,
                    ) if canales > 1 else np.interp(posiciones, np.arange(len(audio)), audio)
                sd.play(audio, samplerate=frecuencia_salida, device=config.AUDIO_SALIDA, blocking=True)
    except Exception as e:
        print(f"(voz no disponible: {e})")


def medir_microfono(dispositivo=None, segundos=5):
    """Devuelve el dispositivo, la frecuencia y el nivel RMS por segundo."""
    import numpy as np
    import sounddevice as sd

    if dispositivo is None:
        dispositivo = Oido._elegir_microfono_con(sd)
    info = sd.query_devices(dispositivo)
    frecuencia = int(info["default_samplerate"])
    niveles, desbordados = [], []
    with sd.InputStream(
        samplerate=frecuencia,
        channels=1,
        dtype="float32",
        blocksize=max(1, frecuencia // 10),
        device=dispositivo,
    ) as stream:
        for _ in range(segundos):
            bloques = []
            desbordado = False
            for _ in range(10):
                audio, overflow = stream.read(max(1, frecuencia // 10))
                bloques.append(audio)
                desbordado = desbordado or overflow
            nivel = float(np.sqrt(np.mean(np.concatenate(bloques) ** 2)))
            niveles.append(nivel)
            desbordados.append(desbordado)
    return info["name"], dispositivo, frecuencia, niveles, desbordados


def probar_microfono(dispositivo=None):
    """Muestra el nivel de entrada del micrófono seleccionado durante cinco segundos."""
    import sounddevice as sd

    nombre, indice, frecuencia, niveles, desbordados = medir_microfono(dispositivo)
    print(f"Probando: {nombre} (dispositivo {indice}, {frecuencia} Hz).")
    print("Hablá durante cinco segundos.")
    for segundo, nivel in enumerate(niveles, 1):
        marca = " (buffer saturado)" if desbordados[segundo - 1] else ""
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


def probar_reconocimiento(dispositivo=None):
    """Escucha una frase y devuelve lo que reconoce Whisper."""
    oido = Oido(dispositivo=dispositivo)
    print("Hablá ahora; voy a escuchar hasta 8 segundos.")
    audio = oido.grabar_frase(espera_max=8, max_seg=8)
    if audio is None:
        return "No detecté voz en la grabación. Revisá la entrada elegida y el nivel del micrófono."
    texto = oido.transcribir(audio)
    return texto or "Se grabó audio, pero Whisper no pudo reconocer palabras."


class Oido:
    def __init__(self, dispositivo=None):
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel

        self.np, self.sd = np, sd
        self.pausar_escucha = threading.Event()
        self.dispositivo = self._elegir_microfono(dispositivo)
        info = self.sd.query_devices(self.dispositivo)
        print(f"Micrófono: {info['name']} (dispositivo {self.dispositivo})")
        self.frecuencia = int(info["default_samplerate"])
        print(f"Frecuencia de captura: {self.frecuencia} Hz (se convierte a {SR} Hz para Whisper)")
        print("Cargando modelo de voz (la primera vez descarga unos cientos de MB)...")
        self.modelo = WhisperModel(config.WHISPER_MODELO, device="auto", compute_type="int8")
        self.umbral = 0.001
        self.calibrar()

    def _elegir_microfono(self, seleccion=None):
        return self._elegir_microfono_con(self.sd, seleccion)

    @staticmethod
    def _elegir_microfono_con(sd, seleccion=None):
        dispositivos = sd.query_devices()
        buscado = config.MICROFONO if seleccion is None else seleccion
        if buscado != "":
            if isinstance(buscado, int):
                indice = buscado
                if 0 <= indice < len(dispositivos) and dispositivos[indice]["max_input_channels"]:
                    return indice
            else:
                buscado = buscado.casefold()
                for indice, dispositivo in enumerate(dispositivos):
                    if dispositivo["max_input_channels"] and buscado in dispositivo["name"].casefold():
                        return indice
            disponibles = ", ".join(
                f"{indice}: {dispositivo['name']}"
                for indice, dispositivo in enumerate(dispositivos)
                if dispositivo["max_input_channels"]
            )
            raise RuntimeError(
                f"No encontré el micrófono '{buscado}'. Entradas disponibles: {disponibles}"
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
            int(self.frecuencia * 1.0),
            samplerate=self.frecuencia,
            channels=1,
            dtype="float32",
            device=self.dispositivo,
        )
        self.sd.wait()
        ruido = float(self.np.sqrt(self.np.mean(audio**2)))
        self.umbral = max(0.001, ruido * 3)
        print(f"Nivel ambiente: {ruido:.4f}; umbral de voz: {self.umbral:.4f}")

    def grabar_frase(self, espera_max=None, max_seg=15):
        """Graba hasta detectar una frase completa. Devuelve audio o None si pasó espera_max sin hablar."""
        while self.pausar_escucha.is_set():
            time.sleep(0.1)
        np = self.np
        bloque = max(1, int(self.frecuencia * 0.1))
        pre, buf = [], []
        hablando, silencio = False, 0
        inicio = time.time()
        with self.sd.InputStream(
            samplerate=self.frecuencia,
            channels=1,
            dtype="float32",
            blocksize=bloque,
            device=self.dispositivo,
        ) as s:
            while True:
                datos, _ = s.read(bloque)
                if self.pausar_escucha.is_set():
                    break
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
        if self.pausar_escucha.is_set() or not buf:
            return None
        audio = np.concatenate(buf).flatten()
        if len(audio) <= self.frecuencia * 0.4:
            return None
        if self.frecuencia != SR:
            cantidad = round(len(audio) * SR / self.frecuencia)
            audio = np.interp(
                np.linspace(0, len(audio) - 1, cantidad),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
        return audio

    def transcribir(self, audio):
        if audio is None:
            return ""
        segmentos, _ = self.modelo.transcribe(audio, language=config.IDIOMA, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segmentos).strip()

    def esperar_activacion(self, al_transcribir=None):
        """Bloquea hasta oír la palabra de activación. Devuelve lo que se dijo después (puede ser '')."""
        while True:
            texto = self.transcribir(self.grabar_frase())
            if not texto:
                continue
            if al_transcribir:
                al_transcribir(texto)
            norm = normalizar(texto)
            for palabra in config.PALABRAS_ACTIVACION:
                if palabra in norm:
                    resto = norm.split(palabra, 1)[1].strip(" ,.")
                    return resto
