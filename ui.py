"""Servidor local del HUD: página web + eventos en vivo (SSE).

Seguridad: solo escucha en 127.0.0.1, exige un token aleatorio en cada petición
y valida el encabezado Host, para que ninguna página web externa pueda dar
órdenes a Jarvis.
"""
import io
import json
import queue
import secrets
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psutil

import config

HUD_DIR = Path(__file__).parent / "hud"


class _ServidorExclusivo(ThreadingHTTPServer):
    """Servidor que NO comparte puerto con otro proceso.

    En Windows, SO_REUSEADDR deja que dos programas escuchen en el mismo puerto y las
    conexiones caen en cualquiera de los dos (de ahí un «Prohibido» con el token de otra sesión).
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # solo existe en Windows
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


PAGINA_PROHIBIDO = """<!doctype html><meta charset="utf-8"><title>Sesión no válida</title>
<body style="background:#02070d;color:#d9f3ff;font-family:Segoe UI,sans-serif;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center;max-width:460px;line-height:1.6"><h2 style="color:#ffb347;letter-spacing:.2em">SESIÓN NO VÁLIDA</h2>
<p>Esta ventana pertenece a otra sesión de Jarvis que ya no está activa.<br>Cerrala y abrí Jarvis de nuevo con <b>run.vbs</b>.</p></div></body>"""


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
            elif tipo in ("estado", "stats", "spotify", "info", "pantalla"):
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
    def __init__(
        self,
        bus,
        entradas,
        accion_spotify=None,
        accion_app=None,
        leer_configuracion=None,
        guardar_configuracion=None,
        probar_audio=None,
        probar_github=None,
        detectar_ubicacion=None,
        probar_ia=None,
    ):
        self.bus = bus
        self.entradas = entradas
        self.accion_spotify = accion_spotify
        self.accion_app = accion_app
        self.leer_configuracion = leer_configuracion
        self.guardar_configuracion = guardar_configuracion
        self.probar_audio = probar_audio
        self.probar_github = probar_github
        self.detectar_ubicacion = detectar_ubicacion
        self.probar_ia = probar_ia
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
        if self.httpd is not None:
            return
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
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
                if urlparse(self.path).path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                if not self._autorizado():
                    return self._enviar(403, "text/html; charset=utf-8", PAGINA_PROHIBIDO.encode("utf-8"))
                ruta = urlparse(self.path).path
                if ruta == "/":
                    html = (HUD_DIR / "index.html").read_text(encoding="utf-8")
                    html = html.replace("{{NOMBRE}}", config.NOMBRE)
                    return self._enviar(200, "text/html; charset=utf-8", html.encode("utf-8"))
                if ruta == "/settings" and srv.leer_configuracion:
                    try:
                        cuerpo = json.dumps(
                            srv.leer_configuracion(), ensure_ascii=False
                        ).encode("utf-8")
                    except Exception as error:
                        cuerpo = json.dumps({"error": str(error)}).encode("utf-8")
                        return self._enviar(500, "application/json; charset=utf-8", cuerpo)
                    return self._enviar(200, "application/json; charset=utf-8", cuerpo)
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
                elif ruta == "/settings" and srv.guardar_configuracion:
                    try:
                        configuracion = srv.guardar_configuracion(datos)
                        resultado = {"ok": True, "configuracion": configuracion}
                    except ValueError as error:
                        resultado = {"ok": False, "error": str(error)}
                        return self._enviar(
                            400,
                            "application/json; charset=utf-8",
                            json.dumps(resultado, ensure_ascii=False).encode("utf-8"),
                        )
                    except Exception as error:
                        resultado = {"ok": False, "error": str(error)}
                        return self._enviar(
                            500,
                            "application/json; charset=utf-8",
                            json.dumps(resultado, ensure_ascii=False).encode("utf-8"),
                        )
                elif ruta == "/test-audio" and srv.probar_audio:
                    try:
                        resultado = {
                            "ok": True,
                            "resultado": srv.probar_audio(str(datos.get("prueba", ""))),
                        }
                    except Exception as error:
                        resultado = {"ok": False, "error": str(error)}
                        return self._enviar(
                            400,
                            "application/json; charset=utf-8",
                            json.dumps(resultado, ensure_ascii=False).encode("utf-8"),
                        )
                elif ruta == "/test-github" and srv.probar_github:
                    try:
                        resultado = {
                            "ok": True,
                            "resultado": srv.probar_github(),
                        }
                    except Exception as error:
                        resultado = {"ok": False, "error": str(error)}
                        return self._enviar(
                            400,
                            "application/json; charset=utf-8",
                            json.dumps(resultado, ensure_ascii=False).encode("utf-8"),
                        )
                elif ruta == "/test-ai" and srv.probar_ia:
                    try:
                        resultado = {
                            "ok": True,
                            **srv.probar_ia(bool(datos.get("comparar"))),
                        }
                    except Exception:
                        resultado = {
                            "ok": False,
                            "error": "No se pudo completar la prueba de IA.",
                        }
                        return self._enviar(
                            500,
                            "application/json; charset=utf-8",
                            json.dumps(resultado, ensure_ascii=False).encode("utf-8"),
                        )
                elif ruta == "/detect-location" and srv.detectar_ubicacion:
                    try:
                        resultado = {
                            "ok": True,
                            "ubicacion": srv.detectar_ubicacion(),
                        }
                    except Exception as error:
                        resultado = {"ok": False, "error": str(error)}
                        return self._enviar(
                            400,
                            "application/json; charset=utf-8",
                            json.dumps(resultado, ensure_ascii=False).encode("utf-8"),
                        )
                elif ruta == "/confirm":
                    srv.bus.responder(datos.get("ok"))
                elif ruta == "/screen":
                    activar = bool(datos.get("on"))
                    config.COMPARTIENDO_PANTALLA = activar
                    srv.bus.publish("pantalla", on=activar)
                    if srv.accion_app:
                        srv.accion_app("pantalla_sync")
                elif ruta == "/accion" and srv.accion_app:
                    srv.accion_app(str(datos.get("nombre", "")))
                elif ruta == "/spotify" and srv.accion_spotify:
                    try:
                        srv.accion_spotify(str(datos.get("accion", "")))
                    except Exception as e:
                        resultado = {"ok": False, "error": str(e)}
                else:
                    return self._enviar(404, "text/plain", b"No existe")
                self._enviar(200, "application/json", json.dumps(resultado).encode())

        base = config.HUD_PUERTO
        for puerto in range(base, base + 25):  # si el puerto está ocupado, prueba el siguiente
            try:
                self.httpd = _ServidorExclusivo(("127.0.0.1", puerto), Handler)
                self.puerto = puerto
                break
            except OSError:
                continue
        else:
            raise OSError(f"No hay puertos libres entre {base} y {base + 24}")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._stats_loop, daemon=True).start()