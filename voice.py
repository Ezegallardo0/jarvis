"""Voz: reconocimiento local y síntesis local o mediante ElevenLabs."""
import os
import re
import tempfile
import threading
import time
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import config

SR = 16000
_PIPER_REVISION = "e7229f7d33a10e145e109b886d661984201ac3c4"
_PIPER_FILES = {
    f"{config.PIPER_MODELO_VOZ}.onnx": 63122309,
    f"{config.PIPER_MODELO_VOZ}.onnx.json": 4963,
}
_PIPER_VOICE = None
_PIPER_LOCK = threading.RLock()


@dataclass(frozen=True)
class InterrupcionVoz:
    texto: str


def normalizar(texto):
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", texto)


def _ajustar_nivel_audio(audio):
    """Amplifica habla tenue sin permitir que el aumento exceda 8x."""
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32)
    if not audio.size:
        return audio
    bloque = max(1, SR // 50)
    niveles = [
        float(np.sqrt(np.mean(audio[inicio:inicio + bloque] ** 2)))
        for inicio in range(0, len(audio), bloque)
        if audio[inicio:inicio + bloque].size
    ]
    nivel_referencia = float(np.percentile(niveles, 90)) if niveles else 0
    if nivel_referencia <= 0:
        return audio
    ganancia = min(8.0, max(1.0, 0.06 / nivel_referencia))
    return np.clip(audio * ganancia, -1.0, 1.0)


def hablar(texto, api_key=None, oido=None):
    print(f"{config.NOMBRE}: {texto}")
    if not config.USAR_VOZ:
        return None
    limpio = texto
    if config.PROVEEDOR_TTS == "elevenlabs":
        try:
            return _hablar_elevenlabs(limpio, api_key, oido)
        except Exception as error:
            mensaje = f"No se pudo generar la voz de ElevenLabs: {error}"
            print(f"({mensaje})")
            return mensaje
    if config.PROVEEDOR_TTS == "piper":
        try:
            return _hablar_piper(limpio, oido)
        except Exception as error:
            mensaje = f"No se pudo generar la voz local de Piper: {error}"
            print(f"({mensaje})")
            return mensaje

    try:
        import pyttsx3

        motor = pyttsx3.init()  # un motor nuevo por frase evita cuelgues en Windows
        for v in motor.getProperty("voices"):
            nombre = (v.name + v.id).lower()
            if "spanish" in nombre or "español" in nombre or "es-" in nombre or "_es_" in nombre:
                motor.setProperty("voice", v.id)
                break
        motor.setProperty("rate", 175)
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "respuesta.wav"
            motor.save_to_file(limpio, str(ruta))
            motor.runAndWait()
            motor.stop()
            audio, frecuencia = _leer_wav(ruta)
            return _reproducir_audio(audio, frecuencia, oido)
    except Exception as e:
        print(f"(voz no disponible: {e})")
    return None


def _cargar_voz_piper():
    global _PIPER_VOICE

    with _PIPER_LOCK:
        if _PIPER_VOICE is not None:
            return _PIPER_VOICE

        import httpx
        from piper import PiperVoice

        carpeta = config.CARPETA_DATOS / "voices" / config.PIPER_MODELO_VOZ
        carpeta.mkdir(parents=True, exist_ok=True)
        for nombre, tamaño_esperado in _PIPER_FILES.items():
            destino = carpeta / nombre
            if destino.is_file() and destino.stat().st_size == tamaño_esperado:
                continue

            ruta_repo = f"es/es_MX/claude/high/{nombre}"
            url = (
                "https://huggingface.co/rhasspy/piper-voices/resolve/"
                f"{_PIPER_REVISION}/{ruta_repo}"
            )
            temporal = destino.with_name(f"{destino.name}.part")
            descargado = 0
            try:
                with httpx.stream(
                    "GET",
                    url,
                    follow_redirects=True,
                    timeout=httpx.Timeout(120, connect=30),
                ) as respuesta:
                    respuesta.raise_for_status()
                    with temporal.open("wb") as archivo:
                        for bloque in respuesta.iter_bytes():
                            archivo.write(bloque)
                            descargado += len(bloque)
                if descargado != tamaño_esperado:
                    raise RuntimeError(
                        f"Descarga incompleta de {nombre}: "
                        f"{descargado} de {tamaño_esperado} bytes."
                    )
                temporal.replace(destino)
            except Exception:
                temporal.unlink(missing_ok=True)
                raise

        _PIPER_VOICE = PiperVoice.load(str(carpeta / f"{config.PIPER_MODELO_VOZ}.onnx"))
        return _PIPER_VOICE


def _leer_wav(ruta):
    import numpy as np

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
    return audio, frecuencia


def _reproducir_audio(audio, frecuencia, oido=None):
    import numpy as np
    import sounddevice as sd

    salida = (
        sd.query_devices(kind="output")
        if config.AUDIO_SALIDA is None
        else sd.query_devices(config.AUDIO_SALIDA, "output")
    )
    frecuencia_salida = int(salida["default_samplerate"])
    if frecuencia != frecuencia_salida:
        cantidad = round(len(audio) * frecuencia_salida / frecuencia)
        posiciones = np.linspace(0, len(audio) - 1, cantidad)
        if getattr(audio, "ndim", 1) > 1:
            audio = np.stack(
                [
                    np.interp(posiciones, np.arange(len(audio)), audio[:, canal])
                    for canal in range(audio.shape[1])
                ],
                axis=1,
            )
        else:
            audio = np.interp(posiciones, np.arange(len(audio)), audio)
    if oido:
        return oido.reproducir_interrumpible(
            audio, frecuencia_salida, config.AUDIO_SALIDA
        )
    sd.play(
        audio,
        samplerate=frecuencia_salida,
        device=config.AUDIO_SALIDA,
        blocking=True,
    )
    return None


def _hablar_piper(texto, oido=None):
    with _PIPER_LOCK:
        voz = _cargar_voz_piper()
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "respuesta.wav"
            with wave.open(str(ruta), "wb") as archivo:
                voz.synthesize_wav(texto, archivo)
            audio, frecuencia = _leer_wav(ruta)
            return _reproducir_audio(audio, frecuencia, oido)


def probar_elevenlabs(texto, api_key, voice_id=None, oido=None):
    """Genera y reproduce una frase usando la API de ElevenLabs directamente."""
    if not texto.strip():
        raise ValueError("El texto de prueba está vacío.")
    _hablar_elevenlabs(texto, api_key, oido, voice_id=voice_id)
    return 200


def _hablar_elevenlabs(texto, api_key, oido=None, voice_id=None):
    import httpx
    import numpy as np

    clave = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    id_voz = (voice_id or config.ELEVENLABS_VOICE_ID).strip()
    if not clave:
        raise ValueError(
            "Falta ELEVENLABS_API_KEY. Ingresala en la interfaz o definila como variable de entorno."
        )
    if not id_voz or id_voz == "TU_VOICE_ID":
        raise ValueError(
            "Falta un Voice ID válido. Copialo desde la voz elegida en ElevenLabs."
        )
    if not texto.strip():
        return

    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{id_voz}"
    )
    try:
        respuesta = httpx.post(
            url,
            params={"output_format": "pcm_24000"},
            headers={"xi-api-key": clave, "Content-Type": "application/json"},
            json={"text": texto, "model_id": "eleven_multilingual_v2"},
            timeout=60,
        )
    except httpx.RequestError as error:
        raise RuntimeError(f"No pude conectar con ElevenLabs: {error}") from error

    if not respuesta.is_success:
        detalle = ""
        try:
            cuerpo = respuesta.json()
            detalle = cuerpo.get("detail", cuerpo)
            if isinstance(detalle, dict):
                detalle = detalle.get("message", detalle)
                if isinstance(detalle, dict):
                    detalle = detalle.get("message", detalle)
            detalle = str(detalle).replace(clave, "[API key oculta]")[:500]
        except (ValueError, AttributeError):
            detalle = respuesta.text[:500].replace(clave, "[API key oculta]")
        sufijo = f" Detalle: {detalle}" if detalle else ""
    else:
        sufijo = ""

    if respuesta.status_code == 401:
        raise RuntimeError(
            "ElevenLabs devolvió HTTP 401: la API key no es válida, fue revocada "
            "o está mal copiada. Usá una clave creada en ElevenLabs, no la de Gemini/OpenAI."
            f"{sufijo}"
        )
    if respuesta.status_code == 403:
        raise RuntimeError(
            f"ElevenLabs devolvió HTTP 403: la clave no tiene permiso para usar "
            f"la voz {id_voz}, o la voz no está disponible en esa cuenta. "
            f"Revisá permisos de la clave y acceso a la voz.{sufijo}"
        )
    if respuesta.status_code == 402:
        raise RuntimeError(
            "ElevenLabs devolvió HTTP 402: el plan gratuito no permite usar voces "
            "de la biblioteca mediante la API. Actualizá el plan de ElevenLabs o "
            "seguí usando Piper, que funciona gratis y localmente."
            f"{sufijo}"
        )
    if respuesta.status_code == 429:
        raise RuntimeError(
            f"ElevenLabs alcanzó el límite de uso (HTTP 429). Revisá tu plan y cuota.{sufijo}"
        )
    if not respuesta.is_success:
        raise RuntimeError(
            f"ElevenLabs respondió con HTTP {respuesta.status_code}.{sufijo}"
        )
    if not respuesta.content or len(respuesta.content) % 2:
        raise RuntimeError("ElevenLabs devolvió audio PCM vacío o inválido.")

    audio = np.frombuffer(respuesta.content, dtype="<i2").astype(np.float32) / 32768
    return _reproducir_audio(audio, 24000, oido)


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
        self.bloqueo_audio = threading.Lock()
        self.dispositivo = self._elegir_microfono(dispositivo)
        info = self.sd.query_devices(self.dispositivo)
        print(f"Micrófono: {info['name']} (dispositivo {self.dispositivo})")
        self.frecuencia = int(info["default_samplerate"])
        print(f"Frecuencia de captura: {self.frecuencia} Hz (se convierte a {SR} Hz para Whisper)")
        print("Cargando modelo de voz (la primera vez descarga unos cientos de MB)...")
        self.modelo = WhisperModel(config.WHISPER_MODELO, device="auto", compute_type="int8")
        self.ruido_ambiente = 0.00025
        self.umbral = 0.00045
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
        self.ruido_ambiente = ruido
        self.umbral = max(0.00045, ruido * 1.8)
        print(
            f"Nivel ambiente: {ruido:.5f}; umbral de voz: {self.umbral:.5f} "
            "(sensibilidad aumentada)"
        )

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
                if not hablando:
                    self.ruido_ambiente = (
                        self.ruido_ambiente * 0.95 + nivel * 0.05
                    )
                    self.umbral = max(0.00045, self.ruido_ambiente * 1.8)
                if nivel >= self.umbral:
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
        return _ajustar_nivel_audio(audio)

    def transcribir(self, audio):
        if audio is None:
            return ""
        audio = _ajustar_nivel_audio(audio)
        segmentos, _ = self.modelo.transcribe(
            audio,
            language=config.IDIOMA,
            beam_size=3,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.35,
                "min_speech_duration_ms": 100,
                "min_silence_duration_ms": 350,
                "speech_pad_ms": 300,
            },
        )
        return " ".join(s.text.strip() for s in segmentos).strip()

    def reproducir_interrumpible(self, audio, samplerate, dispositivo_salida):
        """Reproduce la respuesta y la corta si detecta voz del usuario."""
        np, sd = self.np, self.sd
        bloque = max(1, int(self.frecuencia * 0.05))
        umbral_interrupcion = max(0.0008, min(self.umbral * 1.25, 0.003))
        umbral_fin_frase = max(0.0004, self.umbral * 0.7)
        previos = []
        frase = []
        actividad = 0
        silencio = 0
        hablando = False
        bloques_frase = 0
        sd.play(
            audio,
            samplerate=samplerate,
            device=dispositivo_salida,
            blocking=False,
        )
        try:
            with sd.InputStream(
                samplerate=self.frecuencia,
                channels=1,
                dtype="float32",
                blocksize=bloque,
                device=self.dispositivo,
            ) as stream:
                while True:
                    salida = sd.get_stream()
                    reproduciendo = salida is not None and salida.active
                    if not reproduciendo and not hablando:
                        break
                    datos, _ = stream.read(bloque)
                    nivel = float(np.sqrt(np.mean(datos**2)))
                    if not hablando:
                        previos = (previos + [datos.copy()])[-6:]
                        if nivel >= umbral_interrupcion:
                            actividad += 1
                            if actividad >= 3:
                                hablando = True
                                frase = list(previos)
                                sd.stop()
                        else:
                            actividad = 0
                    else:
                        frase.append(datos.copy())
                        bloques_frase += 1
                        silencio = silencio + 1 if nivel < umbral_fin_frase else 0
                        if silencio >= 10 or bloques_frase >= 400:
                            break
        except Exception:
            sd.stop()
            raise

        if not hablando:
            return None
        audio_usuario = np.concatenate(frase).flatten()
        if self.frecuencia != SR:
            cantidad = round(len(audio_usuario) * SR / self.frecuencia)
            audio_usuario = np.interp(
                np.linspace(0, len(audio_usuario) - 1, cantidad),
                np.arange(len(audio_usuario)),
                audio_usuario,
            ).astype(np.float32)
        return InterrupcionVoz(self.transcribir(_ajustar_nivel_audio(audio_usuario)))

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

    def escuchar_continuo(self, al_transcribir=None):
        """Escucha una frase sin requerir palabra de activación."""
        while self.pausar_escucha.is_set():
            time.sleep(0.05)
        while not self.pausar_escucha.is_set():
            with self.bloqueo_audio:
                if self.pausar_escucha.is_set():
                    continue
                audio = self.grabar_frase(espera_max=1)
                if self.pausar_escucha.is_set():
                    continue
                texto = self.transcribir(audio)
                if texto and al_transcribir:
                    al_transcribir(texto)
            if texto:
                return texto.strip()
        return ""
