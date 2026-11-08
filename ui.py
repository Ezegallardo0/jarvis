"""Servidor local del HUD: página web + eventos en vivo (SSE).

Seguridad: solo escucha en 127.0.0.1, exige un token aleatorio en cada petición
y valida el encabezado Host, para que ninguna página web externa pueda dar
órdenes a Jarvis.
"""
import io
import json
import queue
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psutil

import config

HUD_DIR = Path(__file__).parent / "hud"


class Bus:
    """Reparte eventos a todas las ventanas del HUD y recibe confirmaciones."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()
        self.ultimo = {}
        self.mensajes = deque(maxlen=40)
        self._resp = queue.Queue()

    def publish(self, tipo, **datos):
        ev = {"tipo": tipo, **datos}
        with self._lock:
            if tipo == "msg":
                self.mensajes.append(ev)
            elif tipo in ("estado", "stats", "spotify"):
                self.ultimo[tipo] = ev
            subs = list(self._subs)
        for q in subs:
            q.put(ev)

    def suscribir(self):
        q = queue.Queue()
        with self._lock:
            self._subs.append(q)
            inicial = list(self.mensajes) + list(self.ultimo.values())
        for ev in inicial:
            q.put(ev)
        return q

    def desuscribir(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def hay_hud(self):
        with self._lock:
            return bool(self._subs)

    def preguntar(self, texto, timeout=30):
        """Muestra un cuadro Sí/No en el HUD. Sin respuesta a tiempo = No."""
        while not self._resp.empty():
            self._resp.get_nowait()
        self.publish("confirmar", texto=texto)
        try:
            ok = self._resp.get(timeout=timeout)
        except queue.Empty:
            ok = False
        self.publish("confirmar_fin")
        return ok

    def responder(self, ok):
        self._resp.put(bool(ok))


class Servidor:
    def __init__(self, bus, entradas, accion_spotify=None):
        self.bus = bus
        self.entradas = entradas
        self.accion_spotify = accion_spotify
        self.puerto = config.HUD_PUERTO
        self.token = secrets.token_urlsafe(16)
        self.httpd = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.puerto}/?t={self.token}"

    def _captura_jpeg(self):
        from PIL import ImageGrab

        img = ImageGrab.grab()
        img.thumbnail((720, 720))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=60)
        return buf.getvalue()

    def _stats_loop(self):
        psutil.cpu_percent(interval=None)
        while True:
            try:
                ram = psutil.virtual_memory()
                disco = psutil.disk_usage(str(Path.home().anchor or "/"))
                bat = psutil.sensors_battery()
                self.bus.publish(
                    "stats",
                    cpu=psutil.cpu_percent(interval=None),
                    ram=ram.percent,
                    disco=disco.percent,
                    bateria=None if bat is None else round(bat.percent),
                    cargando=None if bat is None else bool(bat.power_plugged),
                )
            except Exception:
                pass
            time.sleep(2)

    def iniciar(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _autorizado(self):
                host = self.headers.get("Host", "")
                if host not in (f"127.0.0.1:{srv.puerto}", f"localhost:{srv.puerto}"):
                    return False
                q = parse_qs(urlparse(self.path).query)
                tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
                return secrets.compare_digest(tok, srv.token)

            def _enviar(self, codigo, tipo, cuerpo):
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self):
                if not self._autorizado():
                    return self._enviar(403, "text/plain", b"Prohibido")
                ruta = urlparse(self.path).path
                if ruta == "/":
                    html = (HUD_DIR / "index.html").read_text(encoding="utf-8")
                    html = html.replace("{{NOMBRE}}", config.NOMBRE)
                    return self._enviar(200, "text/html; charset=utf-8", html.encode("utf-8"))
                if ruta == "/screen.jpg":
                    try:
                        return self._enviar(200, "image/jpeg", srv._captura_jpeg())
                    except Exception:
                        return self._enviar(500, "text/plain", b"Sin captura")
                if ruta == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    q = srv.bus.suscribir()
                    try:
                        while True:
                            try:
                                ev = q.get(timeout=15)
                                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                            except queue.Empty:
                                self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                    except OSError:
                        pass
                    finally:
                        srv.bus.desuscribir(q)
                    return
                self._enviar(404, "text/plain", b"No existe")

            def do_POST(self):
                if not self._autorizado():
                    return self._enviar(403, "text/plain", b"Prohibido")
                try:
                    largo = min(int(self.headers.get("Content-Length", 0)), 10_000)
                    datos = json.loads(self.rfile.read(largo) or b"{}")
                except Exception:
                    return self._enviar(400, "text/plain", b"JSON invalido")
                ruta = urlparse(self.path).path
                resultado = {"ok": True}
                if ruta == "/say":
                    texto = str(datos.get("texto", "")).strip()[:1000]
                    if texto:
                        srv.entradas.put(texto)
                elif ruta == "/confirm":
                    srv.bus.responder(datos.get("ok"))
                elif ruta == "/screen":
                    config.COMPARTIENDO_PANTALLA = bool(datos.get("on"))
                elif ruta == "/spotify" and srv.accion_spotify:
                    try:
                        srv.accion_spotify(str(datos.get("accion", "")))
                    except Exception as e:
                        resultado = {"ok": False, "error": str(e)}
                else:
                    return self._enviar(404, "text/plain", b"No existe")
                self._enviar(200, "application/json", json.dumps(resultado).encode())

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.puerto), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._stats_loop, daemon=True).start()