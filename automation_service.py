r"""Servicio independiente de automatizaciones persistentes para Windows (JARVIS).

Soporta:
- Base de datos SQLite persistente (automations.db) para recordatorios, alertas,
  tareas programadas, automatizaciones recurrentes y eventos de calendario.
- Prevención de doble ejecución mediante bloqueo atómico en SQLite.
- Manejo de tareas vencidas / horarios perdidos (run_once, skip, notify_missed).
- Comunicación local segura sin puertos TCP mediante Windows Named Pipe
  (\\.\pipe\JarvisAutomationService) autenticada con token local.
- Ejecución independiente del HUD (proceso en segundo plano o Servicio Windows / Task Scheduler).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import config

PIPE_NAME = r"\\.\pipe\JarvisAutomationService"
TASK_TYPES = ("reminder", "alert", "scheduled", "recurring", "calendar_event")
TASK_STATUSES = ("pending", "paused", "running", "completed", "failed", "missed")
MISSED_POLICIES = ("run_once", "skip", "notify_missed")


def _ahora_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _ahora_iso() -> str:
    return _ahora_utc().isoformat()


def _parse_iso(valor: str | None) -> datetime.datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.datetime.fromisoformat(valor)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _notificar_windows(titulo: str, mensaje: str) -> None:
    """Muestra una notificación local en Windows sin depender del HUD."""
    try:
        if sys.platform == "win32":
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                "$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$txt = $tpl.GetElementsByTagName('text'); "
                f"$txt.Item(0).AppendChild($tpl.CreateTextNode({json.dumps(titulo)})) > $null; "
                f"$txt.Item(1).AppendChild($tpl.CreateTextNode({json.dumps(mensaje)})) > $null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('JARVIS').Show($toast)"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    except Exception:
        pass
    print(f"[JARVIS Automation] {titulo}: {mensaje}")


class AutomationStore:
    """Almacén SQLite persistente y thread-safe para automatizaciones."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else (config.CARPETA_DATOS / "automations.db")
        self._lock = threading.RLock()
        self._inicializado = False

    @contextlib.contextmanager
    def _conectar(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()

    def inicializar(self) -> None:
        with self._lock:
            if self._inicializado and self.db_path.exists():
                return
            with self._conectar() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automations (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        action_type TEXT NOT NULL DEFAULT 'notify',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        schedule_at TEXT NOT NULL,
                        interval_seconds INTEGER NOT NULL DEFAULT 0,
                        missed_policy TEXT NOT NULL DEFAULT 'run_once',
                        requires_ai INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending',
                        lock_token TEXT DEFAULT NULL,
                        last_run_at TEXT DEFAULT NULL,
                        last_error TEXT NOT NULL DEFAULT '',
                        run_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automation_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        automation_id TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT DEFAULT NULL,
                        status TEXT NOT NULL,
                        detail TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_auto_status_sched ON automations(status, schedule_at)"
                )
                conn.commit()
            self._inicializado = True

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        return {
            "id": row["id"],
            "title": row["title"],
            "task_type": row["task_type"],
            "action_type": row["action_type"],
            "payload": payload if isinstance(payload, dict) else {},
            "schedule_at": row["schedule_at"],
            "interval_seconds": row["interval_seconds"],
            "missed_policy": row["missed_policy"],
            "requires_ai": bool(row["requires_ai"]),
            "status": row["status"],
            "last_run_at": row["last_run_at"],
            "last_error": row["last_error"],
            "run_count": row["run_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def crear_tarea(
        self,
        title: str,
        schedule_at: str | datetime.datetime,
        task_type: str = "reminder",
        action_type: str = "notify",
        payload: dict[str, Any] | None = None,
        interval_seconds: int = 0,
        missed_policy: str = "run_once",
        requires_ai: bool = False,
    ) -> dict[str, Any]:
        self.inicializar()
        if task_type not in TASK_TYPES:
            raise ValueError(f"Tipo de tarea inválido: {task_type}")
        if missed_policy not in MISSED_POLICIES:
            raise ValueError(f"Política de vencimiento inválida: {missed_policy}")
        if isinstance(schedule_at, datetime.datetime):
            dt = schedule_at
            if dt.tzinfo is None:
                dt = dt.astimezone()
            sched_iso = dt.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat()
        else:
            parsed = _parse_iso(str(schedule_at))
            if not parsed:
                raise ValueError(f"Fecha/hora programada inválida: {schedule_at}")
            sched_iso = parsed.isoformat()

        tid = f"auto_{uuid.uuid4().hex[:10]}"
        ahora = _ahora_iso()
        with self._lock:
            with self._conectar() as conn:
                conn.execute(
                    """
                    INSERT INTO automations
                    (id, title, task_type, action_type, payload_json, schedule_at, interval_seconds,
                     missed_policy, requires_ai, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        tid,
                        str(title or "").strip() or "Automatización",
                        task_type,
                        action_type,
                        json.dumps(payload or {}, ensure_ascii=False),
                        sched_iso,
                        max(0, int(interval_seconds or 0)),
                        missed_policy,
                        1 if requires_ai else 0,
                        ahora,
                        ahora,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM automations WHERE id = ?", (tid,)).fetchone()
        return self._row_to_dict(row)

    def listar_tareas(self, status: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM automations WHERE status = ? ORDER BY schedule_at ASC LIMIT ?",
                        (status, int(limite)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM automations ORDER BY schedule_at ASC LIMIT ?",
                        (int(limite),),
                    ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def obtener_tarea(self, task_id: str) -> dict[str, Any] | None:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                row = conn.execute("SELECT * FROM automations WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def editar_tarea(
        self,
        task_id: str,
        title: str | None = None,
        schedule_at: str | None = None,
        interval_seconds: int | None = None,
        payload: dict[str, Any] | None = None,
        missed_policy: str | None = None,
    ) -> dict[str, Any] | None:
        self.inicializar()
        actual = self.obtener_tarea(task_id)
        if not actual:
            return None
        nuevo_titulo = actual["title"] if title is None else str(title).strip()
        nuevo_sched = actual["schedule_at"]
        if schedule_at is not None:
            parsed = _parse_iso(schedule_at)
            if not parsed:
                raise ValueError("Fecha/hora inválida.")
            nuevo_sched = parsed.isoformat()
        nuevo_intervalo = actual["interval_seconds"] if interval_seconds is None else max(0, int(interval_seconds))
        nuevo_payload = dict(actual["payload"])
        if payload is not None:
            nuevo_payload.update(payload)
        nueva_pol = actual["missed_policy"] if missed_policy is None else missed_policy
        if nueva_pol not in MISSED_POLICIES:
            raise ValueError("Política inválida.")

        with self._lock:
            with self._conectar() as conn:
                conn.execute(
                    """
                    UPDATE automations
                    SET title = ?, schedule_at = ?, interval_seconds = ?,
                        payload_json = ?, missed_policy = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        nuevo_titulo,
                        nuevo_sched,
                        nuevo_intervalo,
                        json.dumps(nuevo_payload, ensure_ascii=False),
                        nueva_pol,
                        _ahora_iso(),
                        task_id,
                    ),
                )
                conn.commit()
        return self.obtener_tarea(task_id)

    def pausar_tarea(self, task_id: str) -> dict[str, Any] | None:
        return self._cambiar_estado(task_id, "paused")

    def reanudar_tarea(self, task_id: str) -> dict[str, Any] | None:
        return self._cambiar_estado(task_id, "pending")

    def _cambiar_estado(self, task_id: str, estado: str) -> dict[str, Any] | None:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                conn.execute(
                    "UPDATE automations SET status = ?, lock_token = NULL, updated_at = ? WHERE id = ?",
                    (estado, _ahora_iso(), task_id),
                )
                conn.commit()
        return self.obtener_tarea(task_id)

    def eliminar_tarea(self, task_id: str) -> bool:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                cur = conn.execute("DELETE FROM automations WHERE id = ?", (task_id,))
                conn.commit()
                return cur.rowcount > 0

    def reclamar_pendientes(self, ahora: datetime.datetime | None = None) -> list[dict[str, Any]]:
        """Reclama atómicamente tareas listas para evitar doble ejecución."""
        self.inicializar()
        ref = ahora or _ahora_utc()
        ref_iso = ref.isoformat()
        token = uuid.uuid4().hex
        with self._lock:
            with self._conectar() as conn:
                conn.execute(
                    """
                    UPDATE automations
                    SET status = 'running', lock_token = ?, updated_at = ?
                    WHERE status = 'pending' AND lock_token IS NULL AND schedule_at <= ?
                    """,
                    (token, ref_iso, ref_iso),
                )
                conn.commit()
                rows = conn.execute(
                    "SELECT * FROM automations WHERE lock_token = ?", (token,)
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def finalizar_ejecucion(
        self,
        task: dict[str, Any],
        exito: bool,
        detalle: str = "",
        ahora: datetime.datetime | None = None,
    ) -> dict[str, Any] | None:
        self.inicializar()
        ref = ahora or _ahora_utc()
        ref_iso = ref.isoformat()
        intervalo = int(task.get("interval_seconds") or 0)

        if exito and intervalo > 0:
            siguiente = ref + datetime.timedelta(seconds=intervalo)
            nuevo_estado = "pending"
            nuevo_sched = siguiente.isoformat()
        elif exito:
            nuevo_estado = "completed"
            nuevo_sched = task["schedule_at"]
        else:
            nuevo_estado = "failed" if intervalo == 0 else "pending"
            nuevo_sched = (
                (ref + datetime.timedelta(seconds=intervalo)).isoformat()
                if intervalo > 0
                else task["schedule_at"]
            )

        with self._lock:
            with self._conectar() as conn:
                conn.execute(
                    """
                    UPDATE automations
                    SET status = ?, schedule_at = ?, lock_token = NULL,
                        last_run_at = ?, last_error = ?, run_count = run_count + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        nuevo_estado,
                        nuevo_sched,
                        ref_iso,
                        "" if exito else str(detalle)[:400],
                        ref_iso,
                        task["id"],
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO automation_runs (automation_id, started_at, finished_at, status, detail)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (task["id"], ref_iso, ref_iso, "ok" if exito else "error", str(detalle)[:400]),
                )
                conn.commit()
        return self.obtener_tarea(task["id"])

    def recuperar_bloqueadas(self) -> int:
        """Libera tareas que quedaron en 'running' tras un cierre inesperado."""
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                cur = conn.execute(
                    "UPDATE automations SET status = 'pending', lock_token = NULL, updated_at = ? "
                    "WHERE status = 'running'",
                    (_ahora_iso(),),
                )
                conn.commit()
                return cur.rowcount

    def historial_ejecuciones(self, limite: int = 30) -> list[dict[str, Any]]:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                rows = conn.execute(
                    "SELECT * FROM automation_runs ORDER BY id DESC LIMIT ?", (int(limite),)
                ).fetchall()
        return [dict(r) for r in rows]


class AutomationRunner:
    """Motor de ejecución independiente para el servicio de automatizaciones."""

    def __init__(
        self,
        store: AutomationStore | None = None,
        notificador: Callable[[str, str], Any] | None = None,
        ejecutor_ia: Callable[[str], str] | None = None,
        tolerancia_vencida_seg: int = 300,
    ):
        self.store = store or AutomationStore()
        self.notificador = notificador or _notificar_windows
        self.ejecutor_ia = ejecutor_ia
        self.tolerancia_vencida_seg = tolerancia_vencida_seg
        self._detener = threading.Event()

    def procesar_ciclo(self, ahora: datetime.datetime | None = None) -> list[dict[str, Any]]:
        ref = ahora or _ahora_utc()
        reclamadas = self.store.reclamar_pendientes(ahora=ref)
        resultados: list[dict[str, Any]] = []
        for task in reclamadas:
            sched_dt = _parse_iso(task["schedule_at"])
            vencida = sched_dt is not None and (ref - sched_dt).total_seconds() > self.tolerancia_vencida_seg
            if vencida and task["missed_policy"] == "skip":
                res = self.store.finalizar_ejecucion(task, exito=True, detalle="Omitida por horario vencido.", ahora=ref)
                resultados.append(res or task)
                continue
            if vencida and task["missed_policy"] == "notify_missed":
                self.notificador(
                    f"Tarea vencida: {task['title']}",
                    f"Programada para {task['schedule_at']}. {task['payload'].get('mensaje', '')}",
                )
                res = self.store.finalizar_ejecucion(task, exito=True, detalle="Notificada como vencida.", ahora=ref)
                resultados.append(res or task)
                continue

            # Ejecutar en worker para no bloquear si requiere IA o acción externa
            exito, detalle = self._ejecutar_tarea(task)
            res = self.store.finalizar_ejecucion(task, exito=exito, detalle=detalle, ahora=ref)
            resultados.append(res or task)
        return resultados

    def _ejecutar_tarea(self, task: dict[str, Any]) -> tuple[bool, str]:
        payload = task.get("payload") or {}
        action_type = task.get("action_type", "notify")
        try:
            if task.get("requires_ai") or action_type == "ai_prompt":
                prompt = str(payload.get("prompt") or task["title"])
                if self.ejecutor_ia is None:
                    self.notificador(
                        f"Automatización pendiente de IA: {task['title']}",
                        "El modelo de IA no está disponible en este momento.",
                    )
                    return False, "Modelo de IA no disponible; no bloquea el servicio."
                respuesta = self.ejecutor_ia(prompt)
                self.notificador(task["title"], str(respuesta)[:300])
                return True, str(respuesta)[:300]

            mensaje = str(payload.get("mensaje") or task["title"])
            self.notificador(f"JARVIS: {task['title']}", mensaje)
            return True, mensaje
        except Exception as exc:
            return False, str(exc)

    def ejecutar_bucle(self, intervalo_seg: float = 2.0) -> None:
        self.store.inicializar()
        self.store.recuperar_bloqueadas()
        while not self._detener.is_set():
            try:
                self.procesar_ciclo()
            except Exception as exc:
                print(f"[automation_service] Error en ciclo: {exc}")
            self._detener.wait(intervalo_seg)

    def detener(self) -> None:
        self._detener.set()


# ------------------------------------------------------------------ IPC Local (Named Pipe)
def _ruta_token() -> Path:
    return config.CARPETA_DATOS / "service_token"


def _obtener_o_crear_token() -> str:
    ruta = _ruta_token()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.is_file():
        tok = ruta.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_hex(24)
    ruta.write_text(tok, encoding="utf-8")
    return tok


def _manejar_comando_ipc(
    peticion: dict[str, Any],
    store: AutomationStore,
    token_esperado: str,
) -> dict[str, Any]:
    if not secrets.compare_digest(str(peticion.get("token", "")), token_esperado):
        return {"ok": False, "error": "Token IPC inválido."}
    accion = peticion.get("action")
    if accion == "ping":
        return {"ok": True, "status": "running", "pid": os.getpid()}
    if accion == "list":
        return {"ok": True, "tasks": store.listar_tareas(status=peticion.get("status"))}
    if accion == "create":
        t = store.crear_tarea(**(peticion.get("data") or {}))
        return {"ok": True, "task": t}
    if accion == "pause":
        return {"ok": True, "task": store.pausar_tarea(peticion["id"])}
    if accion == "resume":
        return {"ok": True, "task": store.reanudar_tarea(peticion["id"])}
    if accion == "delete":
        return {"ok": True, "deleted": store.eliminar_tarea(peticion["id"])}
    return {"ok": False, "error": f"Acción IPC desconocida: {accion}"}


class PipeServer:
    """Servidor IPC sobre Windows Named Pipe sin abrir puertos TCP."""

    def __init__(self, store: AutomationStore, pipe_name: str = PIPE_NAME):
        self.store = store
        self.pipe_name = pipe_name
        self.token = _obtener_o_crear_token()
        self._detener = threading.Event()

    def iniciar_en_hilo(self) -> threading.Thread:
        hilo = threading.Thread(target=self._servir, daemon=True)
        hilo.start()
        return hilo

    def _servir(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import win32file
            import win32pipe
        except ImportError:
            return

        while not self._detener.is_set():
            pipe = None
            try:
                pipe = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    4,
                    65536,
                    65536,
                    0,
                    None,
                )
                win32pipe.ConnectNamedPipe(pipe, None)
                _, raw = win32file.ReadFile(pipe, 65536)
                peticion = json.loads(raw.decode("utf-8"))
                resp = _manejar_comando_ipc(peticion, self.store, self.token)
                win32file.WriteFile(pipe, json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            except Exception:
                time.sleep(0.2)
            finally:
                if pipe is not None:
                    try:
                        win32file.CloseHandle(pipe)
                    except Exception:
                        pass

    def detener(self) -> None:
        self._detener.set()


def enviar_comando_ipc(action: str, data: dict[str, Any] | None = None, task_id: str = "") -> dict[str, Any]:
    """Envía un comando autenticado al servicio mediante Named Pipe."""
    if sys.platform != "win32":
        return {"ok": False, "error": "Named Pipes solo soportados en Windows."}
    try:
        import win32file
    except ImportError:
        return {"ok": False, "error": "pywin32 no disponible."}

    token = _obtener_o_crear_token()
    payload = {"token": token, "action": action, "data": data or {}, "id": task_id}
    try:
        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        try:
            win32file.WriteFile(handle, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            _, raw = win32file.ReadFile(handle, 65536)
            return json.loads(raw.decode("utf-8"))
        finally:
            win32file.CloseHandle(handle)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ------------------------------------------------------------------ Gestión de servicio Windows
TASK_SCHEDULER_NAME = "JarvisAutomationService"


def estado_servicio() -> dict[str, Any]:
    """Consulta si el servicio está activo vía IPC y si está registrado para inicio automático."""
    ipc = enviar_comando_ipc("ping")
    activo = bool(ipc.get("ok"))
    auto_iniciado = False
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["schtasks", "/Query", "/TN", TASK_SCHEDULER_NAME],
                capture_output=True,
                text=True,
                timeout=5,
            )
            auto_iniciado = r.returncode == 0
        except Exception:
            pass
    return {
        "running": activo,
        "pid": ipc.get("pid") if activo else None,
        "autostart_registered": auto_iniciado,
        "ipc_pipe": PIPE_NAME,
    }


def _comando_ejecucion_servicio() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--automation-service"]
    script = str(Path(__file__).resolve())
    return [sys.executable, script, "--run"]


def iniciar_servicio_segundo_plano() -> dict[str, Any]:
    actual = estado_servicio()
    if actual["running"]:
        return actual
    cmd = _comando_ejecucion_servicio()
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )
    subprocess.Popen(cmd, **kwargs)
    for _ in range(15):
        time.sleep(0.2)
        est = estado_servicio()
        if est["running"]:
            return est
    return estado_servicio()


def detener_servicio_segundo_plano() -> dict[str, Any]:
    est = estado_servicio()
    pid = est.get("pid")
    if pid and sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
    return estado_servicio()


def registrar_inicio_automatico(habilitar: bool = True) -> bool:
    """Registra o elimina la tarea programada de Windows para iniciar al iniciar sesión."""
    if sys.platform != "win32":
        return False
    if habilitar:
        cmd = " ".join(f'"{p}"' for p in _comando_ejecucion_servicio())
        r = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                TASK_SCHEDULER_NAME,
                "/SC",
                "ONLOGON",
                "/TR",
                cmd,
                "/F",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_SCHEDULER_NAME, "/F"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0


_STORE: AutomationStore | None = None
_STORE_LOCK = threading.Lock()


def obtener_store(db_path: str | Path | None = None) -> AutomationStore:
    global _STORE
    ruta = Path(db_path) if db_path else (config.CARPETA_DATOS / "automations.db")
    with _STORE_LOCK:
        if _STORE is None or _STORE.db_path != ruta:
            _STORE = AutomationStore(db_path=ruta)
            _STORE.inicializar()
        return _STORE


def ejecutar_servicio() -> None:
    store = obtener_store()
    pipe = PipeServer(store)
    pipe.iniciar_en_hilo()
    runner = AutomationRunner(store=store)
    try:
        runner.ejecutar_bucle()
    except KeyboardInterrupt:
        pass
    finally:
        runner.detener()
        pipe.detener()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JARVIS Windows Automation Service")
    parser.add_argument("--run", action="store_true", help="Ejecuta el servicio en primer plano")
    args = parser.parse_args()
    if args.run:
        ejecutar_servicio()
