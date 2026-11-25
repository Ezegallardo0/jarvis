"""Sistema de memoria inteligente y estructurada para JARVIS.

Almacena recuerdos persistentes en SQLite organizados por categorías:
- perfil: información general del usuario
- preferencias: estilo de respuestas, voz y comportamiento
- proyectos: nombre, descripción, tecnologías, estado, objetivos y tareas
- tareas: actividades, prioridades, fechas límite y estado
- conversaciones: resúmenes importantes de conversaciones anteriores
- contexto_temporal: información relevante durante una sesión o período
"""
from __future__ import annotations

import contextlib
import datetime
import difflib
import json
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any

import config

CATEGORIAS_VALIDAS = (
    "perfil",
    "preferencias",
    "proyectos",
    "tareas",
    "conversaciones",
    "contexto_temporal",
)

ESTADOS_TAREA = ("pendiente", "en_progreso", "completada", "cancelada")
PRIORIDADES_TAREA = ("baja", "media", "alta", "urgente")

_PATRONES_SENSIBLES = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "posible API key de OpenAI"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "posible API key de Google"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "posible token de GitHub"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "posible token de Slack"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE), "posible token Bearer"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "clave privada"),
    (
        re.compile(
            r"\b(?:contraseña|password|clave\s+secreta|pin\s+bancario|cvv|cbu|alias\s+bancario)\s*(?:es|:|=)\s*\S+",
            re.IGNORECASE,
        ),
        "posible contraseña o dato financiero sensible",
    ),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "posible número de tarjeta"),
)

_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "a", "en", "con", "por", "para", "sobre", "entre", "sin", "que", "qué",
    "como", "cómo", "cual", "cuál", "donde", "dónde", "cuando", "cuándo",
    "es", "son", "esta", "está", "estoy", "soy", "tengo", "tiene", "mi", "mis",
    "tu", "tus", "su", "sus", "me", "te", "se", "nos", "le", "les", "lo",
    "y", "o", "pero", "si", "no", "ya", "muy", "mas", "más", "jarvis",
    "podes", "podrias", "puedes", "quiero", "necesito", "decime", "contame",
}


def _ahora_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", str(texto or "").casefold())
    limpio = "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", limpio).strip()


def _tokens(texto: str) -> set[str]:
    palabras = re.findall(r"[a-z0-9_]{2,}", _normalizar(texto))
    return {p for p in palabras if p not in _STOPWORDS}


def detectar_informacion_sensible(texto: str) -> tuple[bool, str]:
    """Detecta secretos, API keys, contraseñas o datos financieros sensibles."""
    if not isinstance(texto, str) or not texto.strip():
        return False, ""
    for patron, motivo in _PATRONES_SENSIBLES:
        if patron.search(texto):
            return True, motivo
    return False, ""


class MemoryStore:
    """Almacén SQLite thread-safe para la memoria estructurada de JARVIS."""

    def __init__(self, db_path: str | Path | None = None, json_legacy_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else (config.CARPETA_DATOS / "memoria.db")
        self.json_legacy_path = (
            Path(json_legacy_path)
            if json_legacy_path
            else (config.CARPETA_DATOS / "memoria.json")
        )
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
                    CREATE TABLE IF NOT EXISTS recuerdos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        categoria TEXT NOT NULL,
                        titulo TEXT NOT NULL DEFAULT '',
                        contenido TEXT NOT NULL,
                        proyecto TEXT NOT NULL DEFAULT '',
                        etiquetas TEXT NOT NULL DEFAULT '[]',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        confirmado INTEGER NOT NULL DEFAULT 1,
                        expires_at TEXT DEFAULT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        access_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_recuerdos_categoria ON recuerdos(categoria)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_recuerdos_proyecto ON recuerdos(proyecto)"
                )
                conn.commit()
            self._migrar_json_legacy()
            self._inicializado = True

    def _migrar_json_legacy(self) -> None:
        if not self.json_legacy_path.is_file():
            return
        with self._conectar() as conn:
            total = conn.execute("SELECT COUNT(*) FROM recuerdos").fetchone()[0]
            if total > 0:
                return
            try:
                datos = json.loads(self.json_legacy_path.read_text(encoding="utf-8"))
            except Exception:
                return
            if not isinstance(datos, list):
                return
            ahora = _ahora_iso()
            for item in datos:
                if isinstance(item, str) and item.strip():
                    sensible, _ = detectar_informacion_sensible(item)
                    if sensible:
                        continue
                    conn.execute(
                        """
                        INSERT INTO recuerdos
                        (categoria, titulo, contenido, proyecto, etiquetas, metadata_json, confirmado, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        ("perfil", "", item.strip(), "", "[]", "{}", ahora, ahora),
                    )
            conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        try:
            etiquetas = json.loads(row["etiquetas"] or "[]")
        except Exception:
            etiquetas = []
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        return {
            "id": row["id"],
            "categoria": row["categoria"],
            "titulo": row["titulo"],
            "contenido": row["contenido"],
            "proyecto": row["proyecto"],
            "etiquetas": etiquetas if isinstance(etiquetas, list) else [],
            "metadata": metadata if isinstance(metadata, dict) else {},
            "confirmado": bool(row["confirmado"]),
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "access_count": row["access_count"],
        }

    def _validar_categoria(self, categoria: str) -> str:
        cat = str(categoria or "").strip().casefold()
        if cat not in CATEGORIAS_VALIDAS:
            raise ValueError(
                f"Categoría inválida «{categoria}». Válidas: {', '.join(CATEGORIAS_VALIDAS)}."
            )
        return cat

    def detectar_conflictos(
        self,
        categoria: str,
        contenido: str,
        titulo: str = "",
        proyecto: str = "",
    ) -> dict[str, Any]:
        """Detecta si un recuerdo es duplicado o contradice uno existente."""
        self.inicializar()
        cat = self._validar_categoria(categoria)
        norm_nuevo = _normalizar(contenido)
        norm_titulo = _normalizar(titulo)
        norm_proy = _normalizar(proyecto)
        tokens_nuevo = _tokens(contenido)

        existentes = self.listar(categoria=cat, incluir_expirados=False, limite=200)
        duplicado = None
        contradiccion = None

        for item in existentes:
            norm_exist = _normalizar(item["contenido"])
            norm_exist_titulo = _normalizar(item["titulo"])
            norm_exist_proy = _normalizar(item["proyecto"])

            if norm_nuevo == norm_exist and norm_proy == norm_exist_proy:
                duplicado = item
                break

            ratio = difflib.SequenceMatcher(None, norm_nuevo, norm_exist).ratio()
            if ratio >= 0.84 and norm_proy == norm_exist_proy:
                duplicado = item
                break

            # Contradicción probable: mismo título en la misma categoría/proyecto,
            # o mismo prefijo clave (ej. "mi X es ...") con valor distinto.
            if norm_titulo and norm_exist_titulo and norm_titulo == norm_exist_titulo and norm_proy == norm_exist_proy:
                contradiccion = item
                continue

            if cat in ("perfil", "preferencias") and tokens_nuevo:
                tokens_exist = _tokens(item["contenido"])
                comunes = tokens_nuevo & tokens_exist
                if len(comunes) >= 2 and ratio >= 0.45 and ratio < 0.84:
                    contradiccion = item

        return {
            "duplicado": duplicado,
            "contradiccion": contradiccion,
        }

    def agregar(
        self,
        contenido: str,
        categoria: str = "perfil",
        titulo: str = "",
        proyecto: str = "",
        etiquetas: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: str | None = None,
        confirmado: bool = True,
        permitir_duplicado: bool = False,
        reemplazar_contradiccion: bool = False,
    ) -> dict[str, Any]:
        """Agrega un recuerdo validando sensibilidad, duplicados y contradicciones."""
        self.inicializar()
        cat = self._validar_categoria(categoria)
        texto = str(contenido or "").strip()
        if not texto:
            raise ValueError("El contenido del recuerdo no puede estar vacío.")

        sensible, motivo = detectar_informacion_sensible(texto)
        if sensible:
            raise ValueError(
                f"Bloqueado por seguridad: parece contener {motivo}. "
                "No guardo contraseñas, tokens ni datos financieros en la memoria."
            )
        if titulo:
            sens_t, motivo_t = detectar_informacion_sensible(titulo)
            if sens_t:
                raise ValueError(f"Bloqueado por seguridad en título: {motivo_t}.")

        conflictos = self.detectar_conflictos(cat, texto, titulo=titulo, proyecto=proyecto)
        if conflictos["duplicado"] and not permitir_duplicado:
            return {
                "estado": "duplicado",
                "recuerdo": conflictos["duplicado"],
                "mensaje": f"Ya existe un recuerdo equivalente (ID #{conflictos['duplicado']['id']}).",
            }

        if conflictos["contradiccion"]:
            existente = conflictos["contradiccion"]
            if reemplazar_contradiccion:
                actualizado = self.actualizar(
                    existente["id"],
                    contenido=texto,
                    titulo=titulo or existente["titulo"],
                    proyecto=proyecto or existente["proyecto"],
                    etiquetas=etiquetas,
                    metadata=metadata,
                    expires_at=expires_at,
                )
                return {
                    "estado": "actualizado_por_contradiccion",
                    "recuerdo": actualizado,
                    "anterior": existente,
                    "mensaje": f"Se actualizó el recuerdo #{existente['id']} que entraba en conflicto.",
                }
            return {
                "estado": "contradiccion",
                "recuerdo": existente,
                "mensaje": (
                    f"Posible contradicción con el recuerdo #{existente['id']}: "
                    f"«{existente['contenido']}». Confirmá si querés reemplazarlo."
                ),
            }

        ahora = _ahora_iso()
        etiquetas_limpias = [str(e).strip() for e in (etiquetas or []) if str(e).strip()]
        meta_limpia = dict(metadata or {})

        with self._lock:
            with self._conectar() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO recuerdos
                    (categoria, titulo, contenido, proyecto, etiquetas, metadata_json, confirmado, expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cat,
                        str(titulo or "").strip(),
                        texto,
                        str(proyecto or "").strip(),
                        json.dumps(etiquetas_limpias, ensure_ascii=False),
                        json.dumps(meta_limpia, ensure_ascii=False),
                        1 if confirmado else 0,
                        expires_at,
                        ahora,
                        ahora,
                    ),
                )
                nuevo_id = cur.lastrowid
                conn.commit()
                row = conn.execute("SELECT * FROM recuerdos WHERE id = ?", (nuevo_id,)).fetchone()
        return {
            "estado": "creado",
            "recuerdo": self._row_to_dict(row),
            "mensaje": f"Recuerdo #{nuevo_id} guardado en {cat}.",
        }

    def obtener(self, recuerdo_id: int) -> dict[str, Any] | None:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                row = conn.execute("SELECT * FROM recuerdos WHERE id = ?", (int(recuerdo_id),)).fetchone()
                return self._row_to_dict(row) if row else None

    def listar(
        self,
        categoria: str | None = None,
        proyecto: str | None = None,
        incluir_expirados: bool = False,
        limite: int = 100,
    ) -> list[dict[str, Any]]:
        self.inicializar()
        filtros = []
        params: list[Any] = []
        if categoria:
            filtros.append("categoria = ?")
            params.append(self._validar_categoria(categoria))
        if proyecto:
            filtros.append("LOWER(proyecto) = LOWER(?)")
            params.append(proyecto.strip())
        if not incluir_expirados:
            filtros.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_ahora_iso())

        where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
        sql = f"SELECT * FROM recuerdos {where} ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limite)))
        with self._lock:
            with self._conectar() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [self._row_to_dict(r) for r in rows]

    def buscar(
        self,
        consulta: str = "",
        categoria: str | None = None,
        proyecto: str | None = None,
        limite: int = 20,
    ) -> list[dict[str, Any]]:
        """Busca recuerdos por categoría, proyecto y palabras clave."""
        candidatos = self.listar(categoria=categoria, proyecto=proyecto, incluir_expirados=False, limite=300)
        consulta_limpia = _normalizar(consulta)
        if not consulta_limpia:
            return candidatos[:limite]

        tokens_q = _tokens(consulta_limpia)
        puntuados: list[tuple[float, dict[str, Any]]] = []
        for item in candidatos:
            campo_texto = " ".join(
                [
                    item["titulo"],
                    item["contenido"],
                    item["proyecto"],
                    " ".join(item["etiquetas"]),
                    json.dumps(item["metadata"], ensure_ascii=False),
                ]
            )
            norm_campo = _normalizar(campo_texto)
            score = 0.0
            if consulta_limpia in norm_campo:
                score += 5.0
            if tokens_q:
                tokens_item = _tokens(campo_texto)
                coincidencias = len(tokens_q & tokens_item)
                if coincidencias > 0:
                    score += coincidencias * 2.0
            if score > 0:
                puntuados.append((score, item))

        puntuados.sort(key=lambda x: (x[0], x[1]["updated_at"]), reverse=True)
        return [item for _, item in puntuados[:limite]]

    def actualizar(
        self,
        recuerdo_id: int,
        contenido: str | None = None,
        categoria: str | None = None,
        titulo: str | None = None,
        proyecto: str | None = None,
        etiquetas: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any] | None:
        self.inicializar()
        actual = self.obtener(recuerdo_id)
        if not actual:
            return None

        nuevo_contenido = actual["contenido"] if contenido is None else str(contenido).strip()
        if not nuevo_contenido:
            raise ValueError("El contenido no puede quedar vacío.")
        sensible, motivo = detectar_informacion_sensible(nuevo_contenido)
        if sensible:
            raise ValueError(f"Bloqueado por seguridad: parece contener {motivo}.")

        nueva_cat = actual["categoria"] if categoria is None else self._validar_categoria(categoria)
        nuevo_titulo = actual["titulo"] if titulo is None else str(titulo).strip()
        nuevo_proyecto = actual["proyecto"] if proyecto is None else str(proyecto).strip()
        nuevas_etiquetas = actual["etiquetas"] if etiquetas is None else [str(e).strip() for e in etiquetas if str(e).strip()]
        nueva_meta = dict(actual["metadata"])
        if metadata is not None:
            nueva_meta.update(metadata)
        nuevo_exp = actual["expires_at"] if expires_at is None else expires_at
        ahora = _ahora_iso()

        with self._lock:
            with self._conectar() as conn:
                conn.execute(
                    """
                    UPDATE recuerdos
                    SET categoria = ?, titulo = ?, contenido = ?, proyecto = ?,
                        etiquetas = ?, metadata_json = ?, expires_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        nueva_cat,
                        nuevo_titulo,
                        nuevo_contenido,
                        nuevo_proyecto,
                        json.dumps(nuevas_etiquetas, ensure_ascii=False),
                        json.dumps(nueva_meta, ensure_ascii=False),
                        nuevo_exp,
                        ahora,
                        int(recuerdo_id),
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM recuerdos WHERE id = ?", (int(recuerdo_id),)).fetchone()
        return self._row_to_dict(row) if row else None

    def eliminar(self, recuerdo_id: int) -> bool:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                cur = conn.execute("DELETE FROM recuerdos WHERE id = ?", (int(recuerdo_id),))
                conn.commit()
                return cur.rowcount > 0

    def recuperar_para_conversacion(self, consulta: str = "", limite_relevantes: int = 6) -> list[dict[str, Any]]:
        """Recupera solo perfil/preferencias esenciales + recuerdos relevantes al turno actual."""
        self.inicializar()
        seleccionados: dict[int, dict[str, Any]] = {}

        # Siempre incluir perfil y preferencias esenciales (acotado)
        for cat, lim in (("perfil", 4), ("preferencias", 4)):
            for item in self.listar(categoria=cat, limite=lim):
                seleccionados[item["id"]] = item

        # Contexto temporal vigente más reciente (máximo 2)
        for item in self.listar(categoria="contexto_temporal", limite=2):
            seleccionados[item["id"]] = item

        # Si hay consulta, buscar relevantes en todas las categorías
        if consulta and consulta.strip():
            for item in self.buscar(consulta, limite=limite_relevantes):
                seleccionados[item["id"]] = item
        else:
            # Sin consulta específica, incluir proyectos activos y tareas pendientes recientes
            for cat, lim in (("proyectos", 3), ("tareas", 3)):
                for item in self.listar(categoria=cat, limite=lim):
                    if item["metadata"].get("estado") in ("completada", "cancelada", "archivado"):
                        continue
                    seleccionados[item["id"]] = item

        resultado = list(seleccionados.values())
        if resultado:
            ids = [r["id"] for r in resultado]
            placeholders = ",".join("?" for _ in ids)
            with self._lock:
                with self._conectar() as conn:
                    conn.execute(
                        f"UPDATE recuerdos SET access_count = access_count + 1 WHERE id IN ({placeholders})",
                        ids,
                    )
                    conn.commit()
        return resultado

    def formatear_para_prompt(self, consulta: str = "") -> str:
        """Formatea la memoria relevante para inyectarla en el system prompt."""
        recuerdos = self.recuperar_para_conversacion(consulta=consulta)
        if not recuerdos:
            return "(nada todavía)"
        lineas = []
        for r in recuerdos:
            prefijo = f"[{r['categoria']}]"
            if r["proyecto"]:
                prefijo += f" (proyecto: {r['proyecto']})"
            if r["titulo"]:
                prefijo += f" {r['titulo']}:"
            lineas.append(f"- {prefijo} {r['contenido']}")
        return "\n".join(lineas)

    def resumen_estadisticas(self) -> dict[str, Any]:
        self.inicializar()
        with self._lock:
            with self._conectar() as conn:
                rows = conn.execute(
                    "SELECT categoria, COUNT(*) as total FROM recuerdos "
                    "WHERE expires_at IS NULL OR expires_at > ? GROUP BY categoria",
                    (_ahora_iso(),),
                ).fetchall()
        por_categoria = {cat: 0 for cat in CATEGORIAS_VALIDAS}
        for row in rows:
            por_categoria[row["categoria"]] = row["total"]
        return {
            "total": sum(por_categoria.values()),
            "por_categoria": por_categoria,
            "db_path": str(self.db_path),
        }


_STORE: MemoryStore | None = None
_STORE_LOCK = threading.Lock()


def obtener_store(db_path: str | Path | None = None) -> MemoryStore:
    """Devuelve el MemoryStore singleton configurado para CARPETA_DATOS."""
    global _STORE
    ruta_esperada = Path(db_path) if db_path else (config.CARPETA_DATOS / "memoria.db")
    with _STORE_LOCK:
        if _STORE is None or _STORE.db_path != ruta_esperada:
            _STORE = MemoryStore(
                db_path=ruta_esperada,
                json_legacy_path=config.CARPETA_DATOS / "memoria.json",
            )
            _STORE.inicializar()
        return _STORE
