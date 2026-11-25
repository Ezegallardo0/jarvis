"""Pruebas con mocks para la integración de Google Workspace (OAuth, Calendar, Gmail, Drive)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import google_integration
import tools


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
        self.content = content

    def json(self):
        return self._json


class GoogleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.secretos = {
            google_integration.KEYRING_CLIENT_KEY: {"client_id": "cid-123", "client_secret": ""},
            google_integration.KEYRING_TOKEN_KEY: {
                "access_token": "tok-abc",
                "refresh_token": "ref-xyz",
                "expires_at": time.time() + 3600,
                "email": "usuario@gmail.com",
                "scopes": google_integration.GOOGLE_SCOPES,
            },
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.p_datos = mock.patch.object(google_integration, "_leer_secreto", side_effect=lambda k: self.secretos.get(k))
        self.p_cfg = mock.patch("config.CARPETA_DATOS", Path(self.tmp.name))
        self.p_leer = self.p_datos
        self.p_guardar = mock.patch.object(
            google_integration, "_guardar_secreto", side_effect=lambda k, v: self.secretos.__setitem__(k, v)
        )
        self.p_elim = mock.patch.object(
            google_integration, "_eliminar_secreto", side_effect=lambda k: self.secretos.pop(k, None)
        )
        self.p_cfg.start()
        self.p_leer.start()
        self.p_guardar.start()
        self.p_elim.start()

    def tearDown(self):
        self.p_leer.stop()
        self.p_guardar.stop()
        self.p_elim.stop()
        self.p_cfg.stop()
        self.tmp.cleanup()

    def test_estado_conexion_y_desconexion(self):
        est = google_integration.estado_conexion()
        self.assertTrue(est["connected"])
        self.assertEqual(est["email"], "usuario@gmail.com")

        with mock.patch("httpx.Client.post", return_value=_FakeResponse(200)):
            est2 = google_integration.desconectar()
        self.assertFalse(est2["connected"])

    def test_calendar_listar_y_confirmacion_eliminar(self):
        with mock.patch(
            "google_integration._request",
            return_value=_FakeResponse(200, {"items": [{"id": "ev1", "summary": "Reunión", "start": {"dateTime": "2026-11-26T10:00:00Z"}, "end": {"dateTime": "2026-11-26T11:00:00Z"}}]}),
        ):
            evs = google_integration.listar_eventos(dias=3)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["titulo"], "Reunión")

        # Eliminar evento vía tools exige confirmación explícita
        with mock.patch.object(tools, "permiso_control_pc", return_value=True), \
             mock.patch.object(tools, "CONFIRMAR", return_value=False):
            res = tools.ejecutar("google_calendar_eliminar_evento", {"evento_id": "ev1"})
        self.assertIn("cancelada", res.casefold())

    def test_gmail_borrador_y_envio_requiere_confirmacion(self):
        with mock.patch("google_integration._request", return_value=_FakeResponse(200, {"id": "dr1"})):
            dr = google_integration.crear_borrador("dest@example.com", "Hola", "Cuerpo")
        self.assertEqual(dr["id"], "dr1")

        # Enviar sin confirmación debe cancelar y no enviar correo real
        with mock.patch.object(tools, "permiso_control_pc", return_value=True), \
             mock.patch.object(tools, "CONFIRMAR", return_value=False), \
             mock.patch("google_integration.enviar_correo") as mock_send:
            res = tools.ejecutar("gmail_enviar_correo", {"para": "a@b.com", "asunto": "S", "cuerpo": "C"})
        self.assertIn("cancelada", res.casefold())
        mock_send.assert_not_called()

    def test_drive_listar_y_descargar_con_ruta_segura(self):
        with tempfile.TemporaryDirectory() as td:
            destino = Path(td) / "doc.txt"
            with mock.patch(
                "google_integration._request",
                side_effect=[
                    _FakeResponse(200, {"id": "f1", "name": "nota.txt", "mimeType": "text/plain"}),
                    _FakeResponse(200, content=b"contenido drive"),
                ],
            ):
                guardado = google_integration.descargar_archivo_drive("f1", destino)
            self.assertEqual(guardado.read_text(encoding="utf-8"), "contenido drive")

    def test_error_permisos_google(self):
        with mock.patch("httpx.Client.request", return_value=_FakeResponse(403, text="Forbidden")):
            with self.assertRaises(google_integration.GoogleAuthError):
                google_integration._request("GET", "https://www.googleapis.com/drive/v3/files")


if __name__ == "__main__":
    unittest.main()
