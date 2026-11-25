"""Integración oficial de Google Workspace (OAuth 2.0 + Calendar + Gmail + Drive) para JARVIS."""
from __future__ import annotations

import base64
import datetime
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
]

KEYRING_SERVICE = "JarvisGoogle"
KEYRING_CLIENT_KEY = "oauth_client"
KEYRING_TOKEN_KEY = "oauth_tokens"


class GoogleAuthError(RuntimeError):
    """Error de autenticación o permisos con Google OAuth."""


class GoogleAPIError(RuntimeError):
    """Error al invocar una API de Google Workspace."""


def _keyring():
    import keyring

    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        keyring.set_keyring(WinVaultKeyring())
    return keyring


def _guardar_secreto(clave: str, datos: dict[str, Any]) -> None:
    kr = _keyring()
    kr.set_password(KEYRING_SERVICE, clave, json.dumps(datos, ensure_ascii=False))


def _leer_secreto(clave: str) -> dict[str, Any] | None:
    try:
        kr = _keyring()
        raw = kr.get_password(KEYRING_SERVICE, clave)
        if not raw:
            return None
        datos = json.loads(raw)
        return datos if isinstance(datos, dict) else None
    except Exception:
        return None


def _eliminar_secreto(clave: str) -> None:
    try:
        kr = _keyring()
        kr.delete_password(KEYRING_SERVICE, clave)
    except Exception:
        pass


def configurar_cliente_oauth(client_id: str, client_secret: str = "") -> None:
    """Guarda el Client ID (y Client Secret opcional) en Windows Credential Manager."""
    cid = str(client_id or "").strip()
    csec = str(client_secret or "").strip()
    if not cid:
        raise ValueError("El Client ID de Google no puede estar vacío.")
    _guardar_secreto(KEYRING_CLIENT_KEY, {"client_id": cid, "client_secret": csec})


def obtener_cliente_oauth() -> dict[str, str]:
    guardado = _leer_secreto(KEYRING_CLIENT_KEY) or {}
    cid = (guardado.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID", "")).strip()
    csec = (guardado.get("client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET", "")).strip()
    return {"client_id": cid, "client_secret": csec}


def estado_conexion() -> dict[str, Any]:
    """Devuelve el estado actual de la cuenta de Google sin exponer tokens."""
    cliente = obtener_cliente_oauth()
    tokens = _leer_secreto(KEYRING_TOKEN_KEY)
    conectado = bool(tokens and (tokens.get("access_token") or tokens.get("refresh_token")))
    return {
        "client_configured": bool(cliente["client_id"]),
        "connected": conectado,
        "email": (tokens or {}).get("email", "") if conectado else "",
        "scopes": (tokens or {}).get("scopes", []) if conectado else [],
    }


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def iniciar_oauth_escritorio(timeout_segundos: int = 120) -> dict[str, Any]:
    """Ejecuta el flujo OAuth 2.0 PKCE para aplicación de escritorio y guarda los tokens."""
    cliente = obtener_cliente_oauth()
    if not cliente["client_id"]:
        raise GoogleAuthError(
            "Falta configurar el Client ID de Google OAuth (en Conexiones o GOOGLE_CLIENT_ID)."
        )

    verifier, challenge = _pkce_pair()
    estado = secrets.token_urlsafe(24)
    resultado: dict[str, Any] = {}
    evento = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            resultado["code"] = (params.get("code") or [""])[0]
            resultado["state"] = (params.get("state") or [""])[0]
            resultado["error"] = (params.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;background:#050c15;color:#eaf7ff;text-align:center;padding:40px'>"
                b"<h2>JARVIS // Google OAuth completado</h2>"
                b"<p>Podes cerrar esta ventana y volver a JARVIS.</p></body></html>"
            )
            evento.set()

    servidor = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
    puerto = servidor.server_address[1]
    redirect_uri = f"http://127.0.0.1:{puerto}/callback"

    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        query = urllib.parse.urlencode(
            {
                "client_id": cliente["client_id"],
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GOOGLE_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": estado,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        webbrowser.open(f"{GOOGLE_AUTH_URL}?{query}")
        if not evento.wait(timeout=timeout_segundos):
            raise GoogleAuthError("Tiempo de espera agotado esperando la autorización de Google.")
    finally:
        servidor.shutdown()
        servidor.server_close()

    if resultado.get("error"):
        raise GoogleAuthError(f"Autorización cancelada o rechazada: {resultado['error']}")
    if resultado.get("state") != estado or not resultado.get("code"):
        raise GoogleAuthError("Respuesta OAuth inválida (state o code incorrecto).")

    payload = {
        "client_id": cliente["client_id"],
        "code": resultado["code"],
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if cliente["client_secret"]:
        payload["client_secret"] = cliente["client_secret"]

    with httpx.Client(timeout=20.0) as client:
        resp = client.post(GOOGLE_TOKEN_URL, data=payload)
        if resp.status_code >= 400:
            raise GoogleAuthError(f"No pude obtener el token de Google ({resp.status_code}): {resp.text[:200]}")
        token_data = resp.json()

    expires_in = int(token_data.get("expires_in", 3600))
    tokens = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at": time.time() + expires_in - 60,
        "scopes": (token_data.get("scope") or "").split(),
        "email": "",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            u = client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if u.status_code == 200:
                tokens["email"] = u.json().get("email", "")
    except Exception:
        pass

    _guardar_secreto(KEYRING_TOKEN_KEY, tokens)
    return estado_conexion()


def _refrescar_token(tokens: dict[str, Any]) -> dict[str, Any]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise GoogleAuthError("La sesión de Google expiró y no hay refresh_token. Volvé a conectar tu cuenta.")
    cliente = obtener_cliente_oauth()
    if not cliente["client_id"]:
        raise GoogleAuthError("Falta el Client ID de Google para refrescar la sesión.")
    payload = {
        "client_id": cliente["client_id"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if cliente["client_secret"]:
        payload["client_secret"] = cliente["client_secret"]
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(GOOGLE_TOKEN_URL, data=payload)
        if resp.status_code >= 400:
            raise GoogleAuthError("No se pudo renovar el token de Google. Reconectá tu cuenta.")
        data = resp.json()
    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    _guardar_secreto(KEYRING_TOKEN_KEY, tokens)
    return tokens


def _obtener_access_token() -> str:
    tokens = _leer_secreto(KEYRING_TOKEN_KEY)
    if not tokens or not tokens.get("access_token"):
        raise GoogleAuthError("Google no está conectado. Conectá tu cuenta desde el HUD.")
    if time.time() >= float(tokens.get("expires_at", 0)):
        tokens = _refrescar_token(tokens)
    return tokens["access_token"]


def desconectar() -> dict[str, Any]:
    """Revoca el token en Google (si existe) y elimina las credenciales locales."""
    tokens = _leer_secreto(KEYRING_TOKEN_KEY)
    token_a_revocar = (tokens or {}).get("refresh_token") or (tokens or {}).get("access_token")
    if token_a_revocar:
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(GOOGLE_REVOKE_URL, params={"token": token_a_revocar})
        except Exception:
            pass
    _eliminar_secreto(KEYRING_TOKEN_KEY)
    return estado_conexion()


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    token = _obtener_access_token()
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=25.0) as client:
        resp = client.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            tokens = _leer_secreto(KEYRING_TOKEN_KEY) or {}
            tokens = _refrescar_token(tokens)
            headers["Authorization"] = f"Bearer {tokens['access_token']}"
            resp = client.request(method, url, headers=headers, **kwargs)
    if resp.status_code == 403:
        raise GoogleAuthError("Permisos insuficientes en Google para esta operación.")
    if resp.status_code >= 400:
        raise GoogleAPIError(f"Error de Google API ({resp.status_code}): {resp.text[:300]}")
    return resp


# ------------------------------------------------------------------ Calendar
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


def listar_eventos(dias: int = 7, fecha: str | None = None, maximo: int = 10) -> list[dict[str, Any]]:
    ahora = datetime.datetime.now(datetime.timezone.utc)
    if fecha:
        dia = datetime.datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
        time_min = dia.isoformat()
        time_max = (dia + datetime.timedelta(days=1)).isoformat()
    else:
        time_min = ahora.isoformat()
        time_max = (ahora + datetime.timedelta(days=max(1, int(dias)))).isoformat()
    resp = _request(
        "GET",
        f"{CALENDAR_BASE}/calendars/primary/events",
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(int(maximo), 50)),
        },
    )
    items = resp.json().get("items", [])
    return [
        {
            "id": e.get("id", ""),
            "titulo": e.get("summary", "(Sin título)"),
            "inicio": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", ""),
            "fin": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date", ""),
            "ubicacion": e.get("location", ""),
            "descripcion": e.get("description", ""),
        }
        for e in items
    ]


def crear_evento(
    titulo: str,
    inicio_iso: str,
    fin_iso: str,
    descripcion: str = "",
    ubicacion: str = "",
) -> dict[str, Any]:
    cuerpo = {
        "summary": titulo,
        "start": {"dateTime": inicio_iso},
        "end": {"dateTime": fin_iso},
    }
    if descripcion:
        cuerpo["description"] = descripcion
    if ubicacion:
        cuerpo["location"] = ubicacion
    resp = _request("POST", f"{CALENDAR_BASE}/calendars/primary/events", json=cuerpo)
    return resp.json()


def modificar_evento(
    evento_id: str,
    titulo: str | None = None,
    inicio_iso: str | None = None,
    fin_iso: str | None = None,
    descripcion: str | None = None,
) -> dict[str, Any]:
    parche: dict[str, Any] = {}
    if titulo is not None:
        parche["summary"] = titulo
    if inicio_iso is not None:
        parche["start"] = {"dateTime": inicio_iso}
    if fin_iso is not None:
        parche["end"] = {"dateTime": fin_iso}
    if descripcion is not None:
        parche["description"] = descripcion
    resp = _request("PATCH", f"{CALENDAR_BASE}/calendars/primary/events/{evento_id}", json=parche)
    return resp.json()


def eliminar_evento(evento_id: str) -> bool:
    _request("DELETE", f"{CALENDAR_BASE}/calendars/primary/events/{evento_id}")
    return True


def consultar_disponibilidad(inicio_iso: str, fin_iso: str) -> list[dict[str, str]]:
    resp = _request(
        "POST",
        f"{CALENDAR_BASE}/freeBusy",
        json={"timeMin": inicio_iso, "timeMax": fin_iso, "items": [{"id": "primary"}]},
    )
    return resp.json().get("calendars", {}).get("primary", {}).get("busy", [])


def crear_recordatorio_evento(evento_id: str, minutos_antes: int = 15) -> dict[str, Any]:
    parche = {
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": max(0, int(minutos_antes))}],
        }
    }
    resp = _request("PATCH", f"{CALENDAR_BASE}/calendars/primary/events/{evento_id}", json=parche)
    return resp.json()


# ------------------------------------------------------------------ Gmail
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def listar_correos(maximo: int = 10, consulta: str = "") -> list[dict[str, str]]:
    params: dict[str, Any] = {"maxResults": max(1, min(int(maximo), 30))}
    if consulta:
        params["q"] = consulta
    resp = _request("GET", f"{GMAIL_BASE}/messages", params=params)
    mensajes = resp.json().get("messages", [])
    salida = []
    for m in mensajes:
        det = _request(
            "GET",
            f"{GMAIL_BASE}/messages/{m['id']}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
        ).json()
        headers = {h["name"].casefold(): h["value"] for h in det.get("payload", {}).get("headers", [])}
        salida.append(
            {
                "id": det.get("id", m["id"]),
                "de": headers.get("from", ""),
                "asunto": headers.get("subject", "(Sin asunto)"),
                "fecha": headers.get("date", ""),
                "snippet": det.get("snippet", ""),
                "no_leido": "UNREAD" in det.get("labelIds", []),
            }
        )
    return salida


def _decodificar_b64url(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def leer_correo(mensaje_id: str) -> dict[str, Any]:
    det = _request("GET", f"{GMAIL_BASE}/messages/{mensaje_id}", params={"format": "full"}).json()
    payload = det.get("payload", {})
    headers = {h["name"].casefold(): h["value"] for h in payload.get("headers", [])}
    cuerpo = ""
    if payload.get("body", {}).get("data"):
        cuerpo = _decodificar_b64url(payload["body"]["data"])
    else:
        for parte in payload.get("parts", []):
            if parte.get("mimeType") == "text/plain" and parte.get("body", {}).get("data"):
                cuerpo = _decodificar_b64url(parte["body"]["data"])
                break
    return {
        "id": det.get("id", mensaje_id),
        "threadId": det.get("threadId", ""),
        "de": headers.get("from", ""),
        "para": headers.get("to", ""),
        "asunto": headers.get("subject", ""),
        "fecha": headers.get("date", ""),
        "cuerpo": cuerpo[:8000] or det.get("snippet", ""),
    }


def _construir_raw_mime(para: str, asunto: str, cuerpo: str) -> str:
    msg = EmailMessage()
    msg["To"] = para
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def crear_borrador(para: str, asunto: str, cuerpo: str, thread_id: str = "") -> dict[str, Any]:
    message: dict[str, Any] = {"raw": _construir_raw_mime(para, asunto, cuerpo)}
    if thread_id:
        message["threadId"] = thread_id
    resp = _request("POST", f"{GMAIL_BASE}/drafts", json={"message": message})
    return resp.json()


def enviar_correo(para: str, asunto: str, cuerpo: str, thread_id: str = "") -> dict[str, Any]:
    message: dict[str, Any] = {"raw": _construir_raw_mime(para, asunto, cuerpo)}
    if thread_id:
        message["threadId"] = thread_id
    resp = _request("POST", f"{GMAIL_BASE}/messages/send", json=message)
    return resp.json()


def marcar_correo_leido(mensaje_id: str, leido: bool = True) -> dict[str, Any]:
    cuerpo = {"removeLabelIds": ["UNREAD"]} if leido else {"addLabelIds": ["UNREAD"]}
    resp = _request("POST", f"{GMAIL_BASE}/messages/{mensaje_id}/modify", json=cuerpo)
    return resp.json()


# ------------------------------------------------------------------ Drive
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


def listar_archivos_drive(consulta: str = "", carpeta_id: str = "", maximo: int = 20) -> list[dict[str, Any]]:
    filtros = ["trashed = false"]
    if carpeta_id:
        filtros.append(f"'{carpeta_id}' in parents")
    if consulta:
        limpia = consulta.replace("'", "\\'")
        filtros.append(f"name contains '{limpia}'")
    resp = _request(
        "GET",
        f"{DRIVE_BASE}/files",
        params={
            "q": " and ".join(filtros),
            "pageSize": max(1, min(int(maximo), 50)),
            "fields": "files(id, name, mimeType, size, modifiedTime, parents)",
        },
    )
    return resp.json().get("files", [])


def obtener_metadatos_drive(archivo_id: str) -> dict[str, Any]:
    resp = _request(
        "GET",
        f"{DRIVE_BASE}/files/{archivo_id}",
        params={"fields": "id, name, mimeType, size, modifiedTime, createdTime, parents, webViewLink"},
    )
    return resp.json()


def descargar_archivo_drive(archivo_id: str, destino: str | Path) -> Path:
    meta = obtener_metadatos_drive(archivo_id)
    mime = meta.get("mimeType", "")
    destino_path = Path(destino)
    destino_path.parent.mkdir(parents=True, exist_ok=True)
    if mime.startswith("application/vnd.google-apps."):
        export_mime = "text/plain" if "document" in mime else "text/csv"
        resp = _request(
            "GET",
            f"{DRIVE_BASE}/files/{archivo_id}/export",
            params={"mimeType": export_mime},
        )
    else:
        resp = _request("GET", f"{DRIVE_BASE}/files/{archivo_id}", params={"alt": "media"})
    destino_path.write_bytes(resp.content)
    return destino_path


def leer_documento_drive(archivo_id: str) -> str:
    meta = obtener_metadatos_drive(archivo_id)
    mime = meta.get("mimeType", "")
    if mime == "application/vnd.google-apps.document":
        resp = _request(
            "GET",
            f"{DRIVE_BASE}/files/{archivo_id}/export",
            params={"mimeType": "text/plain"},
        )
        return resp.text[:12000]
    if mime == "application/vnd.google-apps.spreadsheet":
        resp = _request(
            "GET",
            f"{DRIVE_BASE}/files/{archivo_id}/export",
            params={"mimeType": "text/csv"},
        )
        return resp.text[:12000]
    if mime.startswith("text/") or mime == "application/json":
        resp = _request("GET", f"{DRIVE_BASE}/files/{archivo_id}", params={"alt": "media"})
        return resp.text[:12000]
    raise GoogleAPIError(f"Tipo de archivo no compatible para lectura directa: {mime}")


def crear_carpeta_drive(nombre: str, padre_id: str = "") -> dict[str, Any]:
    meta: dict[str, Any] = {
        "name": nombre,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if padre_id:
        meta["parents"] = [padre_id]
    resp = _request("POST", f"{DRIVE_BASE}/files", json=meta)
    return resp.json()


def subir_archivo_drive(ruta_local: str | Path, carpeta_id: str = "", nombre_remoto: str = "") -> dict[str, Any]:
    origen = Path(ruta_local)
    if not origen.is_file():
        raise FileNotFoundError(f"No existe el archivo local: {origen}")
    meta: dict[str, Any] = {"name": nombre_remoto or origen.name}
    if carpeta_id:
        meta["parents"] = [carpeta_id]
    boundary = f"jarvis_{secrets.token_hex(8)}"
    contenido = origen.read_bytes()
    cuerpo = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(meta, ensure_ascii=False)}\r\n"
        f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + contenido + f"\r\n--{boundary}--\r\n".encode("utf-8")
    resp = _request(
        "POST",
        f"{DRIVE_UPLOAD_BASE}/files",
        params={"uploadType": "multipart"},
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        content=cuerpo,
    )
    return resp.json()


def organizar_archivo_drive(
    archivo_id: str,
    nuevo_nombre: str = "",
    nueva_carpeta_id: str = "",
) -> dict[str, Any]:
    params: dict[str, str] = {}
    body: dict[str, Any] = {}
    if nuevo_nombre:
        body["name"] = nuevo_nombre
    if nueva_carpeta_id:
        actual = obtener_metadatos_drive(archivo_id)
        anteriores = ",".join(actual.get("parents", []))
        params["addParents"] = nueva_carpeta_id
        if anteriores:
            params["removeParents"] = anteriores
    resp = _request("PATCH", f"{DRIVE_BASE}/files/{archivo_id}", params=params, json=body)
    return resp.json()
