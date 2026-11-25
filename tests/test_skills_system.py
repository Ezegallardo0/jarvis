"""Pruebas para el sistema modular de habilidades (skills_manager.py)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
import skills_manager
import tools


class SkillsSystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.p_datos = mock.patch.object(config, "CARPETA_DATOS", self.base)
        self.p_datos.start()
        self.skills_dir = self.base / "skills"
        (self.skills_dir / "custom_skills").mkdir(parents=True)

        # Crear una habilidad de prueba válida
        demo_dir = self.skills_dir / "demo_skill"
        demo_dir.mkdir()
        (demo_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "demo_skill",
                    "name": "Demo Skill",
                    "version": "1.0.0",
                    "description": "Habilidad de prueba",
                    "author": "Test",
                    "enabled_by_default": True,
                    "permissions": ["filesystem_read"],
                    "tools": ["tool_demo_test"],
                    "default_config": {"allowed_paths": [str(self.base)]},
                }
            ),
            encoding="utf-8",
        )
        (demo_dir / "skill.py").write_text(
            "def register(ctx):\n"
            "    def tool_demo_test(ruta):\n"
            "        segura = ctx.verificar_ruta_permitida(ruta)\n"
            "        return f'OK:{segura.name}'\n"
            "    ctx.registrar(tool_demo_test, 'Tool demo', {'ruta': {'type': 'string'}}, ['ruta'], permisos=['filesystem_read'])\n",
            encoding="utf-8",
        )

        self.mgr = skills_manager.SkillsManager(
            skills_dir=self.skills_dir,
            state_file=self.base / "state.json",
            log_file=self.base / "logs.jsonl",
        )
        self.old_mgr = skills_manager._MANAGER
        skills_manager._MANAGER = self.mgr
        self.mgr.descubrir_y_cargar(registrar_herramienta=tools.herramienta)

    def tearDown(self):
        skills_manager._MANAGER = self.old_mgr
        tools.HERRAMIENTAS.pop("tool_demo_test", None)
        self.p_datos.stop()
        self.tmp.cleanup()

    def test_validacion_manifest(self):
        errores = skills_manager.validar_manifest({"id": "BAD ID!", "permissions": ["root_all"]})
        self.assertTrue(len(errores) >= 3)

    def test_habilidad_desactivada_no_puede_ejecutar_ni_exponer_esquema(self):
        archivo = self.base / "nota.txt"
        archivo.write_text("hola", encoding="utf-8")

        res_ok = tools.ejecutar("tool_demo_test", {"ruta": str(archivo)})
        self.assertEqual(res_ok, "OK:nota.txt")

        self.mgr.establecer_estado("demo_skill", False)
        esquemas = {s["function"]["name"] for s in tools.esquemas()}
        self.assertNotIn("tool_demo_test", esquemas)

        res_block = tools.ejecutar("tool_demo_test", {"ruta": str(archivo)})
        self.assertIn("desactivada", res_block.casefold())

    def test_habilidad_no_puede_acceder_fuera_de_rutas_permitidas(self):
        res = tools.ejecutar("tool_demo_test", {"ruta": "C:\\Windows\\System32\\drivers\\etc\\hosts"})
        self.assertIn("no tiene permiso", res.casefold())

    def test_custom_skill_arranca_desactivada_y_revision_segura_no_ejecuta(self):
        custom = self.skills_dir / "custom_skills" / "externa"
        custom.mkdir()
        (custom / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "externa_custom",
                    "name": "Externa",
                    "version": "0.1.0",
                    "description": "Custom",
                    "author": "Tercero",
                    "enabled_by_default": True,
                    "permissions": ["network"],
                    "tools": [],
                }
            ),
            encoding="utf-8",
        )
        info = self.mgr.revisar_instalacion_segura(custom)
        self.assertTrue(info["requiere_confirmacion"])
        self.mgr.descubrir_y_cargar(registrar_herramienta=tools.herramienta)
        self.assertFalse(self.mgr.esta_activa("externa_custom"))


if __name__ == "__main__":
    unittest.main()
