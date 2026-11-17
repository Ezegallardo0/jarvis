"""Persistent native speech-to-speech sessions for JARVIS LIVE."""
import asyncio
import base64
import statistics
import threading
import time
from collections import deque

import config
import voice

LIVE_MODELS = {
    "gemini": "gemini-3.8-live",
    "openai": "gpt-live-1",
}
LIVE_LABELS = {
    "gemini": "Gemini Live",
    "openai": "OpenAI GPT-Live",
}
LIVE_VOICES = {
    "gemini": ("Kore", "Puck", "Aoede"),
    "openai": ("marin", "cedar"),
}
_AUDIO_FRAME_MS = 40
_MAX_INPUT_FRAMES = 12
_MAX_OUTPUT_FRAMES = 38


class LiveVoiceError(RuntimeError):
    """A provider or local audio device could not run a LIVE session."""


def _value(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_text(value):
    if value is None:
        return ""
    text = _value(value, "text", "")
    return text if isinstance(text, str) else ""


def _selected_voice(provider):
    setting = (
        config.LIVE_GEMINI_VOICE
        if provider == "gemini"
        else config.LIVE_OPENAI_VOICE
    )
    return setting if setting in LIVE_VOICES[provider] else LIVE_VOICES[provider][0]


def _gemini_tool_declarations(schemas):
    declarations = []
    for schema in schemas:
        function = schema.get("function", {})
        name = function.get("name")
        if not name:
            continue
        declaration = {
            "name": name,
            "description": function.get("description", ""),
            "parameters": function.get(
                "parameters",
                {"type": "object", "properties": {}},
            ),
            "behavior": "BLOCKING",
        }
        declarations.append(declaration)
    return declarations


def _relative_ms(value, origin):
    if value is None or origin is None:
        return None
    return round((value - origin) * 1000, 1)


class LiveLatencyTracker:
    """Tracks monotonic audio-turn timestamps and a rolling TTFA window."""

    def __init__(self, publish):
        self._publish = publish
        self._lock = threading.Lock()
        self._origin = None
        self._current = None
        self._samples = deque(maxlen=10)
        self._errors = 0
        self._interruptions = 0
        self._last_interruption = 0.0

    def capture_started(self, timestamp=None):
        with self._lock:
            self._origin = timestamp or time.perf_counter()
            origin = self._origin
        self._publish_snapshot("capture_started", origin)

    def speech_started(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        with self._lock:
            if (
                self._current
                and self._current.get("speech_end") is None
                and not self._current.get("interrupted")
            ):
                return
            self._current = {
                "speech_start": timestamp,
                "first_audio_sent": None,
                "speech_end": None,
                "turn_end_detected": None,
                "processing_started": None,
                "first_response_event": None,
                "first_audio_received": None,
                "playback_started": None,
                "response_finished": None,
                "interrupted": False,
            }
        self._publish_snapshot("speech_started", timestamp)

    def speech_ended(self, timestamp=None, detected_at=None):
        timestamp = timestamp or time.perf_counter()
        detected_at = detected_at or timestamp
        with self._lock:
            if self._current is None:
                self._current = {}
            self._current["speech_end"] = timestamp
            self._current["turn_end_detected"] = detected_at
            self._current["processing_started"] = detected_at
        self._publish_snapshot("turn_end_detected", detected_at)

    def audio_sent(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        if self._set_once("first_audio_sent", timestamp):
            self._publish_snapshot("first_audio_sent", timestamp)

    def response_event(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        first = False
        with self._lock:
            if self._current is not None:
                first = self._current["first_response_event"] is None
                self._current["first_response_event"] = (
                    self._current["first_response_event"] or timestamp
                )
        if first:
            self._publish_snapshot("first_response_event", timestamp)

    def audio_received(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        first = False
        with self._lock:
            if self._current is not None:
                first = self._current["first_audio_received"] is None
                self._current["first_audio_received"] = (
                    self._current["first_audio_received"] or timestamp
                )
                self._current["first_response_event"] = (
                    self._current["first_response_event"] or timestamp
                )
        if first:
            self._publish_snapshot("first_audio_received", timestamp)

    def playback_started(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        with self._lock:
            if self._current is not None:
                self._current["playback_started"] = (
                    self._current["playback_started"] or timestamp
                )
                self._current["first_response_event"] = (
                    self._current["first_response_event"] or timestamp
                )
        self._publish_snapshot("playback_started", timestamp)

    def interrupted(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        with self._lock:
            if timestamp - self._last_interruption < 1:
                return
            self._last_interruption = timestamp
            self._interruptions += 1
            if self._current is not None:
                self._current["interrupted"] = True
        self._publish_snapshot("interrupted", timestamp)

    def response_finished(self, timestamp=None):
        timestamp = timestamp or time.perf_counter()
        with self._lock:
            if self._current is None:
                return
            self._current["response_finished"] = timestamp
            sample = dict(self._current)
            if (
                sample.get("speech_end")
                and sample.get("playback_started")
                and not sample.get("interrupted")
            ):
                sample["ttfa_ms"] = round(
                    (sample["playback_started"] - sample["speech_end"]) * 1000,
                    1,
                )
                self._samples.append(sample)
            self._current = None
        self._publish_snapshot("response_finished", timestamp)

    def error(self):
        with self._lock:
            self._errors += 1
        self._publish_snapshot("error", time.perf_counter())

    def snapshot(self):
        with self._lock:
            origin = self._origin
            current = dict(self._current) if self._current is not None else None
            samples = list(self._samples)
            errors = self._errors
            interruptions = self._interruptions
        values = [sample["ttfa_ms"] for sample in samples]
        latest = current if current is not None else (samples[-1] if samples else None)
        ttfa = latest.get("ttfa_ms") if latest else None
        if (
            ttfa is None
            and latest
            and latest.get("speech_end")
            and latest.get("playback_started")
        ):
            ttfa = round(
                (latest["playback_started"] - latest["speech_end"]) * 1000,
                1,
            )
        return {
            "capture_started_ms": _relative_ms(origin, origin),
            "first_audio_sent_ms": _relative_ms(
                latest.get("first_audio_sent") if latest else None, origin
            ),
            "speech_started_ms": _relative_ms(
                latest.get("speech_start") if latest else None, origin
            ),
            "turn_end_detected_ms": _relative_ms(
                latest.get("turn_end_detected") if latest else None, origin
            ),
            "processing_started_ms": _relative_ms(
                latest.get("processing_started") if latest else None, origin
            ),
            "first_response_event_ms": _relative_ms(
                latest.get("first_response_event") if latest else None, origin
            ),
            "first_audio_received_ms": _relative_ms(
                latest.get("first_audio_received") if latest else None, origin
            ),
            "playback_started_ms": _relative_ms(
                latest.get("playback_started") if latest else None, origin
            ),
            "response_finished_ms": _relative_ms(
                latest.get("response_finished") if latest else None, origin
            ),
            "ttfa_ms": ttfa,
            "samples": len(values),
            "average_ttfa_ms": round(statistics.mean(values), 1) if values else None,
            "median_ttfa_ms": round(statistics.median(values), 1) if values else None,
            "best_ttfa_ms": min(values) if values else None,
            "worst_ttfa_ms": max(values) if values else None,
            "errors": errors,
            "error_rate_percent": round(
                errors * 100 / (len(values) + errors), 1
            ) if values or errors else 0,
            "interruptions": interruptions,
        }

    def _set_once(self, name, timestamp):
        with self._lock:
            if self._current is None or self._current[name] is not None:
                return False
            self._current[name] = timestamp
            return True

    def _publish_snapshot(self, event, timestamp):
        self._publish(event, metrics=self.snapshot(), at_ms=_relative_ms(timestamp, self._origin))


class _AudioBridge:
    """Bounded PCM queues and callback-driven audio I/O on one persistent session."""

    def __init__(self, input_rate, output_rate, on_event, tracker, muted_event):
        import numpy as np
        import sounddevice as sd

        self.np = np
        self.sd = sd
        self.loop = asyncio.get_running_loop()
        self.on_event = on_event
        self.tracker = tracker
        self.muted_event = muted_event
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.actual_input_rate = input_rate
        self.actual_output_rate = output_rate
        self.input_queue = asyncio.Queue(maxsize=_MAX_INPUT_FRAMES)
        self._output_lock = threading.Lock()
        self._output = deque()
        self._output_current = None
        self._output_offset = 0
        self._output_active = False
        self._generation_complete = False
        self._closed = False
        self._speech_active = False
        self._speech_candidate_at = None
        self._last_voice_at = None
        self._quiet_frames = 0
        self._voice_frames = 0
        self._noise = 0.001
        self._sensitivity = max(
            0,
            min(100, int(getattr(config, "MICROFONO_SENSIBILIDAD", 60))),
        )
        self._input_device = voice.Oido._elegir_microfono_con(
            sd,
            config.MICROFONO or None,
        )
        self._output_device = config.AUDIO_SALIDA
        self._input_stream = None
        self._output_stream = None

    def start(self):
        input_info = self.sd.query_devices(self._input_device, "input")
        output_info = (
            self.sd.query_devices(kind="output")
            if self._output_device is None
            else self.sd.query_devices(self._output_device, "output")
        )
        self.tracker.capture_started()
        self._open_input(
            self.input_rate,
            int(input_info["default_samplerate"]),
        )
        self._open_output(
            self.output_rate,
            int(output_info["default_samplerate"]),
        )

    def _open_input(self, requested_rate, default_rate):
        try:
            self._input_stream = self.sd.InputStream(
                samplerate=requested_rate,
                channels=1,
                dtype="int16",
                blocksize=max(1, requested_rate * _AUDIO_FRAME_MS // 1000),
                device=self._input_device,
                callback=self._input_callback,
            )
            self._input_stream.start()
            self.actual_input_rate = requested_rate
        except Exception as first_error:
            if default_rate == requested_rate:
                raise LiveVoiceError(
                    f"No pude abrir el micrófono seleccionado: {first_error}"
                ) from first_error
            try:
                if self._input_stream is not None:
                    self._input_stream.close()
                self._input_stream = self.sd.InputStream(
                    samplerate=default_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=max(1, default_rate * _AUDIO_FRAME_MS // 1000),
                    device=self._input_device,
                    callback=self._input_callback,
                )
                self._input_stream.start()
                self.actual_input_rate = default_rate
            except Exception as fallback_error:
                raise LiveVoiceError(
                    "No pude abrir el micrófono en la frecuencia requerida "
                    f"({requested_rate} Hz) ni en su frecuencia predeterminada "
                    f"({default_rate} Hz): {fallback_error}"
                ) from first_error

    def _open_output(self, requested_rate, default_rate):
        try:
            self._output_stream = self.sd.OutputStream(
                samplerate=requested_rate,
                channels=1,
                dtype="int16",
                blocksize=max(1, requested_rate * 20 // 1000),
                device=self._output_device,
                callback=self._output_callback,
            )
            self._output_stream.start()
            self.actual_output_rate = requested_rate
        except Exception as first_error:
            if default_rate == requested_rate:
                raise LiveVoiceError(
                    f"No pude abrir la salida de audio seleccionada: {first_error}"
                ) from first_error
            try:
                if self._output_stream is not None:
                    self._output_stream.close()
                self._output_stream = self.sd.OutputStream(
                    samplerate=default_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=max(1, default_rate * 20 // 1000),
                    device=self._output_device,
                    callback=self._output_callback,
                )
                self.actual_output_rate = default_rate
            except Exception as fallback_error:
                raise LiveVoiceError(
                    "No pude abrir la salida de audio en la frecuencia requerida "
                    f"({requested_rate} Hz) ni en su frecuencia predeterminada "
                    f"({default_rate} Hz): {fallback_error}"
                ) from first_error

    def _input_callback(self, indata, _frames, _timing, status):
        if self._closed or self.muted_event.is_set():
            return
        raw = bytes(indata)
        now = time.perf_counter()
        if status:
            detail = str(status)
            self.loop.call_soon_threadsafe(
                lambda: self.on_event("audio_status", detail=detail)
            )
        self._track_voice(raw, now)
        if self.actual_input_rate != self.input_rate:
            samples = self.np.frombuffer(raw, dtype="<i2").astype(self.np.float32)
            count = max(1, round(len(samples) * self.input_rate / self.actual_input_rate))
            positions = self.np.linspace(0, len(samples) - 1, count)
            raw = self.np.interp(positions, self.np.arange(len(samples)), samples)
            raw = self.np.clip(raw, -32768, 32767).astype("<i2").tobytes()
        try:
            self.loop.call_soon_threadsafe(self._enqueue_input, raw)
        except RuntimeError:
            return

    def _track_voice(self, raw, now):
        samples = self.np.frombuffer(raw, dtype="<i2").astype(self.np.float32)
        if not samples.size:
            return
        rms = float(self.np.sqrt(self.np.mean(samples * samples))) / 32768
        threshold = max(
            0.0007,
            self._noise * max(0.35, min(1.5, 1.7 - self._sensitivity / 100)),
        )
        if not self._speech_active:
            if rms < threshold:
                self._noise = self._noise * 0.98 + rms * 0.02
                self._voice_frames = 0
                self._speech_candidate_at = None
                return
            if self._speech_candidate_at is None:
                self._speech_candidate_at = now
            self._voice_frames += 1
            if self._voice_frames >= 2:
                self._speech_active = True
                self._last_voice_at = now
                start = self._speech_candidate_at
                self.loop.call_soon_threadsafe(self._speech_started, start)
            return
        if rms >= threshold * 0.7:
            self._last_voice_at = now
            self._quiet_frames = 0
            return
        self._quiet_frames += 1
        if self._quiet_frames >= 10:
            self._speech_active = False
            self._voice_frames = 0
            self._quiet_frames = 0
            end = self._last_voice_at or now
            self.loop.call_soon_threadsafe(self._speech_ended, end, now)

    def _speech_started(self, timestamp):
        self.begin_turn()
        with self._output_lock:
            speaking = self._output_active
        if speaking:
            self.clear_output()
            self.tracker.interrupted(timestamp)
            self.on_event("interrupted")
        self.tracker.speech_started(timestamp)
        self.on_event("speech_started", at_ms=_relative_ms(timestamp, self.tracker._origin))

    def _speech_ended(self, timestamp, detected_at):
        self.tracker.speech_ended(timestamp, detected_at)
        self.on_event(
            "speech_ended",
            at_ms=_relative_ms(timestamp, self.tracker._origin),
            detected_at_ms=_relative_ms(detected_at, self.tracker._origin),
        )

    def _enqueue_input(self, raw):
        if self._closed or self.muted_event.is_set():
            return
        while self.input_queue.full():
            try:
                self.input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.input_queue.put_nowait(raw)

    async def next_input(self):
        return await self.input_queue.get()

    def enqueue_output(self, raw):
        if not raw:
            return
        samples = self.np.frombuffer(raw, dtype="<i2")
        if self.actual_output_rate != self.output_rate and samples.size:
            count = max(1, round(len(samples) * self.actual_output_rate / self.output_rate))
            samples = self.np.interp(
                self.np.linspace(0, len(samples) - 1, count),
                self.np.arange(len(samples)),
                samples.astype(self.np.float32),
            ).clip(-32768, 32767).astype("<i2")
        frame_samples = max(1, self.actual_output_rate * 20 // 1000)
        with self._output_lock:
            for start in range(0, len(samples), frame_samples):
                if len(self._output) >= _MAX_OUTPUT_FRAMES:
                    break
                self._output.append(samples[start:start + frame_samples].copy())

    def begin_turn(self):
        with self._output_lock:
            self._generation_complete = False

    def response_complete(self):
        with self._output_lock:
            self._generation_complete = True
            finished = not self._output_active and not self._output and self._output_current is None
        if finished:
            self.on_event("playback_finished")

    def clear_output(self):
        with self._output_lock:
            self._output.clear()
            self._output_current = None
            self._output_offset = 0
            self._output_active = False

    def _output_callback(self, outdata, frames, _timing, status):
        outdata.fill(0)
        if status:
            detail = str(status)
            self.loop.call_soon_threadsafe(
                lambda: self.on_event("audio_status", detail=detail)
            )
        target = outdata.reshape(-1)
        written = 0
        volume = max(
            0,
            min(100, int(getattr(config, "VOLUMEN_VOZ", 100))),
        ) / 100
        with self._output_lock:
            while written < frames:
                if self._output_current is None:
                    if not self._output:
                        break
                    self._output_current = self._output.popleft()
                    self._output_offset = 0
                available = len(self._output_current) - self._output_offset
                take = min(available, frames - written)
                target[written:written + take] = self._output_current[
                    self._output_offset:self._output_offset + take
                ]
                written += take
                self._output_offset += take
                if self._output_offset >= len(self._output_current):
                    self._output_current = None
                    self._output_offset = 0
            audible = written > 0 and volume > 0
            became_active = audible and not self._output_active
            became_idle = (
                not audible
                and self._output_active
                and self._generation_complete
            )
            self._output_active = audible
        if written:
            if volume < 1:
                target[:written] = (target[:written].astype(self.np.float32) * volume).astype(
                    self.np.int16
                )
            if became_active:
                timestamp = time.perf_counter()
                self.loop.call_soon_threadsafe(self._playback_started, timestamp)
        if became_idle:
            self.loop.call_soon_threadsafe(self.on_event, "playback_finished")

    def _playback_started(self, timestamp):
        self.tracker.playback_started(timestamp)
        self.on_event("playback_started", at_ms=_relative_ms(timestamp, self.tracker._origin))

    def close(self):
        self._closed = True
        self.clear_output()
        for stream in (self._input_stream, self._output_stream):
            if stream is None:
                continue
            try:
                stream.stop()
            except Exception as error:
                self.on_event("audio_close_error", detail=str(error))
            try:
                stream.close()
            except Exception as error:
                self.on_event("audio_close_error", detail=str(error))


class _ProviderBase:
    input_rate = 16000
    output_rate = 24000

    def __init__(self, api_key, model, tools, on_event, on_tool, muted_event, history):
        self.api_key = api_key
        self.model = model
        self.tools = tools
        self.on_event = on_event
        self.on_tool = on_tool
        self.muted_event = muted_event
        self.history = history
        self.tracker = LiveLatencyTracker(
            lambda event, **payload: self.on_event(
                "metrics",
                event=event,
                **payload,
            )
        )
        self.bridge = None
        self._user_transcript = ""
        self._assistant_transcript = ""

    def _start_audio(self):
        self.bridge = _AudioBridge(
            self.input_rate,
            self.output_rate,
            self._audio_event,
            self.tracker,
            self.muted_event,
        )
        self.bridge.start()
        self.on_event("audio_ready", input_rate=self.bridge.actual_input_rate)

    def _audio_event(self, event, **payload):
        if event == "playback_finished":
            self.tracker.response_finished()
        self.on_event(event, **payload)

    def _append_text(self, speaker, text, append=True):
        if not text:
            return
        self.on_event("transcript", speaker=speaker, text=text, append=append)

    def _finish_user(self, text):
        text = text.strip()
        if text:
            self.on_event("user_final", text=text)

    def _finish_assistant(self, text):
        text = text.strip()
        if text:
            self.on_event("assistant_final", text=text)


class GeminiLiveProvider(_ProviderBase):
    """Google's native audio-to-audio Live API over one persistent WebSocket."""

    async def run(self, stop_event, resume_handle=None):
        try:
            from google import genai
        except ImportError as error:
            raise LiveVoiceError(
                "Falta google-genai. Instalá las dependencias de requirements.txt."
            ) from error

        client = genai.Client(
            api_key=self.api_key,
            http_options={"api_version": "v1beta"},
        )
        declaration = _gemini_tool_declarations(self.tools)
        live_config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": (
                "Sos Jarvis, un asistente conversacional natural. Respondé en español "
                "argentino, breve y directo, adaptando el detalle a la pregunta. "
                "No repitas la pregunta. Si te interrumpen, escuchá la nueva indicación. "
                "Para acciones en esta computadora usá únicamente las funciones "
                "registradas; nunca inventes comandos ni afirmes una acción sin resultado."
            ),
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "context_window_compression": {"sliding_window": {}},
            "session_resumption": {"handle": resume_handle} if resume_handle else {},
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": _selected_voice("gemini"),
                    },
                },
            },
        }
        if declaration:
            live_config["tools"] = [{"function_declarations": declaration}]
        try:
            async with client.aio.live.connect(
                model=self.model,
                config=live_config,
            ) as session:
                self.on_event("connected", provider="gemini", model=self.model)
                if self.history and not resume_handle:
                    history = [
                        {
                            "role": "user" if item["role"] == "user" else "model",
                            "parts": [{"text": item["content"][:1200]}],
                        }
                        for item in self.history[-16:]
                        if item.get("content")
                    ]
                    if history:
                        await session.send_client_content(
                            turns=history,
                            turn_complete=False,
                        )
                self._start_audio()
                receive_task = asyncio.create_task(self._receive(session))
                sender_task = asyncio.create_task(self._send_audio(session))
                stop_task = asyncio.create_task(asyncio.to_thread(stop_event.wait))
                done, pending = await asyncio.wait(
                    (receive_task, sender_task, stop_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    for task in (receive_task, sender_task):
                        task.cancel()
                    await asyncio.gather(receive_task, sender_task, return_exceptions=True)
                else:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        if task is not stop_task:
                            error = task.exception()
                            if error:
                                raise error
                    raise LiveVoiceError("La conexión Gemini Live terminó inesperadamente.")
                await session.close()
        except LiveVoiceError:
            raise
        except Exception as error:
            safe_error = str(error).replace(self.api_key, "[credencial oculta]")
            raise LiveVoiceError(f"Gemini Live: {safe_error}") from error
        finally:
            if self.bridge is not None:
                self.bridge.close()
            close = getattr(client, "aio", None)
            close = getattr(close, "aclose", None)
            if callable(close):
                await close()

    async def _send_audio(self, session):
        from google.genai import types

        while True:
            audio = await self.bridge.next_input()
            self.tracker.audio_sent()
            await session.send_realtime_input(
                audio=types.Blob(
                    data=audio,
                    mime_type=f"audio/pcm;rate={self.bridge.input_rate}",
                )
            )

    async def _receive(self, session):
        async for message in session.receive():
            server_content = _value(message, "server_content")
            model_turn = None
            if server_content is not None:
                interrupted = bool(_value(server_content, "interrupted", False))
                if interrupted:
                    self.bridge.clear_output()
                    self.tracker.interrupted()
                    self.on_event("interrupted")
                input_transcription = _as_text(
                    _value(server_content, "input_transcription")
                )
                if input_transcription:
                    self._user_transcript += input_transcription
                    self._append_text("user", input_transcription)
                output_transcription = _as_text(
                    _value(server_content, "output_transcription")
                )
                if output_transcription:
                    self.tracker.response_event()
                    self._assistant_transcript += output_transcription
                    self._append_text("jarvis", output_transcription)
                model_turn = _value(server_content, "model_turn")
                data = _value(message, "data")
                if not interrupted and model_turn is not None:
                    self.tracker.response_event()
                    self._queue_model_audio(model_turn)
                if bool(_value(server_content, "turn_complete", False)):
                    self._finish_user(self._user_transcript)
                    self._finish_assistant(self._assistant_transcript)
                    self._user_transcript = ""
                    self._assistant_transcript = ""
                    self.bridge.response_complete()

            data = _value(message, "data")
            if (
                data
                and model_turn is None
                and not (server_content is not None and interrupted)
            ):
                self._queue_audio(data)

            resumption = _value(message, "session_resumption_update")
            if resumption is not None:
                resumable = bool(_value(resumption, "resumable", False))
                handle = _value(resumption, "new_handle")
                if resumable and handle:
                    self.on_event("resume_handle", handle=handle)

            if _value(message, "go_away") is not None:
                self.on_event("reconnecting", reason="El proveedor renovará la conexión.")
                return

            tool_call = _value(message, "tool_call")
            if tool_call is not None:
                self.tracker.response_event()
                await self._handle_tool_call(session, tool_call)

    def _queue_model_audio(self, model_turn):
        for part in _value(model_turn, "parts", []) or []:
            inline_data = _value(part, "inline_data")
            data = _value(inline_data, "data")
            if data:
                self._queue_audio(data)

    def _queue_audio(self, data):
        if isinstance(data, str):
            data = base64.b64decode(data)
        if len(data) % 2:
            data = data[:-1]
        if data:
            self.tracker.audio_received()
            self.bridge.enqueue_output(data)

    async def _handle_tool_call(self, session, tool_call):
        from google.genai import types

        responses = []
        calls = _value(tool_call, "function_calls", []) or []
        for call in calls:
            name = _value(call, "name", "")
            arguments = _value(call, "args", {}) or {}
            result = await asyncio.to_thread(self.on_tool, name, arguments)
            responses.append(
                types.FunctionResponse(
                    id=_value(call, "id"),
                    name=name,
                    response={"result": str(result)},
                )
            )
        if responses:
            await session.send_tool_response(function_responses=responses)


class OpenAIRealtimeProvider(_ProviderBase):
    """OpenAI GPT-Live full-duplex audio with client-side tool delegation."""

    input_rate = 24000
    output_rate = 24000

    def __init__(self, api_key, model, tools, on_event, on_tool, muted_event, history):
        super().__init__(api_key, model, tools, on_event, on_tool, muted_event, history)
        self._pending_delegations = deque()
        self._user_turn_ended = False
        self._user_final_sent = False
        self._active_connection = None
        self._output_idle_handle = None
        self._loop = None

    async def run(self, stop_event):
        self._pending_delegations = deque()
        self._loop = asyncio.get_running_loop()
        try:
            import importlib.util

            if importlib.util.find_spec("websockets") is None:
                raise LiveVoiceError(
                    "Falta el extra realtime de OpenAI. Instalá "
                    "`openai[realtime]` desde requirements.txt."
                )
            from openai import AsyncOpenAI
        except LiveVoiceError:
            raise
        except ImportError as error:
            raise LiveVoiceError(
                "Falta el SDK de OpenAI Realtime. Instalá requirements.txt."
            ) from error

        client = AsyncOpenAI(api_key=self.api_key, max_retries=0)
        instructions = (
            "You are Jarvis, a natural voice assistant. Speak naturally in "
            "Argentinian Spanish. Be concise and do not repeat the user's question. "
            "When the user asks to control this computer, access its files, or "
            "request current local system information, create a client delegation. "
            "Never claim an action succeeded before the application returns a result."
        )
        session_config = {
            "model": self.model,
            "instructions": instructions,
            "audio": {
                "format": {"type": "audio/pcm", "rate": self.input_rate},
                "output": {"voice": _selected_voice("openai")},
            },
            "delegation": {"type": "client"},
        }
        if self.history:
            session_config["input"] = [
                {
                    "type": "message",
                    "role": item["role"],
                    "content": [{
                        "type": "input_text" if item["role"] == "user" else "output_text",
                        "text": item["content"][:1200],
                    }],
                }
                for item in self.history[-16:]
                if item.get("content")
            ]

        try:
            async with client.live.connect(max_retries=0) as connection:
                started = asyncio.Event()
                receive_task = asyncio.create_task(
                    self._receive(connection, started)
                )
                await connection.session.start(
                    session=session_config,
                    event_id="jarvis_live_start",
                )
                await asyncio.wait_for(started.wait(), timeout=30)
                self.on_event("connected", provider="openai", model=self.model)
                self._active_connection = connection
                self._start_audio()
                sender_task = asyncio.create_task(self._send_audio(connection))
                stop_task = asyncio.create_task(asyncio.to_thread(stop_event.wait))
                done, pending = await asyncio.wait(
                    (receive_task, sender_task, stop_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    sender_task.cancel()
                    await asyncio.gather(sender_task, return_exceptions=True)
                    await connection.session.close()
                    try:
                        await asyncio.wait_for(receive_task, timeout=5)
                    except asyncio.TimeoutError:
                        receive_task.cancel()
                        await asyncio.gather(receive_task, return_exceptions=True)
                else:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        if task is not stop_task:
                            error = task.exception()
                            if error:
                                raise error
                    raise LiveVoiceError("La sesión GPT-Live terminó inesperadamente.")
        except LiveVoiceError:
            raise
        except Exception as error:
            safe_error = str(error).replace(self.api_key, "[credencial oculta]")
            raise LiveVoiceError(f"OpenAI GPT-Live: {safe_error}") from error
        finally:
            self._cancel_output_idle()
            if self.bridge is not None:
                self.bridge.close()
            await client.close()

    async def _send_audio(self, connection):
        while True:
            audio = await self.bridge.next_input()
            self.tracker.audio_sent()
            await connection.session.input_audio.append(
                audio=base64.b64encode(audio).decode("ascii")
            )

    async def _receive(self, connection, started):
        async for event in connection:
            event_type = _value(event, "type", "")
            if event_type in (
                "session.output_transcript.delta",
                "session.output_audio.delta",
                "session.delegation.created",
            ):
                self.tracker.response_event()
            if event_type == "session.started":
                started.set()
                continue
            if "interrupt" in event_type.casefold():
                self._cancel_output_idle()
                if self.bridge is not None:
                    self.bridge.clear_output()
                self.tracker.interrupted()
                self.on_event("interrupted")
                continue
            if event_type == "session.input_transcript.delta":
                text = _value(event, "delta", "")
                self._user_transcript += text
                self._append_text("user", text)
                if self._user_turn_ended:
                    if not self._user_final_sent and self._user_transcript.strip():
                        self._finish_user(self._user_transcript)
                        self._user_final_sent = True
                    self._flush_delegations()
            elif event_type == "session.output_transcript.delta":
                text = _value(event, "delta", "")
                self._assistant_transcript += text
                self._append_text("jarvis", text)
            elif event_type == "session.output_audio.delta":
                audio = base64.b64decode(_value(event, "delta", ""))
                self.tracker.audio_received()
                if self.bridge is not None:
                    self.bridge.enqueue_output(audio)
                self._schedule_output_completion()
            elif event_type == "session.delegation.created":
                delegation = _value(event, "delegation", {})
                delegation_id = _value(delegation, "id")
                if (
                    delegation_id
                    and _value(delegation, "target", "client") == "client"
                ):
                    self._pending_delegations.append(delegation_id)
                    self._flush_delegations()
            elif event_type == "error":
                message = _value(event, "message", "El proveedor rechazó la sesión.")
                raise LiveVoiceError(f"OpenAI GPT-Live: {message}")

    def _audio_event(self, event, **payload):
        if event == "speech_started":
            self._cancel_output_idle()
            if self._user_turn_ended:
                if self._assistant_transcript:
                    self._finish_assistant(self._assistant_transcript)
                self._assistant_transcript = ""
                self._user_transcript = ""
                self._user_final_sent = False
            self._user_turn_ended = False
        elif event == "speech_ended":
            self._user_turn_ended = True
            if self._user_transcript.strip():
                self._finish_user(self._user_transcript)
                self._user_final_sent = True
            self._flush_delegations()
        super()._audio_event(event, **payload)

    def _schedule_output_completion(self):
        self._cancel_output_idle()
        self._output_idle_handle = self._loop.call_later(
            0.6,
            self._finish_output_after_idle,
        )

    def _cancel_output_idle(self):
        handle = getattr(self, "_output_idle_handle", None)
        if handle is not None:
            handle.cancel()
            self._output_idle_handle = None

    def _finish_output_after_idle(self):
        self._output_idle_handle = None
        if self._user_turn_ended and self.bridge is not None:
            self._finish_assistant(self._assistant_transcript)
            self.bridge.response_complete()
            self._user_transcript = ""
            self._assistant_transcript = ""

    def _flush_delegations(self):
        connection = self._active_connection
        text = self._user_transcript.strip()
        if not self._user_turn_ended or not connection or not text:
            return
        while self._pending_delegations:
            delegation_id = self._pending_delegations.popleft()
            task = asyncio.create_task(
                self._send_delegation_result(connection, delegation_id, text)
            )
            task.add_done_callback(self._delegation_task_finished)

    async def _send_delegation_result(self, connection, delegation_id, text):
        try:
            result = await asyncio.to_thread(
                self.on_tool,
                "__client_delegation__",
                {"text": text},
            )
        except Exception as error:
            self.on_event(
                "tool_error",
                detail=f"Falló la delegación al Core Engine: {type(error).__name__}.",
            )
            result = "El Core Engine no pudo completar la tarea solicitada."
        await connection.session.commentary.append(
            event_id=f"jarvis_tool_{time.monotonic_ns()}",
            delegation_id=delegation_id,
            content=str(result),
        )

    def _delegation_task_finished(self, task):
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            detail = str(error).replace(self.api_key, "[credencial oculta]")
            self.on_event(
                "state",
                state="error",
                detail=f"No pude devolver el resultado al modelo LIVE: {detail}",
            )


class LiveVoiceManager:
    """Selects, switches, reconnects, and reports native LIVE providers."""

    def __init__(
        self,
        credential_resolver,
        tool_executor,
        on_event,
        tool_schemas,
        preferred_provider="gemini",
    ):
        self.credential_resolver = credential_resolver
        self.tool_executor = tool_executor
        self.on_event = on_event
        self.tool_schemas = tool_schemas
        self.preferred_provider = (
            preferred_provider if preferred_provider in LIVE_MODELS else "gemini"
        )
        self._selected_provider = self.preferred_provider
        self._stop_event = threading.Event()
        self._session_stop = None
        self._selection_lock = threading.Lock()
        self._muted_event = threading.Event()
        self._resume_handle = None
        self._history = deque(maxlen=32)
        self._provider = None

    @property
    def selected_provider(self):
        with self._selection_lock:
            return self._selected_provider

    def available_providers(self):
        available = []
        for provider in LIVE_MODELS:
            configured = bool(self.credential_resolver(provider))
            available.append({
                "id": provider,
                "label": LIVE_LABELS[provider],
                "model": LIVE_MODELS[provider],
                "voices": list(LIVE_VOICES[provider]),
                "configured": configured,
            })
        return available

    def select_provider(self, provider):
        if provider not in LIVE_MODELS:
            raise ValueError(f"Proveedor LIVE no válido: {provider}")
        if not any(
            item["id"] == provider and item["configured"]
            for item in self.available_providers()
        ):
            raise LiveVoiceError(
                f"Configurá la clave de {LIVE_LABELS[provider]} en Ajustes antes de cambiar."
            )
        with self._selection_lock:
            if provider == self._selected_provider:
                return
            self._selected_provider = provider
            session_stop = self._session_stop
        if session_stop is not None:
            session_stop.set()
        self.on_event(
            "provider_selected",
            provider=provider,
            model=LIVE_MODELS[provider],
        )

    def set_muted(self, muted):
        if muted:
            self._muted_event.set()
        else:
            self._muted_event.clear()

    def stop(self):
        self._stop_event.set()
        with self._selection_lock:
            session_stop = self._session_stop
        if session_stop is not None:
            session_stop.set()

    def restart_session(self):
        with self._selection_lock:
            self._resume_handle = None
            session_stop = self._session_stop
        if session_stop is not None:
            session_stop.set()

    def run(self):
        while not self._stop_event.is_set():
            provider_name = self.selected_provider
            try:
                api_key = self.credential_resolver(provider_name)
            except Exception as error:
                self._report_error(f"No pude leer la credencial de LIVE: {error}")
                return
            if not api_key:
                self._report_error(
                    f"Falta la credencial de {LIVE_LABELS[provider_name]} en el "
                    "Administrador de credenciales de Windows."
                )
                return

            session_stop = threading.Event()
            with self._selection_lock:
                if provider_name != self._selected_provider:
                    continue
                self._session_stop = session_stop
            provider = self._create_provider(provider_name, api_key)
            self._provider = provider
            attempt = 0
            while (
                not self._stop_event.is_set()
                and not session_stop.is_set()
            ):
                if attempt:
                    self.on_event(
                        "state",
                        state="reconnecting",
                        provider=LIVE_LABELS[provider_name],
                        model=provider.model,
                    )
                try:
                    provider.history = list(self._history)
                    if provider_name == "gemini":
                        session = provider.run(session_stop, self._resume_handle)
                    else:
                        session = provider.run(session_stop)
                    asyncio.run(session)
                    if self._stop_event.is_set() or session_stop.is_set():
                        break
                    raise LiveVoiceError("La sesión LIVE se cerró sin finalizar.")
                except Exception as error:
                    if self._stop_event.is_set() or session_stop.is_set():
                        break
                    provider.tracker.error()
                    self._report_error(str(error))
                    if isinstance(error, LiveVoiceError) and str(error).startswith("Falta "):
                        return
                    attempt += 1
                    if attempt >= 4:
                        break
                    delay = min(2 ** (attempt - 1), 8)
                    self.on_event(
                        "state",
                        state="reconnecting",
                        provider=LIVE_LABELS[provider_name],
                        model=provider.model,
                        retry_in_seconds=delay,
                    )
                    if session_stop.wait(delay):
                        break
            if self._stop_event.is_set():
                break
            with self._selection_lock:
                changed = provider_name != self._selected_provider
                self._session_stop = None
            if changed:
                self._resume_handle = None
                continue
            if attempt >= 4:
                self.on_event(
                    "state",
                    state="error",
                    provider=LIVE_LABELS[provider_name],
                    model=provider.model,
                )
                return
            if not session_stop.is_set():
                self._stop_event.wait(0.2)
        self.on_event("state", state="idle")

    def _create_provider(self, provider_name, api_key):
        options = {
            "api_key": api_key,
            "model": LIVE_MODELS[provider_name],
            "tools": self.tool_schemas(),
            "on_event": self._provider_event,
            "on_tool": self.tool_executor,
            "muted_event": self._muted_event,
            "history": list(self._history),
        }
        if provider_name == "gemini":
            provider = GeminiLiveProvider(**options)
            provider._resume_handle = self._resume_handle
            return provider
        return OpenAIRealtimeProvider(**options)

    def _provider_event(self, event, **payload):
        if event == "user_final":
            text = payload.get("text", "").strip()
            if text:
                self._history.append({"role": "user", "content": text})
        elif event == "assistant_final":
            text = payload.get("text", "").strip()
            if text:
                self._history.append({"role": "assistant", "content": text})
        elif event == "resume_handle":
            self._resume_handle = payload.get("handle")
        self.on_event(event, **payload)

    def _report_error(self, message):
        self.on_event(
            "state",
            state="error",
            provider=LIVE_LABELS.get(self.selected_provider, self.selected_provider),
            model=LIVE_MODELS.get(self.selected_provider, ""),
            detail=message,
        )
