"""Herramientas que Jarvis puede usar sobre tu PC (Windows)."""
import ctypes
import datetime
import json
import os
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

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
    "Abre una página web en el navegador predeterminado.",
    {"url": {"type": "string", "description": "Dirección, ej: https://www.youtube.com"}},
    ["url"],
)
def abrir_web(url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Abrí {url}."


@herramienta(
    "Busca algo en Google en el navegador.",
    {"consulta": {"type": "string", "description": "Texto a buscar"}},
    ["consulta"],
)
def buscar_en_google(consulta):
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(consulta)}")
    return f"Busqué '{consulta}' en Google."


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
    "Pone un recordatorio que avisará en voz alta dentro de N minutos.",
    {
        "minutos": {"type": "number", "description": "Minutos hasta el aviso"},
        "mensaje": {"type": "string", "description": "Qué recordar"},
    },
    ["minutos", "mensaje"],
)
def poner_recordatorio(minutos, mensaje):
    minutos = float(minutos)
    t = threading.Timer(minutos * 60, lambda: NOTIFICAR(f"Recordatorio: {mensaje}"))
    t.daemon = True
    t.start()
    return f"Listo, te aviso en {minutos:g} minutos."


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