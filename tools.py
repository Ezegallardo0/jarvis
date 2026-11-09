"""Herramientas que Jarvis puede usar sobre tu PC (Windows)."""
import ctypes
import datetime
import json
import math
import os
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import httpx
import psutil

import config

HERRAMIENTAS = {}


def NOTIFICAR(texto):  # main.py lo reemplaza para hablar en voz alta
    print(texto)


def CONFIRMAR(texto):  # main.py lo reemplaza (voz o teclado)
    return input(f"{texto} [s/n]: ").strip().lower().startswith(("s", "y"))


def herramienta(descripcion, parametros=None, requeridos=None, confirmar=False):
    """Registra una función como herramienta que el modelo puede invocar."""

    def deco(fn):
        HERRAMIENTAS[fn.__name__] = {
            "fn": fn,
            "confirmar": confirmar,
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": descripcion,
                    "parameters": {
                        "type": "object",
                        "properties": parametros or {},
                        "required": requeridos or [],
                    },
                },
            },
        }
        return fn

    return deco


def esquemas():
    return [h["schema"] for h in HERRAMIENTAS.values()]


def ejecutar(nombre, args):
    h = HERRAMIENTAS.get(nombre)
    if not h:
        return f"Herramienta desconocida: {nombre}"
    args = args or {}
    if h["confirmar"] and not CONFIRMAR(f"Voy a ejecutar {nombre} con {json.dumps(args, ensure_ascii=False)}. ¿Confirmás?"):
        return "El usuario canceló la acción."
    try:
        return str(h["fn"](**args))
    except Exception as e:  # el modelo ve el error y puede explicarlo
        return f"Error al ejecutar {nombre}: {e}"


def _ruta_segura(ruta):
    p = Path(ruta).expanduser().resolve()
    for base in config.CARPETAS_PERMITIDAS:
        try:
            p.relative_to(base.resolve())
            return p
        except ValueError:
            continue
    raise PermissionError(f"Ruta fuera de las carpetas permitidas: {p}")


# ---------------------------------------------------------------- memoria
_MEM = config.CARPETA_DATOS / "memoria.json"


def cargar_memoria():
    try:
        return json.loads(_MEM.read_text(encoding="utf-8"))
    except Exception:
        return []


# ---------------------------------------------------------------- herramientas
@herramienta("Devuelve la fecha y hora actuales.")
def hora_actual():
    return datetime.datetime.now().strftime("%A %d/%m/%Y, %H:%M")


ALIAS_APPS = {
    "calc": "calculadora",
    "calculator": "calculadora",
    "notepad": "bloc de notas",
    "bloc": "bloc de notas",
    "notas": "bloc de notas",
    "explorer": "explorador",
    "archivos": "explorador",
    "explorador de archivos": "explorador",
    "task manager": "administrador de tareas",
    "taskmgr": "administrador de tareas",
    "settings": "configuracion",
    "configuración": "configuracion",
    "ajustes": "configuracion",
    "google chrome": "chrome",
    "visual studio code": "vscode",
    "code": "vscode",
}


@herramienta(
    "Abre una aplicación de la PC. El nombre debe ser exactamente uno de la lista permitida.",
    {"nombre": {"type": "string", "enum": list(config.APPS), "description": "Nombre exacto de la aplicación"}},
    ["nombre"],
)
def abrir_aplicacion(nombre):
    import difflib

    n = nombre.lower().strip()
    n = ALIAS_APPS.get(n, n)
    if n not in config.APPS:
        cerca = difflib.get_close_matches(n, list(config.APPS), n=1, cutoff=0.6)
        if cerca:
            n = cerca[0]
    destino = config.APPS.get(n)
    if not destino:
        return f"NO se abrió nada: no conozco '{nombre}'. Apps disponibles: {', '.join(config.APPS)}"
    if n == "vscode":
        candidatos = []
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidatos.append(Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe")
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            carpeta = os.environ.get(variable)
            if carpeta:
                candidatos.append(Path(carpeta) / "Microsoft VS Code" / "Code.exe")
        destino = next((str(p) for p in candidatos if p.is_file()), destino)
    ejecutable = shutil.which(destino)
    if ejecutable:
        destino = ejecutable
    os.startfile(destino)
    return f"Abriendo {n}."


@herramienta(
    "Cierra las ventanas de una aplicación permitida. Pide confirmación antes de cerrar.",
    {"nombre": {"type": "string", "enum": list(config.APPS), "description": "Aplicación que se va a cerrar"}},
    ["nombre"],
    confirmar=True,
)
def cerrar_aplicacion(nombre):
    n = ALIAS_APPS.get(nombre.lower().strip(), nombre.lower().strip())
    if n not in config.APPS:
        return f"No conozco '{nombre}'. Apps disponibles: {', '.join(config.APPS)}"

    nombres_proceso = {
        "configuracion": {"systemsettings.exe"},
        "terminal": {"wt.exe", "windowsterminal.exe"},
        "vscode": {"code.exe"},
        "spotify": {"spotify.exe"},
        "word": {"winword.exe"},
        "excel": {"excel.exe"},
    }
    ejecutable = Path(config.APPS[n]).name.lower()
    if not ejecutable.endswith(".exe"):
        ejecutable += ".exe"
    objetivos = nombres_proceso.get(n, {ejecutable})

    pids = set()
    for proceso in psutil.process_iter(["pid", "name"]):
        try:
            if (proceso.info["name"] or "").lower() in objetivos:
                pids.add(proceso.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not pids:
        return f"No encontré {n} abierto."

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    cerradas = []
    callback_tipo = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cerrar_ventana(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            if user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                cerradas.append(pid.value)
        return True

    callback = callback_tipo(cerrar_ventana)
    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError()
    cantidad = len(cerradas)
    if not cantidad:
        return f"Encontré {n} abierto, pero no tiene ventanas visibles para cerrar."
    return f"Envié la solicitud de cierre a {cantidad} ventana(s) de {n}."


@herramienta(
    "Abre una página web en el navegador predeterminado.",
    {"url": {"type": "string", "description": "Dirección, ej: https://www.youtube.com"}},
    ["url"],
)
def abrir_web(url):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ValueError("El enlace debe ser una dirección web http o https válida.")
    if not webbrowser.open(url):
        raise OSError("Windows no pudo abrir el enlace en el navegador predeterminado.")
    return f"Abrí {url}."


@herramienta(
    "Busca algo en Google en el navegador.",
    {"consulta": {"type": "string", "description": "Texto a buscar"}},
    ["consulta"],
)
def buscar_en_google(consulta):
    url = f"https://www.google.com/search?q={quote_plus(consulta)}"
    if not webbrowser.open(url):
        raise OSError("Windows no pudo abrir Google en el navegador predeterminado.")
    return f"Busqué '{consulta}' en Google."


@herramienta(
    "Busca información actual en internet. Usala para noticias, datos recientes, preguntas sobre el mundo real o cuando no sepas la respuesta. Devuelve títulos, resúmenes y enlaces.",
    {"consulta": {"type": "string", "description": "Pregunta o términos para buscar"}},
    ["consulta"],
)
def buscar_en_internet(consulta):
    from ddgs import DDGS

    resultados = DDGS().text(consulta.strip()[:300], max_results=5, region="wt-wt")
    if not resultados:
        return "La búsqueda no encontró resultados. Probá reformularla."
    return "\n".join(
        f"{i}. {item.get('title', 'Sin título')}\n"
        f"Resumen: {item.get('body', 'Sin resumen')}\n"
        f"Fuente: {item.get('href', '')}"
        for i, item in enumerate(resultados, 1)
    )


_CODIGOS_CLIMA = {
    0: "despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "niebla",
    48: "niebla con escarcha",
    51: "llovizna ligera",
    53: "llovizna moderada",
    55: "llovizna intensa",
    61: "lluvia ligera",
    63: "lluvia moderada",
    65: "lluvia intensa",
    71: "nieve ligera",
    73: "nieve moderada",
    75: "nieve intensa",
    80: "chaparrones ligeros",
    81: "chaparrones moderados",
    82: "chaparrones intensos",
    95: "tormenta",
    96: "tormenta con granizo leve",
    99: "tormenta con granizo intenso",
}


@herramienta(
    "Consulta el pronóstico meteorológico para hoy o mañana. Si el usuario no indicó ciudad, preguntásela antes de usar esta herramienta.",
    {
        "ubicacion": {"type": "string", "description": "Ciudad y país o región"},
        "dia": {"type": "string", "enum": ["hoy", "mañana"]},
    },
    ["ubicacion", "dia"],
)
def consultar_clima(ubicacion, dia="mañana"):
    nombre = ubicacion.strip()
    if not nombre:
        raise ValueError("Indicá una ciudad para consultar el pronóstico.")
    dia_normalizado = dia.lower().strip().replace("ñ", "n")
    if dia_normalizado not in ("hoy", "manana"):
        raise ValueError("Solo puedo consultar el pronóstico de hoy o mañana.")

    timeout = httpx.Timeout(10.0, connect=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as cliente:
        geocodificacion = cliente.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": nombre, "count": 1, "language": "es", "format": "json"},
        )
        geocodificacion.raise_for_status()
        lugares = geocodificacion.json().get("results") or []
        if not lugares:
            return f"No encontré la ubicación '{nombre}'. Indicá otra ciudad."
        lugar = lugares[0]
        pronostico = cliente.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lugar["latitude"],
                "longitude": lugar["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 2 if dia_normalizado == "manana" else 1,
            },
        )
        pronostico.raise_for_status()
        diarios = pronostico.json().get("daily", {})

    indice = 1 if dia_normalizado == "manana" else 0
    fecha = diarios.get("time", [])[indice]
    codigo = diarios.get("weather_code", [])[indice]
    maxima = diarios.get("temperature_2m_max", [])[indice]
    minima = diarios.get("temperature_2m_min", [])[indice]
    lluvia = diarios.get("precipitation_probability_max", [None])[indice]
    condicion = _CODIGOS_CLIMA.get(codigo, "condiciones variables")
    lugar_formateado = ", ".join(
        parte for parte in (lugar.get("name"), lugar.get("admin1"), lugar.get("country")) if parte
    )
    posibilidad = f", probabilidad máxima de lluvia {lluvia}%" if lluvia is not None else ""
    return (
        f"Pronóstico para {lugar_formateado}, {fecha}: {condicion}, "
        f"mínima {minima} °C, máxima {maxima} °C{posibilidad}. "
        "Fuente: Open-Meteo."
    )


@herramienta("Informa el estado de la PC: CPU, RAM, disco y batería.")
def info_sistema():
    ram = psutil.virtual_memory()
    disco = psutil.disk_usage(str(Path.home().anchor or "/"))
    partes = [
        f"CPU {psutil.cpu_percent(interval=0.5)}%",
        f"RAM {ram.percent}% usada ({ram.used / 1e9:.1f} de {ram.total / 1e9:.1f} GB)",
        f"Disco {disco.percent}% usado ({disco.free / 1e9:.0f} GB libres)",
    ]
    bat = psutil.sensors_battery()
    if bat:
        partes.append(f"Batería {bat.percent:.0f}% ({'cargando' if bat.power_plugged else 'a batería'})")
    return ", ".join(partes)


@herramienta(
    "Sube, baja o silencia el volumen del sistema. Cada paso equivale a un 2%.",
    {
        "accion": {"type": "string", "enum": ["subir", "bajar", "silenciar"]},
        "pasos": {"type": "integer", "description": "Cantidad de pasos (default 5)"},
    },
    ["accion"],
)
def control_volumen(accion, pasos=5):
    teclas = {"subir": 0xAF, "bajar": 0xAE, "silenciar": 0xAD}
    if accion not in teclas:
        return "Acción inválida."
    veces = 1 if accion == "silenciar" else max(1, min(int(pasos), 50))
    user32 = ctypes.windll.user32
    for _ in range(veces):
        user32.keybd_event(teclas[accion], 0, 0, 0)
        user32.keybd_event(teclas[accion], 0, 2, 0)
    return f"Volumen: {accion}."


@herramienta("Saca una captura de pantalla y la guarda en la carpeta de Jarvis.")
def captura_pantalla():
    from PIL import ImageGrab

    carpeta = config.CARPETA_DATOS / "capturas"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"captura_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    ImageGrab.grab().save(ruta)
    return f"Captura guardada en {ruta}"


@herramienta(
    "Lista los archivos y carpetas de una carpeta del usuario.",
    {"carpeta": {"type": "string", "description": "Ruta, ej: ~/Documents"}},
    ["carpeta"],
)
def listar_archivos(carpeta):
    p = _ruta_segura(carpeta)
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:60]
    return "\n".join(("[carpeta] " if i.is_dir() else "") + i.name for i in items) or "(vacía)"


@herramienta(
    "Lee el contenido de un archivo de texto del usuario (máx. 4000 caracteres).",
    {"ruta": {"type": "string", "description": "Ruta del archivo"}},
    ["ruta"],
)
def leer_archivo(ruta):
    p = _ruta_segura(ruta)
    return p.read_text(encoding="utf-8", errors="replace")[:4000]


@herramienta(
    "Crea una nota de texto con un título y un contenido.",
    {
        "titulo": {"type": "string"},
        "contenido": {"type": "string"},
    },
    ["titulo", "contenido"],
)
def crear_nota(titulo, contenido):
    carpeta = config.CARPETA_DATOS / "notas"
    carpeta.mkdir(parents=True, exist_ok=True)
    seguro = "".join(c for c in titulo if c.isalnum() or c in " -_").strip() or "nota"
    ruta = carpeta / f"{seguro}.txt"
    ruta.write_text(contenido, encoding="utf-8")
    return f"Nota guardada en {ruta}"


@herramienta(
    "Pone un recordatorio que avisará en voz alta dentro de N minutos. Jarvis debe permanecer abierto.",
    {
        "minutos": {"type": "number", "description": "Minutos hasta el aviso"},
        "mensaje": {"type": "string", "description": "Qué recordar"},
    },
    ["minutos", "mensaje"],
)
def poner_recordatorio(minutos, mensaje):
    minutos = float(minutos)
    if not math.isfinite(minutos) or minutos <= 0:
        raise ValueError("El tiempo del recordatorio debe ser mayor que cero.")
    t = threading.Timer(minutos * 60, lambda: NOTIFICAR(f"Recordatorio: {mensaje}"))
    t.daemon = True
    t.start()
    return f"Listo, te aviso en {minutos:g} minutos."


@herramienta(
    "Programa una alerta para la próxima vez que ocurra la hora indicada (formato 24 h HH:MM). Jarvis debe permanecer abierto.",
    {
        "hora": {"type": "string", "description": "Hora local, por ejemplo 18:30"},
        "mensaje": {"type": "string", "description": "Qué debe avisar"},
    },
    ["hora", "mensaje"],
)
def crear_alerta(hora, mensaje):
    ahora = datetime.datetime.now()
    objetivo = datetime.datetime.strptime(hora, "%H:%M").replace(
        year=ahora.year, month=ahora.month, day=ahora.day
    )
    if objetivo <= ahora:
        objetivo += datetime.timedelta(days=1)
    segundos = (objetivo - ahora).total_seconds()
    t = threading.Timer(segundos, lambda: NOTIFICAR(f"Alerta: {mensaje}"))
    t.daemon = True
    t.start()
    dia = "hoy" if objetivo.date() == ahora.date() else "mañana"
    return f"Alerta programada para {dia} a las {objetivo:%H:%M}."


@herramienta(
    "Guarda un dato sobre el usuario en la memoria permanente (gustos, nombres, preferencias).",
    {"dato": {"type": "string", "description": "El dato a recordar"}},
    ["dato"],
)
def guardar_en_memoria(dato):
    config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
    datos = cargar_memoria()
    datos.append(dato)
    _MEM.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return "Anotado."


@herramienta("Bloquea la sesión de Windows.", confirmar=True)
def bloquear_pantalla():
    ctypes.windll.user32.LockWorkStation()
    return "Pantalla bloqueada."


@herramienta(
    "Ejecuta un comando de consola de Windows y devuelve su salida. Úsalo solo si ninguna otra herramienta sirve.",
    {"comando": {"type": "string", "description": "Comando de cmd/PowerShell"}},
    ["comando"],
    confirmar=True,
)
def ejecutar_comando(comando):
    r = subprocess.run(comando, shell=True, capture_output=True, text=True, timeout=30, errors="replace")
    salida = (r.stdout + r.stderr).strip()
    return salida[:2000] or f"(sin salida, código {r.returncode})"