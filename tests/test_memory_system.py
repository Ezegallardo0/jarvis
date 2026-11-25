"""Pruebas para el sistema de memoria estructurada (memory_system.py)."""
import datetime
import json
import tempfile
import unittest
from pathlib import Path

import memory_system


class MemorySystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = memory_system.MemoryStore(
            db_path=self.base / "memoria.db",
            json_legacy_path=self.base / "memoria.json",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_agregar_consultar_actualizar_y_eliminar(self):
        res = self.store.agregar(
            "Desarrollar asistente en Python y SQLite",
            categoria="proyectos",
            titulo="JARVIS",
            proyecto="JARVIS",
            metadata={"estado": "activo", "tecnologias": "Python, SQLite"},
        )
        self.assertEqual(res["estado"], "creado")
        rec_id = res["recuerdo"]["id"]

        encontrados = self.store.buscar(consulta="SQLite", categoria="proyectos", proyecto="JARVIS")
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0]["id"], rec_id)

        actualizado = self.store.actualizar(rec_id, metadata={"estado": "en_progreso"})
        self.assertEqual(actualizado["metadata"]["estado"], "en_progreso")

        self.assertTrue(self.store.eliminar(rec_id))
        self.assertIsNone(self.store.obtener(rec_id))

    def test_bloquea_informacion_sensible(self):
        for secreto in (
            "Mi api key es sk-1234567890abcdef1234567890",
            "Mi token github ghp_1234567890abcdefghij1234567890",
            "Mi contraseña es SuperSecreta123",
        ):
            with self.assertRaises(ValueError):
                self.store.agregar(secreto, categoria="perfil")

    def test_detecta_duplicados_y_contradicciones(self):
        r1 = self.store.agregar("Prefiero respuestas breves y directas", categoria="preferencias", titulo="Estilo")
        self.assertEqual(r1["estado"], "creado")

        dup = self.store.agregar("Prefiero respuestas breves y directas", categoria="preferencias", titulo="Estilo")
        self.assertEqual(dup["estado"], "duplicado")

        contra = self.store.agregar("Prefiero respuestas largas y detalladas", categoria="preferencias", titulo="Estilo")
        self.assertEqual(contra["estado"], "contradiccion")

        reemplazo = self.store.agregar(
            "Prefiero respuestas largas y detalladas",
            categoria="preferencias",
            titulo="Estilo",
            reemplazar_contradiccion=True,
        )
        self.assertEqual(reemplazo["estado"], "actualizado_por_contradiccion")

    def test_recupera_solo_relevantes_y_excluye_expirados(self):
        self.store.agregar("Me llamo Ezequiel", categoria="perfil")
        self.store.agregar("Proyecto Alpha en Rust", categoria="proyectos", proyecto="Alpha")
        self.store.agregar("Proyecto Beta en Python", categoria="proyectos", proyecto="Beta")
        pasado = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        self.store.agregar("Dato expirado", categoria="contexto_temporal", expires_at=pasado)

        prompt = self.store.formatear_para_prompt("como avanza Beta en Python")
        self.assertIn("Ezequiel", prompt)
        self.assertIn("Beta", prompt)
        self.assertNotIn("Dato expirado", prompt)

    def test_migracion_desde_memoria_json_legacy(self):
        legacy = self.base / "memoria.json"
        legacy.write_text(json.dumps(["Vive en Buenos Aires", "sk-secret12345678901234567890"]), encoding="utf-8")
        store2 = memory_system.MemoryStore(db_path=self.base / "migrada.db", json_legacy_path=legacy)
        items = store2.listar()
        self.assertEqual(len(items), 1)
        self.assertIn("Buenos Aires", items[0]["contenido"])


if __name__ == "__main__":
    unittest.main()
