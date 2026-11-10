"""Pruebas de permisos, voz de respaldo y recepción de adjuntos."""
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import ai_providers
import config
import brain
import tools
import ui
import voice


class PermisosYVozTests(unittest.TestCase):
    def test_saludos_se_tratan_como_conversacion_normal(self):
        for texto in (
            "Hola",
            "Hola Jarvis",
            "Hola, ¿cómo estás?",
            "Buen día",
            "Buenas noches, ¿todo bien?",
        ):
            with self.subTest(texto=texto):
                self.assertTrue(brain._es_saludo_sencillo(texto))
        self.assertFalse(brain._es_saludo_sencillo("Hola, abrí YouTube"))

    def test_saludo_cloud_no_envia_herramientas_al_modelo(self):
        cerebro = object.__new__(brain.Cerebro)
        cerebro.historial = []
        cerebro.proveedor = "openai"
        cerebro.modelo = "gpt-test"
        cerebro.api_key = "test-key"
        mensaje = mock.Mock()
        mensaje.content = "¡Hola! ¿Cómo va?"
        mensaje.tool_calls = None
        mensaje.model_dump.return_value = {
            "role": "assistant",
            "content": "¡Hola! ¿Cómo va?",
        }
        respuesta = mock.Mock()
        respuesta.choices = [mock.Mock(message=mensaje)]
        cerebro.cliente = mock.Mock()
        cerebro.cliente.chat.completions.create.return_value = respuesta

        with mock.patch.object(tools, "detencion_solicitada", return_value=False):
            resultado = cerebro.responder("Hola")

        self.assertEqual(resultado, "¡Hola! ¿Cómo va?")
        opciones = cerebro.cliente.chat.completions.create.call_args.kwargs
        self.assertNotIn("tools", opciones)
        self.assertNotIn("tool_choice", opciones)
        self.assertEqual(cerebro.historial[0]["content"], "Hola")

    def test_saludo_ollama_se_envia_sin_herramientas(self):
        cerebro = object.__new__(brain.Cerebro)
        cerebro.historial = []
        cerebro.proveedor = "ollama"
        cerebro.cliente = mock.Mock()
        cerebro.cliente.chat.return_value.message.content = "¡Hola! ¿Cómo va?"
        cerebro.cliente.chat.return_value.message.tool_calls = None

        with mock.patch.object(tools, "detencion_solicitada", return_value=False):
            resultado = cerebro.responder("Hola")

        self.assertEqual(resultado, "¡Hola! ¿Cómo va?")
        self.assertEqual(cerebro.cliente.chat.call_args.kwargs["tools"], [])

    def test_pedido_explicito_de_apertura_no_depende_del_modelo(self):
        cerebro = object.__new__(brain.Cerebro)
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(
                tools, "ejecutar", return_value="Abriendo chrome."
            ) as ejecutar,
        ):
            resultado = cerebro.responder("Jarvis, por favor abrime Google Chrome")

        self.assertEqual(resultado, "Abriendo chrome.")
        ejecutar.assert_called_once_with(
            "abrir_aplicacion", {"nombre": "chrome"}
        )
        self.assertEqual(
            cerebro.historial,
            [
                {"role": "user", "content": "Jarvis, por favor abrime Google Chrome"},
                {"role": "assistant", "content": "Abriendo chrome."},
            ],
        )

    def test_accion_directa_deja_contexto_para_el_siguiente_pedido(self):
        cerebro = object.__new__(brain.Cerebro)
        cerebro.proveedor = "openai"
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(tools, "ejecutar", return_value="Abriendo Chrome."),
        ):
            cerebro.responder("Abrí Chrome")

        cerebro._responder_api = mock.Mock(
            side_effect=lambda texto: "\n".join(
                entrada["content"] for entrada in cerebro.historial
            )
        )
        with mock.patch.object(tools, "detencion_solicitada", return_value=False):
            contexto = cerebro.responder("Buscá el video que te mencioné")

        self.assertIn("Abrí Chrome", contexto)
        self.assertIn("Abriendo Chrome.", contexto)
        cerebro._responder_api.assert_called_once_with(
            "Buscá el video que te mencioné"
        )

    def test_youtube_se_abre_como_sitio_web(self):
        cerebro = object.__new__(brain.Cerebro)
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(tools, "resolver_aplicacion", return_value=None),
            mock.patch.object(tools, "ejecutar", return_value="Abrí YouTube.") as ejecutar,
        ):
            respuesta = cerebro.responder("Abrí YouTube")

        self.assertEqual(respuesta, "Abrí YouTube.")
        ejecutar.assert_called_once_with(
            "abrir_web", {"url": "https://www.youtube.com/"}
        )

    def test_detecta_ordenes_habladas_con_acentos_y_aliases(self):
        casos = {
            "\u00bfAbr\u00ed Chrome?": "chrome",
            "abrir la aplicaci\u00f3n de VS Code": "vscode",
            "Pod\u00e9s abrir la calculadora": "calculadora",
            "Quiero que me abras Microsoft Edge": "edge",
            "\u00bfMe abr\u00eds Google Chrome, por favor?": "chrome",
            "me podes abrirme Excel": "excel",
            "Abrime WhatsApp": "whatsapp",
            "me gusta Chrome": None,
        }
        for pedido, esperado in casos.items():
            with self.subTest(pedido=pedido):
                self.assertEqual(brain._aplicacion_solicitada(pedido), esperado)

    def test_pedido_de_app_desconocida_no_se_delega_al_modelo(self):
        cerebro = object.__new__(brain.Cerebro)
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(tools, "ejecutar") as ejecutar,
        ):
            resultado = cerebro.responder("Abrime una aplicación desconocida")

        self.assertIn("No encontré", resultado)
        ejecutar.assert_not_called()

    def test_pedido_compuesto_de_apertura_y_tarea_se_delega_al_modelo(self):
        cerebro = object.__new__(brain.Cerebro)
        cerebro.proveedor = "openai"
        cerebro._responder_api = mock.Mock(return_value="Listo.")
        pedido = "Abrí Chrome y buscá información sobre cómo organizar archivos"
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(tools, "resolver_aplicacion", return_value="chrome"),
        ):
            resultado = cerebro.responder(pedido)

        self.assertEqual(resultado, "Listo.")
        cerebro._responder_api.assert_called_once_with(pedido)

    def test_pedido_compuesto_en_ensemble_usa_proveedor_con_herramientas(self):
        cerebro = object.__new__(brain.CerebroEnsemble)
        cerebro._cerebro_principal = mock.Mock()
        cerebro._cerebro_principal.responder.return_value = "Listo."
        pedido = "Abrí Chrome y buscá información sobre cómo organizar archivos"
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(tools, "resolver_aplicacion", return_value="chrome"),
        ):
            resultado = cerebro.responder(pedido)

        self.assertEqual(resultado, "Listo.")
        cerebro._cerebro_principal.responder.assert_called_once_with(pedido)

    def test_accion_en_modo_ensemble_rapido_se_ejecuta_con_herramientas(self):
        cerebro = object.__new__(brain.CerebroEnsemble)
        cerebro._cerebro_principal = mock.Mock()
        cerebro._cerebro_principal.responder.return_value = "Buscando."
        cerebro._clientes_extra = {"groq": object()}
        pedido = "Buscá ese video en YouTube"
        with (
            mock.patch.object(config, "ESTRATEGIA_ENSEMBLE", "rapido", create=True),
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
        ):
            respuesta = cerebro.responder(pedido)

        self.assertEqual(respuesta, "Buscando.")
        cerebro._cerebro_principal.responder.assert_called_once_with(pedido)

    def test_pedido_de_video_aleatorio_en_youtube_no_depende_del_modelo(self):
        cerebro = object.__new__(brain.Cerebro)
        pedido = "Quiero que abra Chrome y ponga un video aleatorio en YouTube"
        with (
            mock.patch.object(tools, "detencion_solicitada", return_value=False),
            mock.patch.object(tools, "ejecutar", return_value="Video abierto.") as ejecutar,
        ):
            resultado = cerebro.responder(pedido)

        self.assertEqual(resultado, "Video abierto.")
        ejecutar.assert_called_once_with(
            "reproducir_video_youtube",
            {"consulta": "videos populares", "navegador": "chrome"},
        )

    def test_reproduce_un_video_aleatorio_de_youtube(self):
        resultados = [
            {"title": "Resultado externo", "url": "https://example.com/watch?v=abc"},
            {"title": "Video de prueba", "url": "https://www.youtube.com/watch?v=abc123"},
        ]
        with (
            mock.patch("ddgs.DDGS") as ddgs,
            mock.patch.object(tools, "webbrowser") as navegador,
        ):
            ddgs.return_value.videos.return_value = resultados
            navegador.open.return_value = True
            respuesta = tools.reproducir_video_youtube()

        self.assertIn("video de YouTube", respuesta)
        ddgs.return_value.videos.assert_called_once_with(
            "videos populares", region="wt-wt", max_results=20
        )
        navegador.open.assert_called_once_with("https://www.youtube.com/watch?v=abc123")

    def test_reproduce_en_chrome_si_el_usuario_lo_pidio(self):
        resultados = [
            {"title": "Video de prueba", "url": "https://youtu.be/abc123"},
        ]
        with (
            mock.patch("ddgs.DDGS") as ddgs,
            mock.patch.object(tools, "_abrir_url_en_chrome") as abrir_chrome,
        ):
            ddgs.return_value.videos.return_value = resultados
            respuesta = tools.reproducir_video_youtube(navegador="chrome")

        self.assertIn("video de YouTube", respuesta)
        abrir_chrome.assert_called_once_with("https://youtu.be/abc123")

    def test_chrome_abre_el_video_directamente_sin_shell(self):
        url = "https://www.youtube.com/watch?v=abc123"
        with (
            mock.patch.object(tools.os, "name", "nt"),
            mock.patch.dict(
                "os.environ",
                {"ProgramFiles": r"C:\Apps", "LOCALAPPDATA": ""},
                clear=True,
            ),
            mock.patch.object(Path, "is_file", lambda ruta: ruta.name == "chrome.exe"),
            mock.patch.object(tools.subprocess, "Popen") as ejecutar,
        ):
            tools._abrir_url_en_chrome(url)

        ejecutar.assert_called_once_with(
            [r"C:\Apps\Google\Chrome\Application\chrome.exe", url],
            shell=False,
        )

    def test_no_finge_abrir_aplicacion_que_no_esta_instalada(self):
        with (
            mock.patch.dict("os.environ", {"SystemRoot": r"C:\Windows"}, clear=True),
            mock.patch.object(tools.shutil, "which", return_value=None),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(tools.os, "startfile", create=True) as abrir,
        ):
            with self.assertRaises(FileNotFoundError):
                tools.abrir_aplicacion("firefox")

        abrir.assert_not_called()

    def test_acciones_sensibles_requieren_permiso_de_control_global(self):
        for nombre in (
            "abrir_aplicacion",
            "reproducir_video_youtube",
            "abrir_gmail",
            "abrir_configuracion_aplicaciones",
            "crear_archivo",
            "crear_carpeta",
            "crear_documento_word",
            "leer_archivo",
        ):
            with self.subTest(nombre=nombre):
                self.assertTrue(tools.HERRAMIENTAS[nombre]["confirmar"])

    def test_permiso_persistido_ejecuta_sin_confirmacion_individual(self):
        tools.reiniciar_detencion()
        funcion = mock.Mock(return_value="Abriendo chrome.")
        herramienta = dict(tools.HERRAMIENTAS["abrir_aplicacion"])
        herramienta["fn"] = funcion
        with (
            mock.patch.dict(
                tools.HERRAMIENTAS, {"abrir_aplicacion": herramienta}
            ),
            mock.patch.object(tools, "registrar_accion"),
            mock.patch.object(tools, "permiso_control_pc", return_value=True),
            mock.patch.object(tools, "CONFIRMAR") as confirmar,
        ):
            resultado = tools.ejecutar("abrir_aplicacion", {"nombre": "chrome"})

        self.assertEqual(resultado, "Abriendo chrome.")
        funcion.assert_called_once_with(nombre="chrome")
        confirmar.assert_not_called()

    def test_sin_permiso_global_no_ejecuta_ni_pregunta_por_accion(self):
        herramienta = dict(tools.HERRAMIENTAS["abrir_aplicacion"])
        herramienta["fn"] = mock.Mock()
        with (
            mock.patch.dict(
                tools.HERRAMIENTAS, {"abrir_aplicacion": herramienta}
            ),
            mock.patch.object(tools, "registrar_accion"),
            mock.patch.object(tools, "permiso_control_pc", return_value=False),
            mock.patch.object(tools, "CONFIRMAR") as confirmar,
        ):
            resultado = tools.ejecutar("abrir_aplicacion", {"nombre": "chrome"})

        self.assertIn("autorices el control", resultado)
        herramienta["fn"].assert_not_called()
        confirmar.assert_not_called()

    def test_permiso_control_se_persiste_y_permite_rutas_fuera_de_home(self):
        with tempfile.TemporaryDirectory() as temporal:
            with (
                mock.patch.object(config, "CARPETA_DATOS", Path(temporal) / "jarvis"),
                mock.patch.object(
                    config, "CARPETAS_PERMITIDAS", [Path(temporal) / "home"]
                ),
            ):
                self.assertIsNone(tools.permiso_control_pc())
                tools.establecer_permiso_control(True)
                self.assertIs(tools.permiso_control_pc(), True)
                archivo_fuera_de_home = Path(temporal) / "otro_usuario" / "datos.txt"
                archivo_fuera_de_home.parent.mkdir()
                self.assertEqual(
                    tools._ruta_usuario(str(archivo_fuera_de_home)),
                    archivo_fuera_de_home.resolve(),
                )
                tools.establecer_permiso_control(False)
                self.assertIs(tools.permiso_control_pc(), False)
                with self.assertRaises(PermissionError):
                    tools._ruta_usuario(str(archivo_fuera_de_home))

    def test_resuelve_nombre_de_app_instalada_con_error_de_transcripcion(self):
        with mock.patch.object(
            tools,
            "_aplicaciones_instaladas",
            return_value={"whatsapp": ("WhatsApp", r"C:\Apps\WhatsApp.lnk")},
        ):
            self.assertEqual(tools.resolver_aplicacion("whatsap"), "WhatsApp")

    def test_abre_aplicacion_encontrada_en_accesos_de_windows(self):
        with (
            mock.patch.object(
                tools,
                "_aplicaciones_instaladas",
                return_value={"whatsapp": ("WhatsApp", r"C:\Apps\WhatsApp.lnk")},
            ),
            mock.patch.object(tools.os, "startfile", create=True) as abrir,
        ):
            resultado = tools.abrir_aplicacion("whatsap")

        self.assertEqual(resultado, "Abriendo WhatsApp.")
        abrir.assert_called_once_with(r"C:\Apps\WhatsApp.lnk")

    def test_piper_fallido_usa_voz_de_windows(self):
        with (
            mock.patch.object(config, "USAR_VOZ", True),
            mock.patch.object(config, "VOZ_SILENCIADA", False),
            mock.patch.object(config, "PROVEEDOR_TTS", "piper"),
            mock.patch.object(voice, "_hablar_piper", side_effect=RuntimeError("modelo ausente")),
            mock.patch.object(voice, "_hablar_windows") as hablar_windows,
            mock.patch("builtins.print"),
        ):
            resultado = voice.hablar("Prueba de audio")
        hablar_windows.assert_called_once_with("Prueba de audio")
        self.assertIn("voz local de Windows", resultado)

    def test_elevenlabs_pcm_se_reproduce_a_24000_hz(self):
        respuesta = mock.Mock(status_code=200, is_success=True, content=b"\x00\x00")
        with (
            mock.patch("httpx.post", return_value=respuesta) as enviar,
            mock.patch.object(voice, "_reproducir_audio") as reproducir,
        ):
            voice._hablar_elevenlabs(
                "Prueba",
                api_key=" \n clave-de-prueba\t ",
                voice_id="voz-de-prueba",
            )

        self.assertEqual(
            enviar.call_args.kwargs["headers"]["xi-api-key"],
            "clave-de-prueba",
        )
        self.assertIn(
            "/v1/text-to-speech/voz-de-prueba",
            enviar.call_args.args[0],
        )
        self.assertEqual(enviar.call_args.kwargs["params"], {"output_format": "pcm_24000"})
        reproducir.assert_called_once()
        self.assertEqual(reproducir.call_args.args[1], 24000)

    def test_elevenlabs_normaliza_la_clave_del_entorno(self):
        respuesta = mock.Mock(status_code=200, is_success=True, content=b"\x00\x00")
        with (
            mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": " \n clave-env\t "}),
            mock.patch("httpx.post", return_value=respuesta) as enviar,
            mock.patch.object(voice, "_reproducir_audio"),
        ):
            voice._hablar_elevenlabs(
                "Prueba", api_key=None, voice_id="voz-de-prueba"
            )

        self.assertEqual(
            enviar.call_args.kwargs["headers"]["xi-api-key"],
            "clave-env",
        )

    def test_error_elevenlabs_se_conserva_al_usar_voz_de_respaldo(self):
        with (
            mock.patch.object(config, "USAR_VOZ", True),
            mock.patch.object(config, "VOZ_SILENCIADA", False),
            mock.patch.object(config, "PROVEEDOR_TTS", "elevenlabs"),
            mock.patch.object(
                voice, "_hablar_elevenlabs",
                side_effect=RuntimeError("HTTP 401: clave rechazada"),
            ),
            mock.patch.object(voice, "_hablar_windows") as hablar_windows,
            mock.patch("builtins.print"),
        ):
            resultado = voice.hablar("Prueba de audio")
        hablar_windows.assert_called_once_with("Prueba de audio")
        self.assertIn("HTTP 401", resultado)
        self.assertIn("voz local de Windows", resultado)

    def test_vscode_prefiere_el_exe_nativo_y_usa_shell_de_windows(self):
        entorno = {
            "LOCALAPPDATA": r"C:\Apps\Local",
            "ProgramFiles": r"C:\Apps\Program Files",
            "ProgramFiles(x86)": r"C:\Apps\Program Files (x86)",
            "SystemRoot": r"C:\Apps\Windows",
        }
        with (
            mock.patch.dict("os.environ", entorno, clear=True),
            mock.patch.object(config, "APPS", {"vscode": "code"}),
            mock.patch.object(
                tools.shutil, "which",
                return_value=r"C:\Apps\Local\Programs\Microsoft VS Code\bin\code.cmd",
            ),
            mock.patch.object(Path, "is_file", lambda ruta: ruta.name == "Code.exe"),
            mock.patch.object(tools.os, "startfile", create=True) as abrir,
            mock.patch.object(tools.subprocess, "Popen") as ejecutar,
        ):
            resultado = tools.abrir_aplicacion("vscode")

        self.assertIn("Abriendo vscode", resultado)
        abrir.assert_called_once_with(
            r"C:\Apps\Local\Programs\Microsoft VS Code\Code.exe"
        )
        ejecutar.assert_not_called()

    def test_cloudflare_envia_auth_y_herramientas_y_devuelve_tool_calls(self):
        respuesta = mock.Mock(status_code=200)
        respuesta.json.return_value = {
            "success": True,
            "result": {
                "response": "",
                "tool_calls": [
                    {
                        "name": "abrir_aplicacion",
                        "arguments": {"nombre": "chrome"},
                    }
                ],
            },
        }
        mensajes = [
            {"role": "user", "content": "Abrí Chrome"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "abrir_aplicacion",
                            "arguments": '{"nombre": "chrome"}',
                        }
                    }
                ],
            },
            {"role": "tool", "content": "Abriendo chrome."},
        ]
        herramienta = tools.HERRAMIENTAS["abrir_aplicacion"]["schema"]
        cliente = ai_providers._CloudflareCompletions(
            "cuenta-prueba", "token-prueba"
        )

        with mock.patch("httpx.post", return_value=respuesta) as post:
            salida = cliente.create(
                model="@cf/test-model",
                messages=mensajes,
                tools=[herramienta],
                tool_choice="auto",
            )

        solicitud = post.call_args.kwargs
        self.assertEqual(
            solicitud["headers"]["Authorization"], "Bearer token-prueba"
        )
        self.assertEqual(solicitud["json"]["tools"], [herramienta["function"]])
        self.assertEqual(
            solicitud["json"]["messages"][1]["content"],
            '{"name": "abrir_aplicacion", "arguments": {"nombre": "chrome"}}',
        )
        self.assertEqual(solicitud["json"]["messages"][2]["role"], "tool")
        llamada = salida.choices[0].message.tool_calls[0]
        self.assertEqual(llamada.function.name, "abrir_aplicacion")
        self.assertEqual(
            json.loads(llamada.function.arguments), {"nombre": "chrome"}
        )
        self.assertEqual(salida.choices[0].finish_reason, "tool_calls")


class AdjuntosTests(unittest.TestCase):
    def test_interfaz_rechaza_ordenes_de_texto(self):
        entradas = queue.Queue()
        servidor = ui.Servidor(ui.Bus(), entradas)
        with mock.patch.object(config, "HUD_PUERTO", 18760):
            servidor.iniciar()
            try:
                url = f"http://127.0.0.1:{servidor.puerto}/say?t={servidor.token}"
                solicitud = Request(
                    url,
                    data=json.dumps({"texto": "Hola"}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-Token": servidor.token,
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(solicitud, timeout=5)

                self.assertEqual(error.exception.code, 405)
                self.assertTrue(entradas.empty())
                error.exception.close()
            finally:
                servidor.httpd.shutdown()
                servidor.httpd.server_close()

    def test_pantalla_no_se_comparte_sin_permiso_global(self):
        servidor = ui.Servidor(ui.Bus(), queue.Queue())
        with (
            mock.patch.object(config, "HUD_PUERTO", 18761),
            mock.patch.object(config, "COMPARTIENDO_PANTALLA", False),
            mock.patch.object(tools, "permiso_control_pc", return_value=False),
        ):
            servidor.iniciar()
            try:
                url = f"http://127.0.0.1:{servidor.puerto}/screen?t={servidor.token}"
                solicitud = Request(
                    url,
                    data=json.dumps({"on": True}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-Token": servidor.token,
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(solicitud, timeout=5)

                self.assertEqual(error.exception.code, 403)
                self.assertFalse(config.COMPARTIENDO_PANTALLA)
                error.exception.close()
                captura = (
                    f"http://127.0.0.1:{servidor.puerto}/screen.jpg?t={servidor.token}"
                )
                with self.assertRaises(HTTPError) as error_captura:
                    urlopen(captura, timeout=5)

                self.assertEqual(error_captura.exception.code, 403)
                error_captura.exception.close()
            finally:
                servidor.httpd.shutdown()
                servidor.httpd.server_close()

    def test_upload_de_texto_se_guarda_y_se_envia_al_chat(self):
        entradas = queue.Queue()
        servidor = ui.Servidor(ui.Bus(), entradas)
        with tempfile.TemporaryDirectory() as temporal:
            with (
                mock.patch.object(config, "CARPETA_DATOS", Path(temporal)),
                mock.patch.object(config, "HUD_PUERTO", 18765),
            ):
                servidor.iniciar()
                try:
                    url = f"http://127.0.0.1:{servidor.puerto}/upload?t={servidor.token}"
                    cuerpo = json.dumps(
                        {
                            "nombre": "notas.txt",
                            "contenido": "Texto de prueba",
                            "pregunta": "Resumilo",
                        }
                    ).encode("utf-8")
                    solicitud = Request(
                        url,
                        data=cuerpo,
                        headers={
                            "Content-Type": "application/json",
                            "X-Token": servidor.token,
                        },
                        method="POST",
                    )
                    with urlopen(solicitud, timeout=5) as respuesta:
                        datos = json.loads(respuesta.read())

                    self.assertTrue(datos["ok"])
                    pedido = entradas.get_nowait()
                    self.assertIn("Resumilo", pedido)
                    self.assertIn("Texto de prueba", pedido)
                    adjuntos = list((Path(temporal) / "adjuntos").glob("*.txt"))
                    self.assertEqual(len(adjuntos), 1)
                    self.assertEqual(adjuntos[0].read_text(encoding="utf-8"), "Texto de prueba")
                finally:
                    servidor.httpd.shutdown()
                    servidor.httpd.server_close()

    def test_upload_rechaza_archivos_no_textuales(self):
        servidor = ui.Servidor(ui.Bus(), queue.Queue())
        with mock.patch.object(config, "HUD_PUERTO", 18790):
            servidor.iniciar()
            try:
                url = f"http://127.0.0.1:{servidor.puerto}/upload?t={servidor.token}"
                cuerpo = json.dumps(
                    {"nombre": "programa.exe", "contenido": "no ejecutar", "pregunta": ""}
                ).encode("utf-8")
                solicitud = Request(
                    url,
                    data=cuerpo,
                    headers={"Content-Type": "application/json", "X-Token": servidor.token},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(solicitud, timeout=5)
                self.assertEqual(error.exception.code, 415)
                error.exception.close()
            finally:
                servidor.httpd.shutdown()
                servidor.httpd.server_close()


if __name__ == "__main__":
    unittest.main()
