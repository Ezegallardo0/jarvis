"""Focused tests for JARVIS LIVE streaming and speech preparation."""
import unittest
import asyncio
import threading
from types import SimpleNamespace
from unittest import mock

import ai_provider_config
import brain
import live_voice
import voice


def _chunk(text=None, tool_calls=None):
    delta = SimpleNamespace(content=text, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class LiveVoiceTests(unittest.TestCase):
    def test_openai_live_flushes_late_user_transcript_after_vad_end(self):
        events = []
        provider = live_voice.OpenAIRealtimeProvider(
            "key",
            "model",
            [],
            lambda event, **payload: events.append((event, payload)),
            lambda *_args: None,
            threading.Event(),
            [],
        )
        provider._user_turn_ended = True
        provider._flush_delegations = mock.Mock()

        class Events:
            def __aiter__(self):
                async def iterate():
                    yield SimpleNamespace(
                        type="session.input_transcript.delta",
                        delta="Hola, JARVIS.",
                    )

                return iterate()

        asyncio.run(provider._receive(Events(), asyncio.Event()))

        self.assertIn(
            ("user_final", {"text": "Hola, JARVIS."}),
            events,
        )
        provider._flush_delegations.assert_called_once()

    def test_openai_live_finishes_playback_after_audio_delta_idle(self):
        events = []

        class Timer:
            def __init__(self, callback):
                self.callback = callback
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class Loop:
            def call_later(self, _delay, callback):
                timer = Timer(callback)
                timers.append(timer)
                return timer

        class Bridge:
            def response_complete(self):
                events.append(("response_complete", {}))

        timers = []
        provider = live_voice.OpenAIRealtimeProvider(
            "key",
            "model",
            [],
            lambda event, **payload: events.append((event, payload)),
            lambda *_args: None,
            threading.Event(),
            [],
        )
        provider._loop = Loop()
        provider._user_turn_ended = True
        provider._assistant_transcript = "Respuesta completa."
        provider.bridge = Bridge()

        provider._schedule_output_completion()
        timers[-1].callback()

        self.assertIn(
            ("assistant_final", {"text": "Respuesta completa."}),
            events,
        )
        self.assertIn(("response_complete", {}), events)
        self.assertEqual(provider._assistant_transcript, "")

    def test_openai_live_reports_core_engine_failure_to_user_and_model(self):
        events = []
        results = []

        class Commentary:
            async def append(self, **payload):
                results.append(payload)

        def fail_tool(*_args):
            raise RuntimeError("local failure")

        provider = live_voice.OpenAIRealtimeProvider(
            "key",
            "model",
            [],
            lambda event, **payload: events.append((event, payload)),
            fail_tool,
            threading.Event(),
            [],
        )

        asyncio.run(
            provider._send_delegation_result(
                SimpleNamespace(session=SimpleNamespace(commentary=Commentary())),
                "delegation-id",
                "Abrí Chrome.",
            )
        )

        self.assertEqual(events[0][0], "tool_error")
        self.assertEqual(
            results[0]["content"],
            "El Core Engine no pudo completar la tarea solicitada.",
        )

    def test_native_live_providers_use_provider_audio_rates(self):
        self.assertEqual(live_voice.GeminiLiveProvider.input_rate, 16000)
        self.assertEqual(live_voice.GeminiLiveProvider.output_rate, 24000)
        self.assertEqual(live_voice.OpenAIRealtimeProvider.input_rate, 24000)
        self.assertEqual(live_voice.OpenAIRealtimeProvider.output_rate, 24000)

    def test_live_latency_tracker_reports_actual_playback_ttfa(self):
        published = []
        tracker = live_voice.LiveLatencyTracker(
            lambda event, **payload: published.append((event, payload))
        )

        tracker.capture_started(10.0)
        tracker.speech_started(11.0)
        tracker.audio_sent(11.1)
        tracker.speech_ended(12.0, 12.4)
        tracker.response_event(12.5)
        tracker.audio_received(12.6)
        tracker.playback_started(12.8)
        self.assertEqual(tracker.snapshot()["ttfa_ms"], 800.0)
        tracker.response_finished(13.0)

        metrics = tracker.snapshot()
        self.assertEqual(metrics["ttfa_ms"], 800.0)
        self.assertEqual(metrics["samples"], 1)
        self.assertEqual(metrics["average_ttfa_ms"], 800.0)
        self.assertEqual(metrics["first_audio_sent_ms"], 1100.0)
        self.assertEqual(metrics["first_audio_received_ms"], 2600.0)
        self.assertTrue(published)

    def test_live_interruption_counter_deduplicates_provider_echo(self):
        tracker = live_voice.LiveLatencyTracker(lambda *_args, **_kwargs: None)

        tracker.interrupted(5.0)
        tracker.interrupted(5.2)

        self.assertEqual(tracker.snapshot()["interruptions"], 1)

    def test_interrupted_response_is_not_counted_as_completed_ttfa_sample(self):
        tracker = live_voice.LiveLatencyTracker(lambda *_args, **_kwargs: None)

        tracker.capture_started(1.0)
        tracker.speech_started(1.1)
        tracker.speech_ended(2.0, 2.4)
        tracker.playback_started(2.6)
        tracker.interrupted(2.7)
        tracker.response_finished(3.0)

        self.assertEqual(tracker.snapshot()["samples"], 0)

    def test_audio_bridge_marks_playback_complete_when_response_has_no_audio(self):
        events = []
        bridge = live_voice._AudioBridge.__new__(live_voice._AudioBridge)
        bridge._output_lock = threading.Lock()
        bridge._generation_complete = False
        bridge._output_active = False
        bridge._output = []
        bridge._output_current = None
        bridge.on_event = lambda event: events.append(event)

        bridge.response_complete()

        self.assertEqual(events, ["playback_finished"])

    def test_audio_callback_schedules_status_without_threadsafe_keyword_args(self):
        scheduled = []
        events = []

        class FakeLoop:
            def call_soon_threadsafe(self, callback, *args):
                scheduled.append((callback, args))

        bridge = live_voice._AudioBridge.__new__(live_voice._AudioBridge)
        bridge._closed = False
        bridge.muted_event = threading.Event()
        bridge.loop = FakeLoop()
        bridge.on_event = lambda event, **payload: events.append((event, payload))
        bridge._track_voice = lambda *_args: None
        bridge.actual_input_rate = bridge.input_rate = 16000
        bridge._enqueue_input = lambda _raw: None

        bridge._input_callback(bytes(640), 320, None, "input overflow")

        scheduled[0][0]()
        self.assertEqual(events, [("audio_status", {"detail": "input overflow"})])

    def test_live_manager_reports_configured_providers_without_keys(self):
        manager = live_voice.LiveVoiceManager(
            credential_resolver=lambda provider: "test-key" if provider == "gemini" else "",
            tool_schemas=lambda: [],
            tool_executor=lambda *_args: None,
            on_event=lambda *_args, **_kwargs: None,
        )

        configured = {
            item["id"]: item["configured"]
            for item in manager.available_providers()
        }
        self.assertEqual(configured, {"gemini": True, "openai": False})

    def test_text_streaming_capabilities_exclude_cloudflare_adapter(self):
        self.assertTrue(ai_provider_config.soporta_streaming_texto("openai"))
        self.assertTrue(ai_provider_config.soporta_streaming_texto("gemini"))
        self.assertFalse(ai_provider_config.soporta_streaming_texto("cloudflare"))
        self.assertFalse(ai_provider_config.soporta_streaming_texto("unknown"))

    def test_streamed_chat_emits_text_and_preserves_history(self):
        class FakeCompletions:
            def create(self, **options):
                self.options = options
                return iter((_chunk("Hola"), _chunk(", ¿cómo estás?")))

        completions = FakeCompletions()
        cerebro = brain.Cerebro.__new__(brain.Cerebro)
        cerebro.proveedor = "openai"
        cerebro.api_key = "test-key"
        cerebro.modelo = "test-model"
        cerebro.cliente = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        cerebro.proveedor_alternativo = None
        cerebro.clave_alternativa = None
        cerebro.historial = []
        cerebro.ia_estado = "sin_verificar"
        cerebro.ia_error = ""
        fragmentos = []

        with mock.patch.object(brain.tools, "detencion_solicitada", return_value=False):
            respuesta = cerebro.responder(
                "¿Cómo estás?",
                al_fragmento=lambda texto: fragmentos.append(texto) or True,
            )

        self.assertEqual(respuesta, "Hola, ¿cómo estás?")
        self.assertEqual(fragmentos, ["Hola", ", ¿cómo estás?"])
        self.assertIs(completions.options["stream"], True)
        self.assertEqual(cerebro.historial[-1]["content"], respuesta)

    def test_stream_cancellation_closes_response_and_discards_later_text(self):
        class FakeStream(list):
            closed = False

            def close(self):
                self.closed = True

        stream = FakeStream((_chunk("Primero"), _chunk("Obsoleto")))
        fragmentos = []
        texto, llamadas, cancelada = brain._consumir_stream_chat(
            stream,
            lambda parte: (fragmentos.append(parte), False)[1],
        )

        self.assertEqual(texto, "Primero")
        self.assertEqual(fragmentos, ["Primero"])
        self.assertEqual(llamadas, [])
        self.assertTrue(cancelada)
        self.assertTrue(stream.closed)

    def test_speech_segmentation_emits_sentences_and_keeps_partial_tail(self):
        fragmentos, resto = voice.separar_fragmentos(
            "Primera oración. Una segunda intervención"
        )

        self.assertEqual(fragmentos, ["Primera oración."])
        self.assertEqual(resto, "Una segunda intervención")
        final, resto = voice.separar_fragmentos(resto, final=True)
        self.assertEqual(final, ["Una segunda intervención"])
        self.assertEqual(resto, "")

    def test_microphone_sensitivity_lowers_vad_threshold_when_increased(self):
        menos_sensible = voice._calcular_umbral_voz(0.001, sensibilidad=0)
        mas_sensible = voice._calcular_umbral_voz(0.001, sensibilidad=100)

        self.assertEqual(voice._calcular_umbral_voz(0.001, sensibilidad=60), 0.0011)
        self.assertLess(mas_sensible, menos_sensible)


if __name__ == "__main__":
    unittest.main()
