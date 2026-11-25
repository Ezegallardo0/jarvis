"""Pruebas de herramientas, validación de argumentos, permisos, proveedores de IA y modelos."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_provider_errors
import config
import model_manager
import tools


class ToolsAndProvidersTests(unittest.TestCase):
    def test_validacion_argumentos_herramienta(self):
        res = tools.ejecutar("poner_recordatorio", {"minutos": "no_es_numero", "mensaje": "test"})
        self.assertIn("Argumentos inválidos", res)

    def test_ruta_segura_bloquea_fuera_de_carpetas_permitidas(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(config, "CARPETAS_PERMITIDAS", [Path(td)]), \
                 mock.patch.object(tools, "permiso_control_pc", return_value=False):
                with self.assertRaises(PermissionError):
                    tools._ruta_segura("C:\\Windows\\System32\\cmd.exe")

    def test_confirmacion_accion_sensible_cancela_si_rechaza(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(config, "CARPETA_DATOS", Path(td)), \
                 mock.patch.object(tools, "permiso_control_pc", return_value=True), \
                 mock.patch.object(tools, "CONFIRMAR", return_value=False):
                res = tools.ejecutar("eliminar_memoria", {"id_recuerdo": 999})
            self.assertIn("cancelada", res.casefold())

    def test_error_proveedor_ia_oculta_clave(self):
        err = RuntimeError("401 Unauthorized sk-secret-token-123456")
        msg = ai_provider_errors.formatear_error_publico("openai", err, "sk-secret-token-123456")
        self.assertNotIn("sk-secret-token-123456", msg)

    def test_model_manager_integridad_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            archivo = Path(td) / "modelo.bin"
            archivo.write_bytes(b"jarvis-model-bytes")
            digest = model_manager.calcular_sha256(archivo)
            self.assertTrue(model_manager.verificar_integridad(archivo, digest))
            self.assertFalse(model_manager.verificar_integridad(archivo, "0" * 64))


if __name__ == "__main__":
    unittest.main()
