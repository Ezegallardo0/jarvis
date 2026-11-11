import json
import tempfile
import unittest
from pathlib import Path

import preferencias_usuario


class PreferenciasUsuarioTests(unittest.TestCase):
    def test_nombre_se_guarda_y_se_recupera(self):
        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "preferencias_usuario.json"

            guardado = preferencias_usuario.guardar_nombre_usuario(ruta, "  Ana  ")

            self.assertEqual(guardado, "Ana")
            self.assertEqual(preferencias_usuario.cargar_nombre_usuario(ruta), "Ana")
            self.assertEqual(
                json.loads(ruta.read_text(encoding="utf-8")),
                {"nombre_usuario": "Ana"},
            )

    def test_nombre_ausente_se_pide_en_el_siguiente_inicio(self):
        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "preferencias_usuario.json"

            self.assertEqual(preferencias_usuario.cargar_nombre_usuario(ruta), "")

    def test_rechaza_nombre_vacio_o_demasiado_largo(self):
        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "preferencias_usuario.json"

            with self.assertRaises(ValueError):
                preferencias_usuario.guardar_nombre_usuario(ruta, " ")
            with self.assertRaises(ValueError):
                preferencias_usuario.guardar_nombre_usuario(ruta, "A" * 61)


if __name__ == "__main__":
    unittest.main()
