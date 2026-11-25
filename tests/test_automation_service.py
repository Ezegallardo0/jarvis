"""Pruebas para el servicio persistente de automatizaciones e IPC (automation_service.py)."""
import datetime
import tempfile
import unittest
from pathlib import Path

import automation_service


class AutomationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = automation_service.AutomationStore(db_path=Path(self.tmp.name) / "auto.db")
        self.ahora = datetime.datetime(2026, 11, 25, 19, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistencia_y_prevencion_doble_ejecucion(self):
        t = self.store.crear_tarea(
            "Llamar a cliente",
            self.ahora - datetime.timedelta(seconds=5),
            task_type="reminder",
            payload={"mensaje": "Llamar"},
        )
        notifs = []
        runner = automation_service.AutomationRunner(
            store=self.store,
            notificador=lambda tit, msg: notifs.append((tit, msg)),
        )
        r1 = runner.procesar_ciclo(ahora=self.ahora)
        r2 = runner.procesar_ciclo(ahora=self.ahora)
        self.assertEqual(len(r1), 1)
        self.assertEqual(r1[0]["status"], "completed")
        self.assertEqual(len(r2), 0)
        self.assertEqual(len(notifs), 1)

    def test_manejo_tareas_vencidas_skip_y_pausa(self):
        t_skip = self.store.crear_tarea(
            "Vencida",
            self.ahora - datetime.timedelta(hours=2),
            missed_policy="skip",
        )
        t_pause = self.store.crear_tarea("Pausada", self.ahora - datetime.timedelta(seconds=10))
        self.store.pausar_tarea(t_pause["id"])

        notifs = []
        runner = automation_service.AutomationRunner(
            store=self.store,
            notificador=lambda tit, msg: notifs.append((tit, msg)),
        )
        runner.procesar_ciclo(ahora=self.ahora)
        self.assertEqual(len(notifs), 0)
        self.assertEqual(self.store.obtener_tarea(t_skip["id"])["status"], "completed")
        self.assertEqual(self.store.obtener_tarea(t_pause["id"])["status"], "paused")

        self.store.reanudar_tarea(t_pause["id"])
        runner.procesar_ciclo(ahora=self.ahora)
        self.assertEqual(len(notifs), 1)

    def test_tarea_ai_sin_modelo_no_bloquea_servicio(self):
        t_ai = self.store.crear_tarea(
            "Resumen IA",
            self.ahora,
            action_type="ai_prompt",
            requires_ai=True,
            payload={"prompt": "Resumí mi día"},
        )
        t_ok = self.store.crear_tarea("Alerta normal", self.ahora)
        notifs = []
        runner = automation_service.AutomationRunner(
            store=self.store,
            notificador=lambda tit, msg: notifs.append((tit, msg)),
            ejecutor_ia=None,
        )
        runner.procesar_ciclo(ahora=self.ahora)
        self.assertEqual(self.store.obtener_tarea(t_ai["id"])["status"], "failed")
        self.assertEqual(self.store.obtener_tarea(t_ok["id"])["status"], "completed")

    def test_ipc_autenticado_con_token(self):
        bad = automation_service._manejar_comando_ipc({"token": "wrong", "action": "ping"}, self.store, "secret")
        self.assertFalse(bad["ok"])

        good = automation_service._manejar_comando_ipc({"token": "secret", "action": "ping"}, self.store, "secret")
        self.assertTrue(good["ok"])
        self.assertEqual(good["status"], "running")


if __name__ == "__main__":
    unittest.main()
