"""Gestor modular de habilidades (Skills System) para JARVIS."""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

import config

PERMISOS_CONOCIDOS = (
    "network",
    "filesystem_read",
    "filesystem_write",
    "media_control",
    "system_info",
    "notifications",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")


def _ahora_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def validar_manifest(manifest: dict[str, Any]) -> list[str]:
    """Valida la estructura y campos obligatorios de un manifest.json de habilidad."""
    errores: list[str] = []
    if not isinstance(manifest, dict):
        return ["El manifiesto debe ser un objeto JSON."]
    for campo in ("id", "name", "version", "description", "author"):
        valor = manifest.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            errores.append(f"Falta el campo obligatorio '{campo}'.")
    skill_id = manifest.get("id", "")
    if isinstance(skill_id, str) and skill_id and not _ID_RE.match(skill_id):
        errores.append("El campo 'id' debe usar solo minúsculas, números, '-' o '_' (2-49 caracteres).")
    permisos = manifest.get("permissions", [])
    if not isinstance(permisos, list):
        errores.append("'permissions' debe ser una lista.")
    else:
        for perm in permisos:
            if perm not in PERMISOS_CONOCIDOS:
                errores.append(f"Permiso desconocido en manifiesto: '{perm}'.")
    deps = manifest.get("dependencies", [])
    if not isinstance(deps, list):
        errores.append("'dependencies' debe ser una lista.")
    herramientas = manifest.get("tools", [])
    if not isinstance(herramientas, list):
        errores.append("'tools' debe ser una lista.")
    return errores


class SkillsManager:
    """Administra el ciclo de vida, permisos, configuración y logs de habilidades."""

    def __init__(
        self,
        skills_dir: str | Path | None = None,
        state_file: str | Path | None = None,
        log_file: str | Path | None = None,
    ):
        base_proyecto = Path(__file__).resolve().parent
        self.skills_dir = Path(skills_dir) if skills_dir else (base_proyecto / "skills")
        self.state_file = Path(state_file) if state_file else (config.CARPETA_DATOS / "skills_state.json")
        self.log_file = Path(log_file) if log_file else (config.CARPETA_DATOS / "skills_logs.jsonl")
        self._lock = threading.RLock()
        self._skills: dict[str, dict[str, Any]] = {}
        self._estado_persistido: dict[str, dict[str, Any]] = {}
        self._cargar_estado()

    def _cargar_estado(self) -> None:
        try:
            datos = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                self._estado_persistido = datos
                return
        except Exception:
            pass
        self._estado_persistido = {}

    def _guardar_estado(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._estado_persistido, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.state_file)

    def registrar_evento(self, skill_id: str, accion: str, estado: str, detalle: str = "") -> None:
        entrada = {
            "timestamp": _ahora_iso(),
            "skill_id": skill_id,
            "accion": accion,
            "estado": estado,
            "detalle": str(detalle)[:300],
        }
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def leer_logs(self, skill_id: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        if not self.log_file.is_file():
            return []
        try:
            lineas = self.log_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        eventos = []
        for linea in reversed(lineas):
            if not linea.strip():
                continue
            try:
                ev = json.loads(linea)
            except Exception:
                continue
            if skill_id and ev.get("skill_id") != skill_id:
                continue
            eventos.append(ev)
            if len(eventos) >= limite:
                break
        return eventos

    def descubrir_y_cargar(self, registrar_herramienta: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
        """Descubre habilidades en skills/ y custom_skills/, valida manifiestos y registra herramientas."""
        with self._lock:
            self._cargar_estado()
            cargadas: list[dict[str, Any]] = []
            if not self.skills_dir.is_dir():
                return cargadas

            directorios = []
            for entry in sorted(self.skills_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith((".", "__")):
                    continue
                if entry.name == "custom_skills":
                    for custom in sorted(entry.iterdir()):
                        if custom.is_dir() and not custom.name.startswith((".", "__")):
                            directorios.append((custom, True))
                else:
                    directorios.append((entry, False))

            for carpeta, es_custom in directorios:
                manifest_path = carpeta / "manifest.json"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    self.registrar_evento(carpeta.name, "load_manifest", "error", str(exc))
                    continue

                errores = validar_manifest(manifest)
                if errores:
                    self.registrar_evento(
                        str(manifest.get("id") or carpeta.name),
                        "validate_manifest",
                        "error",
                        "; ".join(errores),
                    )
                    continue

                skill_id = manifest["id"]
                estado_guardado = self._estado_persistido.get(skill_id, {})
                # Las habilidades custom descargadas/agregadas arrancan desactivadas hasta confirmación explícita
                default_enabled = False if es_custom else bool(manifest.get("enabled_by_default", True))
                activa = bool(estado_guardado.get("enabled", default_enabled))
                cfg_default = manifest.get("default_config", {})
                if not isinstance(cfg_default, dict):
                    cfg_default = {}
                cfg_actual = {**cfg_default, **(estado_guardado.get("config") or {})}
                permisos_concedidos = estado_guardado.get("granted_permissions")
                if not isinstance(permisos_concedidos, list):
                    permisos_concedidos = list(manifest.get("permissions", []))

                info_skill = {
                    "id": skill_id,
                    "name": manifest["name"],
                    "description": manifest["description"],
                    "version": manifest["version"],
                    "author": manifest["author"],
                    "enabled": activa,
                    "custom": es_custom,
                    "permissions": list(manifest.get("permissions", [])),
                    "granted_permissions": permisos_concedidos,
                    "dependencies": list(manifest.get("dependencies", [])),
                    "tools": list(manifest.get("tools", [])),
                    "config": cfg_actual,
                    "path": str(carpeta),
                    "error": "",
                }
                self._skills[skill_id] = info_skill

                skill_py = carpeta / "skill.py"
                if skill_py.is_file() and registrar_herramienta is not None:
                    try:
                        self._cargar_modulo_skill(skill_id, skill_py, info_skill, registrar_herramienta)
                        self.registrar_evento(skill_id, "load", "ok", f"v{manifest['version']}")
                    except Exception as exc:
                        info_skill["error"] = str(exc)
                        self.registrar_evento(skill_id, "load", "error", str(exc))
                cargadas.append(info_skill)
            return cargadas

    def _cargar_modulo_skill(
        self,
        skill_id: str,
        skill_py: Path,
        info_skill: dict[str, Any],
        registrar_herramienta: Callable[..., Any],
    ) -> None:
        spec = importlib.util.spec_from_file_location(f"jarvis_skill_{skill_id}", str(skill_py))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"No se pudo cargar el módulo {skill_py}")
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        register_fn = getattr(modulo, "register", None)
        if not callable(register_fn):
            raise RuntimeError("El archivo skill.py debe definir register(contexto).")

        contexto = SkillContext(self, skill_id, registrar_herramienta)
        register_fn(contexto)

    def listar(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._skills.values()]

    def obtener(self, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._skills.get(skill_id)
            return dict(item) if item else None

    def esta_activa(self, skill_id: str) -> bool:
        with self._lock:
            item = self._skills.get(skill_id)
            return bool(item and item.get("enabled"))

    def establecer_estado(self, skill_id: str, activa: bool) -> dict[str, Any]:
        with self._lock:
            if skill_id not in self._skills:
                raise KeyError(f"Habilidad desconocida: {skill_id}")
            self._skills[skill_id]["enabled"] = bool(activa)
            registro = self._estado_persistido.setdefault(skill_id, {})
            registro["enabled"] = bool(activa)
            self._guardar_estado()
            self.registrar_evento(skill_id, "toggle", "ok", f"enabled={bool(activa)}")
            return dict(self._skills[skill_id])

    def actualizar_config(self, skill_id: str, nueva_config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(nueva_config, dict):
            raise ValueError("La configuración debe ser un objeto JSON.")
        with self._lock:
            if skill_id not in self._skills:
                raise KeyError(f"Habilidad desconocida: {skill_id}")
            self._skills[skill_id]["config"].update(nueva_config)
            registro = self._estado_persistido.setdefault(skill_id, {})
            registro["config"] = dict(self._skills[skill_id]["config"])
            self._guardar_estado()
            self.registrar_evento(skill_id, "config_update", "ok", ", ".join(nueva_config.keys()))
            return dict(self._skills[skill_id])

    def verificar_permisos(self, skill_id: str, permisos_requeridos: list[str] | None = None) -> tuple[bool, str]:
        with self._lock:
            skill = self._skills.get(skill_id)
            if not skill:
                return False, f"Habilidad «{skill_id}» no registrada."
            if not skill["enabled"]:
                return False, f"La habilidad «{skill['name']}» está desactivada."
            requeridos = permisos_requeridos if permisos_requeridos is not None else skill["permissions"]
            concedidos = set(skill.get("granted_permissions", []))
            faltantes = [p for p in requeridos if p not in concedidos]
            if faltantes:
                return False, f"Faltan permisos para «{skill['name']}»: {', '.join(faltantes)}."
            return True, ""

    def revisar_instalacion_segura(self, carpeta_origen: str | Path) -> dict[str, Any]:
        """Inspecciona una habilidad externa sin ejecutar su código y reporta riesgos/permisos."""
        origen = Path(carpeta_origen)
        manifest_path = origen / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("La carpeta de la habilidad no contiene manifest.json.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        errores = validar_manifest(manifest)
        if errores:
            raise ValueError("Manifiesto inválido: " + "; ".join(errores))
        archivos = [str(p.relative_to(origen)) for p in origen.rglob("*") if p.is_file()]
        return {
            "manifest": manifest,
            "archivos": archivos,
            "requiere_confirmacion": True,
            "mensaje": (
                f"Habilidad «{manifest['name']}» v{manifest['version']} por {manifest['author']}. "
                f"Permisos solicitados: {', '.join(manifest.get('permissions', [])) or 'ninguno'}. "
                "Revisá los archivos antes de activar."
            ),
        }


class SkillContext:
    """Contexto seguro inyectado a cada skill.py al registrarse."""

    def __init__(self, manager: SkillsManager, skill_id: str, registrar_herramienta: Callable[..., Any]):
        self._manager = manager
        self.skill_id = skill_id
        self._registrar_herramienta = registrar_herramienta

    def config(self) -> dict[str, Any]:
        skill = self._manager.obtener(self.skill_id)
        return dict(skill.get("config", {})) if skill else {}

    def verificar_ruta_permitida(self, ruta: str | Path) -> Path:
        """Restringe el acceso de la habilidad a las carpetas permitidas de su config o de JARVIS."""
        ok, motivo = self._manager.verificar_permisos(self.skill_id, ["filesystem_read"])
        if not ok:
            raise PermissionError(motivo)
        objetivo = Path(ruta).expanduser().resolve()
        permitidas_cfg = self.config().get("allowed_paths") or []
        bases = [Path(p).expanduser().resolve() for p in permitidas_cfg] if permitidas_cfg else [
            Path(p).expanduser().resolve() for p in config.CARPETAS_PERMITIDAS
        ]
        for base in bases:
            try:
                objetivo.relative_to(base)
                return objetivo
            except ValueError:
                continue
        raise PermissionError(f"La habilidad «{self.skill_id}» no tiene permiso para acceder a {objetivo}.")

    def registrar(
        self,
        fn: Callable[..., Any],
        descripcion: str,
        parametros: dict[str, Any] | None = None,
        requeridos: list[str] | None = None,
        permisos: list[str] | None = None,
        confirmar: bool = False,
        confirmar_siempre: bool = False,
        texto_confirmacion: Any = None,
    ) -> Callable[..., Any]:
        return self._registrar_herramienta(
            descripcion=descripcion,
            parametros=parametros,
            requeridos=requeridos,
            confirmar=confirmar,
            confirmar_siempre=confirmar_siempre,
            texto_confirmacion=texto_confirmacion,
            skill_id=self.skill_id,
            permisos_skill=permisos or [],
        )(fn)


_MANAGER: SkillsManager | None = None
_MANAGER_LOCK = threading.Lock()


def obtener_manager() -> SkillsManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = SkillsManager()
        return _MANAGER
