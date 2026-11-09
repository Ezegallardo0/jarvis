"""Herramientas que Jarvis puede usar sobre tu PC (Windows)."""
import ctypes
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import threading
import tempfile
import time
import webbrowser
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from urllib.parse import quote, quote_plus, urlsplit

import httpx
import psutil

import config

HERRAMIENTAS = {}
_REGISTRO_LOCK = threading.Lock()
_EVENTO_DETENCION = threading.Event()
_EXTENSIONES_TEXTO = {
    ".csv", ".css", ".html", ".js", ".json", ".md", ".py", ".txt", ".xml"
}
_MAX_TEXTO_ARCHIVO = 500_000


def NOTIFICAR(texto):  # main.py lo reemplaza para hablar en voz alta
    print(texto)


def CONFIRMAR(texto):  # main.py lo reemplaza (voz o teclado)
    return input(f"{texto} [s/n]: ").strip().lower().startswith(("s", "y"))


class AutomatizacionDetenida(Exception):
    """Señala una cancelación cooperativa solicitada desde el HUD."""


def solicitar_detencion():
    _EVENTO_DETENCION.set()


def reiniciar_detencion():
    _EVENTO_DETENCION.clear()


def detencion_solicitada():
    return _EVENTO_DETENCION.is_set()


def _verificar_detencion():
    if detencion_solicitada():
        raise AutomatizacionDetenida("Detención solicitada por el usuario.")


def herramienta(
    descripcion,
    parametros=None,
    requeridos=None,
    confirmar=False,
    requiere_pantalla=False,
    texto_confirmacion=None,
):
    """Registra una función como herramienta que el modelo puede invocar."""

    def deco(fn):
        HERRAMIENTAS[fn.__name__] = {
            "fn": fn,
            "confirmar": confirmar,
            "texto_confirmacion": texto_confirmacion,
            "requiere_pantalla": requiere_pantalla,
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
    return [
        h["schema"]
        for h in HERRAMIENTAS.values()
        if not h["requiere_pantalla"] or config.COMPARTIENDO_PANTALLA
    ]


def ejecutar(nombre, args):
    h = HERRAMIENTAS.get(nombre)
    if not h:
        return f"Herramienta desconocida: {nombre}"
    if h["requiere_pantalla"] and not config.COMPARTIENDO_PANTALLA:
        return "El acceso a la pantalla está desactivado. Pedile al usuario que lo habilite en Jarvis."
    if detencion_solicitada():
        return "Acción cancelada antes de empezar por solicitud del usuario."
    args = args or {}
    try:
        registrar_accion(nombre, "iniciada")
    except OSError as error:
        return f"No ejecuté {nombre}: no pude guardar el registro de acciones ({error})."
    try:
        _verificar_detencion()
        if h["confirmar"]:
            texto = h["texto_confirmacion"]
            if callable(texto):
                pregunta = texto(**args)
            elif texto:
                pregunta = texto.format(**args)
            else:
                pregunta = f"Voy a ejecutar {nombre} con {json.dumps(args, ensure_ascii=False)}. ¿Confirmás?"
            if not CONFIRMAR(pregunta):
                registrar_accion(nombre, "cancelada")
                return "El usuario canceló la acción."
        _verificar_detencion()
        resultado = str(h["fn"](**args))
    except AutomatizacionDetenida:
        try:
            registrar_accion(nombre, "cancelada")
        except OSError as error:
            return f"Acción cancelada; no pude completar el registro ({error})."
        return "Acción cancelada por solicitud del usuario."
    except Exception as e:  # el modelo ve el error y puede explicarlo
        try:
            registrar_accion(nombre, "error")
        except OSError as error_registro:
            return (
                f"Error al ejecutar {nombre}: {e}. Además, no pude completar "
                f"el registro de acciones ({error_registro})."
            )
        return f"Error al ejecutar {nombre}: {e}"
    try:
        registrar_accion(nombre, "completada")
    except OSError as error:
        return (
            f"{resultado} La acción terminó, pero no pude completar su registro "
            f"({error})."
        )
    return resultado


def _ruta_segura(ruta):
    p = Path(ruta).expanduser().resolve()
    for base in config.CARPETAS_PERMITIDAS:
        try:
            p.relative_to(base.resolve())
            return p
        except ValueError:
            continue
    raise PermissionError(f"Ruta fuera de las carpetas permitidas: {p}")


def _ruta_usuario(ruta):
    texto = str(ruta).strip()
    if not texto:
        raise ValueError("Indicá una ruta.")
    original = Path(texto).expanduser()
    if not original.is_absolute():
        original = Path.home() / original
    if original.is_symlink():
        raise PermissionError("No se permiten operaciones sobre enlaces simbólicos.")
    return _ruta_segura(original)


def _validar_archivo_texto(ruta):
    if ruta.suffix.lower() not in _EXTENSIONES_TEXTO:
        extensiones = ", ".join(sorted(_EXTENSIONES_TEXTO))
        raise ValueError(f"Solo se admiten archivos de texto: {extensiones}.")
    if not ruta.is_file():
        raise ValueError("La ruta no es un archivo regular.")
    if ruta.stat().st_size > _MAX_TEXTO_ARCHIVO:
        raise ValueError("El archivo supera el límite de 500.000 bytes.")


def _validar_contenido_texto(contenido):
    if len(contenido.encode("utf-8")) > _MAX_TEXTO_ARCHIVO:
        raise ValueError("El contenido supera el límite de 500.000 bytes en UTF-8.")


def _confirmar_cambio(accion, ruta, destino=""):
    sufijo = f" hacia {destino}" if destino else ""
    return (
        f"Voy a {accion} «{ruta}»{sufijo}. "
        "Esta operación modifica archivos. ¿Confirmás?"
    )


def _confirmar_eliminacion(ruta):
    return (
        f"Voy a eliminar permanentemente «{ruta}». "
        "No se puede deshacer. ¿Confirmás explícitamente?"
    )


def registrar_accion(nombre, estado):
    ruta = config.CARPETA_DATOS / "registro_acciones.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.is_symlink():
        raise OSError("El archivo de registro no puede ser un enlace simbólico.")
    entrada = {
        "fecha": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "herramienta": nombre,
        "estado": estado,
    }
    with _REGISTRO_LOCK, ruta.open("a", encoding="utf-8") as archivo:
        archivo.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _cargar_api_windows():
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError as error:
        raise RuntimeError("Falta pywin32; reinstalá las dependencias de Jarvis.") from error
    return win32api, win32con, win32gui


def _mover_cursor(x, y, win32api):
    win32api.SetCursorPos((x, y))
    if tuple(win32api.GetCursorPos()) != (x, y):
        raise OSError("Windows no colocó el cursor en las coordenadas solicitadas.")


def _buscar_ventana(titulo):
    _, _, win32gui = _cargar_api_windows()
    consulta = str(titulo).strip().casefold()
    if not consulta:
        raise ValueError("Indicá el título de la ventana.")
    coincidencias = []

    def agregar(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            nombre = win32gui.GetWindowText(hwnd).strip()
            if nombre:
                coincidencias.append((hwnd, nombre))
        return True

    win32gui.EnumWindows(agregar, None)
    exactas = [(hwnd, nombre) for hwnd, nombre in coincidencias if nombre.casefold() == consulta]
    candidatas = exactas or [
        (hwnd, nombre)
        for hwnd, nombre in coincidencias
        if consulta in nombre.casefold()
    ]
    if not candidatas:
        raise LookupError(f"No encontré una ventana visible que coincida con «{titulo}».")
    if len(candidatas) > 1:
        opciones = "; ".join(nombre for _, nombre in candidatas[:10])
        raise ValueError(f"El título coincide con varias ventanas: {opciones}. Indicá uno más preciso.")
    return candidatas[0]


def _activar_ventana(hwnd, win32con, win32gui):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    if win32gui.GetForegroundWindow() != hwnd:
        raise OSError("Windows no permitió activar la ventana solicitada.")


def _validar_coordenadas(x, y, win32api):
    if isinstance(x, bool) or not isinstance(x, int) or isinstance(y, bool) or not isinstance(y, int):
        raise ValueError("Las coordenadas deben ser números enteros.")
    izquierda = win32api.GetSystemMetrics(76)
    arriba = win32api.GetSystemMetrics(77)
    ancho = win32api.GetSystemMetrics(78)
    alto = win32api.GetSystemMetrics(79)
    if ancho <= 0 or alto <= 0 or not izquierda <= x < izquierda + ancho or not arriba <= y < arriba + alto:
        raise ValueError("Las coordenadas quedan fuera del escritorio visible.")


def _confirmar_clic(x, y, accion):
    return f"Voy a {accion} en la posición ({x}, {y}) de la pantalla. ¿Confirmás?"


def _confirmar_escritura(ventana, texto):
    return (
        f"Voy a escribir este texto en la ventana «{ventana}»:\n"
        f"{texto}\n¿Confirmás?"
    )


_ATAJOS_PERMITIDOS = {
    "ctrl+a": (0x11, 0x41),
    "ctrl+c": (0x11, 0x43),
    "ctrl+f": (0x11, 0x46),
    "ctrl+o": (0x11, 0x4F),
    "ctrl+p": (0x11, 0x50),
    "ctrl+s": (0x11, 0x53),
    "ctrl+v": (0x11, 0x56),
    "ctrl+x": (0x11, 0x58),
    "ctrl+y": (0x11, 0x59),
    "ctrl+z": (0x11, 0x5A),
    "ctrl+shift+s": (0x11, 0x10, 0x53),
    "ctrl+tab": (0x11, 0x09),
}
_TECLAS_PERMITIDAS = {
    "enter": 0x0D, "tab": 0x09, "escape": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "space": 0x20,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}
_COMANDOS_CONSULTA = {
    "hostname": "hostname.exe",
    "tasklist": "tasklist.exe",
    "whoami": "whoami.exe",
}


def _confirmacion_comando(comando):
    normalizado = str(comando).strip().casefold()
    if normalizado not in _COMANDOS_CONSULTA:
        raise ValueError(
            "Solo se permiten consultas exactas sin argumentos: "
            + ", ".join(sorted(_COMANDOS_CONSULTA))
            + ". Para otras tareas, usá las herramientas específicas de Jarvis."
        )
    return f"Voy a ejecutar la consulta permitida «{normalizado}». ¿Confirmás?"


def _confirmacion_clic(x, y, tipo):
    acciones = {
        "izquierdo": "hacer clic",
        "derecho": "hacer clic derecho",
        "doble": "hacer doble clic",
    }
    if tipo not in acciones:
        raise ValueError("Tipo de clic no permitido.")
    api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x, y, api)
    return _confirmar_clic(x, y, acciones[tipo])


def _confirmacion_arrastre(x_inicio, y_inicio, x_fin, y_fin):
    api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x_inicio, y_inicio, api)
    _validar_coordenadas(x_fin, y_fin, api)
    return (
        f"Voy a arrastrar desde ({x_inicio}, {y_inicio}) hasta "
        f"({x_fin}, {y_fin}). ¿Confirmás?"
    )


def _confirmacion_desplazamiento(x, y, pasos):
    if isinstance(pasos, bool) or not isinstance(pasos, int) or not -20 <= pasos <= 20 or pasos == 0:
        raise ValueError("Los pasos deben ser un entero entre -20 y 20, distinto de cero.")
    api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x, y, api)
    direccion = "hacia arriba" if pasos > 0 else "hacia abajo"
    return (
        f"Voy a desplazar {abs(pasos)} pasos {direccion} en ({x}, {y}). "
        "¿Confirmás?"
    )


def _confirmacion_escritura(ventana, texto):
    texto = _normalizar_texto_escritorio(texto)
    return _confirmar_escritura(ventana, texto)


def _confirmacion_atajo(ventana, atajo):
    normalizado = str(atajo).lower().strip()
    if normalizado not in _ATAJOS_PERMITIDOS:
        raise ValueError("Ese atajo no está permitido.")
    return f"Voy a pulsar {normalizado} en la ventana «{ventana}». ¿Confirmás?"


def _confirmacion_tecla(ventana, tecla, repeticiones=1):
    if tecla not in _TECLAS_PERMITIDAS:
        raise ValueError("Esa tecla no está permitida.")
    if isinstance(repeticiones, bool) or not isinstance(repeticiones, int) or not 1 <= repeticiones <= 20:
        raise ValueError("Las repeticiones deben ser un entero entre 1 y 20.")
    return f"Voy a pulsar {tecla} {repeticiones} vez/veces en «{ventana}». ¿Confirmás?"


def _normalizar_texto_escritorio(texto):
    if not isinstance(texto, str):
        raise ValueError("El texto debe ser una cadena.")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    if len(texto) > 500:
        raise ValueError("El texto debe tener hasta 500 caracteres.")
    if any(ord(caracter) < 0x20 and caracter not in "\n\t" for caracter in texto):
        raise ValueError("El texto contiene caracteres de control no permitidos.")
    return texto


def _nombre_archivo_seguro(nombre, predeterminado):
    nombre = "".join(
        caracter if caracter.isalnum() or caracter in " -_." else "_"
        for caracter in nombre
    ).strip(" .")
    return nombre or predeterminado


def _escapar_texto_xml(texto):
    valido = (
        caracter
        for caracter in texto
        if ord(caracter) in (0x09, 0x0A, 0x0D)
        or 0x20 <= ord(caracter) <= 0xD7FF
        or 0xE000 <= ord(caracter) <= 0xFFFD
        or 0x10000 <= ord(caracter) <= 0x10FFFF
    )
    return escape("".join(valido))


def _abrir_archivo(ruta):
    os.startfile(str(ruta))


def _ruta_disponible(ruta):
    base = ruta
    indice = 2
    while ruta.exists() or ruta.is_symlink():
        ruta = base.with_name(f"{base.stem} ({indice}){base.suffix}")
        indice += 1
    return ruta


def _texto_docx(texto):
    lineas = texto.splitlines() or [""]
    return "".join(
        "<w:p><w:r><w:t xml:space=\"preserve\">"
        f"{_escapar_texto_xml(linea)}"
        "</w:t></w:r></w:p>"
        for linea in lineas
    )


def _guardar_docx(ruta, titulo, contenido):
    cuerpo = (
        "<w:p><w:r><w:rPr><w:b/><w:sz w:val=\"32\"/></w:rPr>"
        "<w:t xml:space=\"preserve\">"
        f"{_escapar_texto_xml(titulo)}"
        "</w:t></w:r></w:p>"
        f"{_texto_docx(contenido)}"
    )
    documento = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{cuerpo}"
        "<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" "
        "w:left=\"1440\" w:header=\"708\" w:footer=\"708\" w:gutter=\"0\"/>"
        "</w:sectPr></w:body></w:document>"
    )
    with zipfile.ZipFile(ruta, "w", compression=zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archivo.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archivo.writestr("word/document.xml", documento)


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
    "Abre sin pedir confirmación una aplicación de la PC solo cuando el usuario lo solicite explícitamente. "
    "El nombre debe ser exactamente uno de la lista permitida; no uses esta herramienta para otras acciones.",
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


def _buscar_github(endpoint, consulta):
    if not config.GITHUB_TOKEN:
        return "GitHub no está conectado. Configurá un token de solo lectura en Ajustes > Integraciones."
    respuesta = httpx.get(
        f"https://api.github.com/search/{endpoint}",
        params={"q": consulta.strip()[:250], "per_page": 5},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=httpx.Timeout(12.0, connect=5.0),
    )
    respuesta.raise_for_status()
    resultados = respuesta.json().get("items", [])
    if not resultados:
        return "GitHub no encontró resultados para esa búsqueda."
    if endpoint == "repositories":
        return "\n".join(
            f"{item['full_name']}: {item.get('description') or 'Sin descripción'}\n"
            f"URL: {item['html_url']}"
            for item in resultados
        )
    if endpoint == "code":
        return "\n".join(
            f"{item['repository']['full_name']} — {item['path']}\nURL: {item['html_url']}"
            for item in resultados
        )
    return "\n".join(
        f"{item.get('repository_url', '').rsplit('/repos/', 1)[-1]} — "
        f"{item['title']} ({item['state']})\nURL: {item['html_url']}"
        for item in resultados
    )


@herramienta(
    "Busca repositorios públicos o privados accesibles para el usuario conectado en GitHub. "
    "Usá una consulta de búsqueda de GitHub, por ejemplo 'user:octocat language:python'. Solo lectura.",
    {"consulta": {"type": "string", "description": "Consulta de búsqueda de repositorios"}},
    ["consulta"],
)
def buscar_repositorios_github(consulta):
    return _buscar_github("repositories", consulta)


@herramienta(
    "Busca issues y pull requests accesibles para el usuario conectado en GitHub. "
    "Usá una consulta de búsqueda de GitHub, por ejemplo 'repo:owner/repo is:issue is:open'. Solo lectura.",
    {"consulta": {"type": "string", "description": "Consulta de búsqueda de issues o pull requests"}},
    ["consulta"],
)
def buscar_issues_github(consulta):
    return _buscar_github("issues", consulta)


@herramienta(
    "Busca archivos y fragmentos de código en repositorios a los que el token tenga acceso. "
    "Ejemplo: 'repo:owner/repo nombre_funcion'. Solo lectura; GitHub requiere permiso Contents: Read-only.",
    {"consulta": {"type": "string", "description": "Consulta de código de GitHub"}},
    ["consulta"],
)
def buscar_codigo_github(consulta):
    return _buscar_github("code", consulta)


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


@herramienta(
    "Mueve el cursor a coordenadas absolutas del escritorio virtual de Windows. No hace clic.",
    {"x": {"type": "integer"}, "y": {"type": "integer"}},
    ["x", "y"],
)
def mover_cursor(x, y):
    _verificar_detencion()
    win32api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x, y, win32api)
    _mover_cursor(x, y, win32api)
    return f"Cursor movido a ({x}, {y})."


@herramienta(
    "Hace un clic izquierdo, derecho o doble clic en coordenadas del escritorio. Usala solo cuando el usuario indique el elemento y pide confirmación.",
    {
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "tipo": {"type": "string", "enum": ["izquierdo", "derecho", "doble"]},
    },
    ["x", "y", "tipo"],
    confirmar=True,
    texto_confirmacion=_confirmacion_clic,
)
def hacer_clic(x, y, tipo):
    _verificar_detencion()
    if tipo not in {"izquierdo", "derecho", "doble"}:
        raise ValueError("Tipo de clic no permitido.")
    win32api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x, y, win32api)
    _mover_cursor(x, y, win32api)
    abajo, arriba = (0x0008, 0x0010) if tipo == "derecho" else (0x0002, 0x0004)
    cantidad = 2 if tipo == "doble" else 1
    for indice in range(cantidad):
        _verificar_detencion()
        win32api.mouse_event(abajo, 0, 0, 0, 0)
        win32api.mouse_event(arriba, 0, 0, 0, 0)
        if indice + 1 < cantidad:
            time.sleep(0.06)
    return f"Clic {tipo} realizado en ({x}, {y})."


@herramienta(
    "Arrastra el botón izquierdo desde una coordenada a otra. Pide confirmación antes de mover elementos.",
    {
        "x_inicio": {"type": "integer"},
        "y_inicio": {"type": "integer"},
        "x_fin": {"type": "integer"},
        "y_fin": {"type": "integer"},
    },
    ["x_inicio", "y_inicio", "x_fin", "y_fin"],
    confirmar=True,
    texto_confirmacion=_confirmacion_arrastre,
)
def arrastrar_mouse(x_inicio, y_inicio, x_fin, y_fin):
    _verificar_detencion()
    win32api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x_inicio, y_inicio, win32api)
    _validar_coordenadas(x_fin, y_fin, win32api)
    _mover_cursor(x_inicio, y_inicio, win32api)
    win32api.mouse_event(0x0002, 0, 0, 0, 0)
    try:
        time.sleep(0.05)
        _verificar_detencion()
        _mover_cursor(x_fin, y_fin, win32api)
    finally:
        win32api.mouse_event(0x0004, 0, 0, 0, 0)
    return f"Arrastre realizado desde ({x_inicio}, {y_inicio}) hasta ({x_fin}, {y_fin})."


@herramienta(
    "Desplaza la página bajo las coordenadas indicadas. Pasos positivos suben y negativos bajan; el rango permitido es -20 a 20.",
    {
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "pasos": {"type": "integer", "description": "Cantidad y dirección de pasos de rueda"},
    },
    ["x", "y", "pasos"],
    confirmar=True,
    texto_confirmacion=_confirmacion_desplazamiento,
)
def desplazar_pagina(x, y, pasos):
    _verificar_detencion()
    if isinstance(pasos, bool) or not isinstance(pasos, int) or not -20 <= pasos <= 20 or pasos == 0:
        raise ValueError("Los pasos deben ser un entero entre -20 y 20, distinto de cero.")
    win32api, _, _ = _cargar_api_windows()
    _validar_coordenadas(x, y, win32api)
    _mover_cursor(x, y, win32api)
    delta = (120 * pasos) & 0xFFFFFFFF
    win32api.mouse_event(0x0800, 0, 0, delta, 0)
    return f"Página desplazada {pasos} paso(s)."


@herramienta(
    "Escribe hasta 500 caracteres en una ventana visible indicada por su título. Siempre muestra el texto y pide confirmación.",
    {
        "ventana": {"type": "string", "description": "Título exacto o único de la ventana destino"},
        "texto": {"type": "string", "description": "Texto a escribir; se permiten saltos de línea y tabulaciones"},
    },
    ["ventana", "texto"],
    confirmar=True,
    texto_confirmacion=_confirmacion_escritura,
)
def escribir_texto(ventana, texto):
    _verificar_detencion()
    texto = _normalizar_texto_escritorio(texto)
    win32api, win32con, win32gui = _cargar_api_windows()
    hwnd, titulo = _buscar_ventana(ventana)
    _activar_ventana(hwnd, win32con, win32gui)
    for caracter in texto:
        _verificar_detencion()
        if caracter == "\n":
            win32api.keybd_event(0x0D, 0, 0, 0)
            win32api.keybd_event(0x0D, 0, 0x0002, 0)
        elif caracter == "\t":
            win32api.keybd_event(0x09, 0, 0, 0)
            win32api.keybd_event(0x09, 0, 0x0002, 0)
        else:
            codificado = caracter.encode("utf-16-le")
            for indice in range(0, len(codificado), 2):
                unidad = int.from_bytes(codificado[indice:indice + 2], "little")
                win32api.keybd_event(0, unidad, 0x0004, 0)
                win32api.keybd_event(0, unidad, 0x0006, 0)
    return f"Texto escrito en «{titulo}»."


@herramienta(
    "Ejecuta solo atajos de teclado permitidos en una ventana visible indicada por su título; siempre pide confirmación.",
    {
        "ventana": {"type": "string", "description": "Título exacto o único de la ventana destino"},
        "atajo": {
            "type": "string",
            "enum": sorted(_ATAJOS_PERMITIDOS),
            "description": "Atajo permitido, por ejemplo ctrl+c, ctrl+v, ctrl+a, ctrl+s o ctrl+tab",
        },
    },
    ["ventana", "atajo"],
    confirmar=True,
    texto_confirmacion=_confirmacion_atajo,
)
def ejecutar_atajo(ventana, atajo):
    _verificar_detencion()
    teclas = _ATAJOS_PERMITIDOS.get(atajo.lower().strip())
    if not teclas:
        raise ValueError("Ese atajo no está permitido.")
    win32api, win32con, win32gui = _cargar_api_windows()
    hwnd, titulo = _buscar_ventana(ventana)
    _activar_ventana(hwnd, win32con, win32gui)
    pulsadas = []
    try:
        for tecla in teclas:
            _verificar_detencion()
            win32api.keybd_event(tecla, 0, 0, 0)
            pulsadas.append(tecla)
    finally:
        for tecla in reversed(pulsadas):
            win32api.keybd_event(tecla, 0, 0x0002, 0)
    return f"Atajo {atajo} enviado a «{titulo}»."


@herramienta(
    "Pulsa una tecla de navegación o edición en una ventana visible indicada por su título. Pide confirmación.",
    {
        "ventana": {"type": "string", "description": "Título exacto o único de la ventana destino"},
        "tecla": {
            "type": "string",
            "enum": list(_TECLAS_PERMITIDAS),
        },
        "repeticiones": {"type": "integer", "description": "Cantidad de pulsaciones, de 1 a 20"},
    },
    ["ventana", "tecla"],
    confirmar=True,
    texto_confirmacion=_confirmacion_tecla,
)
def presionar_tecla(ventana, tecla, repeticiones=1):
    _verificar_detencion()
    codigo = _TECLAS_PERMITIDAS.get(tecla)
    if codigo is None:
        raise ValueError("Esa tecla no está permitida.")
    if isinstance(repeticiones, bool) or not isinstance(repeticiones, int) or not 1 <= repeticiones <= 20:
        raise ValueError("Las repeticiones deben ser un entero entre 1 y 20.")
    win32api, win32con, win32gui = _cargar_api_windows()
    hwnd, titulo = _buscar_ventana(ventana)
    _activar_ventana(hwnd, win32con, win32gui)
    for _ in range(repeticiones):
        _verificar_detencion()
        try:
            win32api.keybd_event(codigo, 0, 0, 0)
        finally:
            win32api.keybd_event(codigo, 0, 0x0002, 0)
    return f"Tecla {tecla} pulsada en «{titulo}»."


@herramienta(
    "Activa, minimiza, maximiza o restaura una ventana visible cuyo título coincida de forma única.",
    {
        "titulo": {"type": "string", "description": "Título exacto o único de la ventana"},
        "accion": {"type": "string", "enum": ["activar", "minimizar", "maximizar", "restaurar"]},
    },
    ["titulo", "accion"],
)
def controlar_ventana(titulo, accion):
    if accion not in {"activar", "minimizar", "maximizar", "restaurar"}:
        raise ValueError("Acción de ventana no permitida.")
    _, win32con, win32gui = _cargar_api_windows()
    hwnd, nombre = _buscar_ventana(titulo)
    if accion == "activar":
        _activar_ventana(hwnd, win32con, win32gui)
    else:
        estados = {
            "minimizar": win32con.SW_MINIMIZE,
            "maximizar": win32con.SW_MAXIMIZE,
            "restaurar": win32con.SW_RESTORE,
        }
        win32gui.ShowWindow(hwnd, estados[accion])
    return f"Ventana «{nombre}»: {accion}."


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
    p = _ruta_usuario(ruta)
    _validar_archivo_texto(p)
    return p.read_text(encoding="utf-8", errors="replace")[:4000]


@herramienta(
    "Busca archivos y carpetas por nombre dentro de una carpeta permitida, sin abrirlos. Devuelve hasta 50 coincidencias.",
    {
        "consulta": {"type": "string", "description": "Parte del nombre a buscar"},
        "carpeta": {"type": "string", "description": "Carpeta donde buscar; por defecto la carpeta del usuario"},
    },
    ["consulta"],
)
def buscar_archivos(consulta, carpeta=""):
    consulta = consulta.strip().casefold()
    if not consulta:
        raise ValueError("Indicá un nombre o parte del nombre para buscar.")
    raiz = _ruta_usuario(carpeta or str(Path.home()))
    if not raiz.is_dir():
        raise ValueError("La ruta de búsqueda no es una carpeta.")

    resultados = []
    revisados = 0
    maximo_revisados = 20_000

    def error_busqueda(error):
        raise error

    for actual, directorios, archivos in os.walk(
        raiz, topdown=True, followlinks=False, onerror=error_busqueda
    ):
        _verificar_detencion()
        directorios[:] = [
            nombre
            for nombre in directorios
            if nombre.casefold()
            not in {".git", ".venv", "venv", "__pycache__", "node_modules", "appdata"}
            and not (Path(actual) / nombre).is_symlink()
        ]
        for nombre in sorted(directorios + archivos, key=str.casefold):
            _verificar_detencion()
            revisados += 1
            if consulta in nombre.casefold():
                resultados.append(str(Path(actual) / nombre))
                if len(resultados) == 50:
                    return "Encontré los primeros 50 resultados:\n" + "\n".join(resultados)
            if revisados >= maximo_revisados:
                parcial = "\n".join(resultados) if resultados else "No encontré coincidencias"
                return (
                    f"{parcial}\nBúsqueda parcial: se alcanzó el límite "
                    f"de {maximo_revisados} elementos."
                )
    return "\n".join(resultados) if resultados else "No encontré coincidencias."


@herramienta(
    "Crea una carpeta nueva dentro de las carpetas permitidas; no sobrescribe elementos existentes.",
    {"ruta": {"type": "string", "description": "Ruta de la carpeta nueva"}},
    ["ruta"],
)
def crear_carpeta(ruta):
    _verificar_detencion()
    destino = _ruta_usuario(ruta)
    if destino.exists():
        raise FileExistsError(f"Ya existe: {destino}")
    destino.mkdir(parents=True)
    return f"Carpeta creada: {destino}"


@herramienta(
    "Crea un archivo de texto nuevo sin sobrescribir. Se admiten .txt, .md, .csv, .json, .py, .html, .css, .js y .xml.",
    {
        "ruta": {"type": "string", "description": "Ruta completa o relativa a la carpeta del usuario"},
        "contenido": {"type": "string", "description": "Contenido del archivo (máx. 500.000 caracteres)"},
    },
    ["ruta", "contenido"],
)
def crear_archivo(ruta, contenido):
    _verificar_detencion()
    _validar_contenido_texto(contenido)
    destino = _ruta_usuario(ruta)
    if destino.suffix.lower() not in _EXTENSIONES_TEXTO:
        raise ValueError("La extensión del archivo no está permitida para escritura.")
    if not destino.parent.is_dir():
        raise FileNotFoundError("La carpeta de destino no existe; creala primero.")
    with destino.open("x", encoding="utf-8", newline="") as archivo:
        archivo.write(contenido)
    return f"Archivo creado: {destino}"


@herramienta(
    "Edita el contenido completo de un archivo de texto permitido. Solicita confirmación antes de reemplazarlo.",
    {
        "ruta": {"type": "string", "description": "Ruta del archivo de texto"},
        "contenido": {"type": "string", "description": "Contenido nuevo completo (máx. 500.000 caracteres)"},
    },
    ["ruta", "contenido"],
    confirmar=True,
    texto_confirmacion=lambda ruta, **_: _confirmar_cambio(
        "reemplazar el contenido de", ruta
    ),
)
def editar_archivo(ruta, contenido):
    _verificar_detencion()
    _validar_contenido_texto(contenido)
    destino = _ruta_usuario(ruta)
    _validar_archivo_texto(destino)
    temporal = None
    try:
        archivo_temporal = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{destino.name}.",
            suffix=".jarvis-tmp",
            dir=destino.parent,
            delete=False,
        )
        temporal = Path(archivo_temporal.name)
        with archivo_temporal as archivo:
            archivo.write(contenido)
        _verificar_detencion()
        temporal.replace(destino)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)
    return f"Archivo actualizado: {destino}"


@herramienta(
    "Renombra un archivo o carpeta dentro de la misma ubicación. Solicita confirmación antes de cambiarlo.",
    {
        "ruta": {"type": "string", "description": "Ruta del elemento que se renombra"},
        "nombre_nuevo": {"type": "string", "description": "Nuevo nombre, sin una ruta"},
    },
    ["ruta", "nombre_nuevo"],
    confirmar=True,
    texto_confirmacion=lambda ruta, nombre_nuevo: _confirmar_cambio(
        "renombrar", ruta, nombre_nuevo
    ),
)
def renombrar_elemento(ruta, nombre_nuevo):
    _verificar_detencion()
    origen = _ruta_usuario(ruta)
    if not origen.exists():
        raise FileNotFoundError(f"No existe: {origen}")
    nombre = Path(nombre_nuevo).name
    if nombre != nombre_nuevo.strip() or nombre in {"", ".", ".."}:
        raise ValueError("Indicá solo el nuevo nombre, sin carpetas.")
    nombre = _nombre_archivo_seguro(nombre, "")
    if not nombre:
        raise ValueError("El nuevo nombre no puede estar vacío.")
    if not Path(nombre).suffix and origen.is_file():
        nombre += origen.suffix
    destino = _ruta_segura(origen.with_name(nombre))
    if destino.exists():
        raise FileExistsError(f"Ya existe: {destino}")
    origen.rename(destino)
    return f"Elemento renombrado: {origen} → {destino}"


@herramienta(
    "Mueve un archivo o carpeta a una carpeta permitida existente. Solicita confirmación antes de moverlo.",
    {
        "ruta": {"type": "string", "description": "Ruta del elemento que se mueve"},
        "carpeta_destino": {"type": "string", "description": "Carpeta de destino existente"},
    },
    ["ruta", "carpeta_destino"],
    confirmar=True,
    texto_confirmacion=lambda ruta, carpeta_destino: _confirmar_cambio(
        "mover", ruta, carpeta_destino
    ),
)
def mover_elemento(ruta, carpeta_destino):
    _verificar_detencion()
    origen = _ruta_usuario(ruta)
    destino = _ruta_usuario(carpeta_destino)
    if not origen.exists():
        raise FileNotFoundError(f"No existe: {origen}")
    if not destino.is_dir():
        raise ValueError("La carpeta de destino no existe.")
    final = _ruta_segura(destino / origen.name)
    if final == origen:
        raise ValueError("El elemento ya está en esa carpeta.")
    if final.exists():
        raise FileExistsError(f"Ya existe un elemento con ese nombre: {final}")
    shutil.move(str(origen), str(final))
    return f"Elemento movido a: {final}"


@herramienta(
    "Elimina permanentemente un archivo o una carpeta vacía dentro de las carpetas permitidas. Siempre pide confirmación explícita.",
    {"ruta": {"type": "string", "description": "Ruta exacta del archivo o carpeta"}},
    ["ruta"],
    confirmar=True,
    texto_confirmacion=lambda ruta: _confirmar_eliminacion(ruta),
)
def eliminar_elemento(ruta):
    _verificar_detencion()
    destino = _ruta_usuario(ruta)
    if not destino.exists():
        raise FileNotFoundError(f"No existe: {destino}")
    if destino.is_dir():
        destino.rmdir()
    else:
        destino.unlink()
    return f"Elemento eliminado: {destino}"


@herramienta(
    "Muestra las últimas acciones de Jarvis sin incluir el contenido de archivos ni argumentos privados.",
    {"limite": {"type": "integer", "description": "Cantidad de registros a mostrar (1 a 100)"}},
)
def consultar_registro_acciones(limite=20):
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= 100:
        raise ValueError("El límite debe ser un entero entre 1 y 100.")
    ruta = config.CARPETA_DATOS / "registro_acciones.jsonl"
    if not ruta.is_file():
        return "Todavía no hay acciones registradas."
    lineas = ruta.read_text(encoding="utf-8").splitlines()[-limite:]
    return "\n".join(
        f"{entrada['fecha']} · {entrada['herramienta']} · {entrada['estado']}"
        for entrada in (json.loads(linea) for linea in lineas)
    ) or "Todavía no hay acciones registradas."


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
    seguro = _nombre_archivo_seguro(titulo, "nota")
    ruta = carpeta / f"{seguro}.txt"
    if ruta.exists():
        ruta = carpeta / f"{seguro}_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
    ruta.write_text(contenido, encoding="utf-8")
    return f"Nota guardada en {ruta}"


@herramienta(
    "Crea un documento de Microsoft Word .docx válido con el título y el contenido indicados. "
    "Por defecto lo guarda en la carpeta documentos de Jarvis y lo abre con la aplicación asociada. "
    "La ruta opcional debe estar dentro de las carpetas permitidas.",
    {
        "titulo": {"type": "string", "description": "Título y nombre sugerido del documento"},
        "contenido": {"type": "string", "description": "Texto completo; cada salto de línea será un párrafo"},
        "ruta": {"type": "string", "description": "Ruta .docx opcional dentro de la carpeta del usuario"},
    },
    ["titulo", "contenido"],
)
def crear_documento_word(titulo, contenido, ruta=""):
    if len(contenido) > 50000:
        raise ValueError("El documento supera el límite de 50.000 caracteres.")
    if ruta.strip():
        destino = Path(ruta).expanduser()
        if not destino.is_absolute():
            destino = Path.home() / destino
        destino = _ruta_segura(destino)
    else:
        carpeta = _ruta_segura(config.CARPETA_DATOS / "documentos")
        nombre = _nombre_archivo_seguro(titulo, "documento")
        destino = carpeta / f"{nombre}.docx"
    if destino.suffix.lower() != ".docx":
        destino = destino.with_suffix(".docx")
    destino = _ruta_segura(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        return f"Ya existe {destino}. No lo sobrescribí."
    _guardar_docx(destino, titulo.strip() or "Documento", contenido)
    try:
        _abrir_archivo(destino)
    except OSError as error:
        return f"Documento Word creado en {destino}, pero Windows no pudo abrirlo: {error}"
    return f"Documento Word creado y abierto: {destino}"


_ELEMENTO_DIBUJO_SCHEMA = {
    "type": "object",
    "properties": {
        "tipo": {
            "type": "string",
            "enum": ["linea", "rectangulo", "elipse", "poligono", "texto"],
        },
        "x1": {"type": "number"},
        "y1": {"type": "number"},
        "x2": {"type": "number"},
        "y2": {"type": "number"},
        "puntos": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "number"}},
        },
        "color": {"type": "string", "description": "Color de borde o trazo, por ejemplo #20C8FF"},
        "relleno": {"type": "string", "description": "Color interior; vacío para transparente"},
        "grosor": {"type": "integer"},
        "texto": {"type": "string"},
        "tamano_texto": {"type": "integer"},
    },
    "required": ["tipo"],
}


def _color_dibujo(nombre, predeterminado):
    from PIL import ImageColor

    color = nombre or predeterminado
    try:
        return ImageColor.getrgb(color) if color else None
    except ValueError as error:
        raise ValueError(f"Color no válido: {color}") from error


def _coordenada(valor, limite):
    numero = float(valor)
    if not math.isfinite(numero) or not 0 <= numero <= limite:
        raise ValueError(f"Coordenada fuera del lienzo (0 a {limite}).")
    return round(numero)


@herramienta(
    "Dibuja una imagen usando formas y texto, la guarda como PNG en la carpeta dibujos de Jarvis "
    "y la abre en Paint. Las coordenadas de cada elemento se expresan en píxeles desde la esquina superior izquierda. "
    "Usá elementos tipo linea, rectangulo, elipse, poligono o texto.",
    {
        "titulo": {"type": "string", "description": "Nombre del archivo"},
        "elementos": {
            "type": "array",
            "items": _ELEMENTO_DIBUJO_SCHEMA,
            "description": "Formas en orden de dibujo. En polígonos, puntos es una lista de pares [x,y].",
        },
        "ancho": {"type": "integer", "description": "Ancho del lienzo en píxeles, máximo 2000"},
        "alto": {"type": "integer", "description": "Alto del lienzo en píxeles, máximo 2000"},
        "fondo": {"type": "string", "description": "Color de fondo, por ejemplo #07111C"},
    },
    ["titulo", "elementos"],
)
def dibujar_en_paint(titulo, elementos, ancho=1200, alto=800, fondo="#FFFFFF"):
    from PIL import Image, ImageDraw, ImageFont

    ancho, alto = int(ancho), int(alto)
    if not 64 <= ancho <= 2000 or not 64 <= alto <= 2000:
        raise ValueError("El lienzo debe medir entre 64 y 2000 píxeles por lado.")
    if not elementos or len(elementos) > 100:
        raise ValueError("Indicá entre 1 y 100 elementos para dibujar.")

    imagen = Image.new("RGB", (ancho, alto), _color_dibujo(fondo, "#FFFFFF"))
    dibujo = ImageDraw.Draw(imagen)
    for elemento in elementos:
        tipo = elemento.get("tipo")
        color = _color_dibujo(elemento.get("color"), "#18212B")
        relleno = _color_dibujo(elemento.get("relleno"), "")
        grosor = int(elemento.get("grosor", 3))
        if not 1 <= grosor <= 40:
            raise ValueError("El grosor de línea debe estar entre 1 y 40.")
        if tipo == "texto":
            texto = str(elemento.get("texto", ""))[:1000]
            tamano = int(elemento.get("tamano_texto", 32))
            if not 8 <= tamano <= 128:
                raise ValueError("El tamaño del texto debe estar entre 8 y 128.")
            fuentes = (
                Path(os.environ.get("WINDIR", r"C:\Windows"))
                / "Fonts"
                / "segoeui.ttf",
                Path(os.environ.get("WINDIR", r"C:\Windows"))
                / "Fonts"
                / "arial.ttf",
            )
            fuente = next((ImageFont.truetype(str(p), tamano) for p in fuentes if p.is_file()), None)
            if fuente is None:
                fuente = ImageFont.load_default()
            x = _coordenada(elemento.get("x1", 0), ancho)
            y = _coordenada(elemento.get("y1", 0), alto)
            dibujo.text((x, y), texto, fill=color, font=fuente)
        elif tipo == "poligono":
            puntos = elemento.get("puntos") or []
            if not 3 <= len(puntos) <= 100:
                raise ValueError("Un polígono debe tener entre 3 y 100 puntos.")
            coordenadas = [
                (
                    _coordenada(punto[0], ancho),
                    _coordenada(punto[1], alto),
                )
                for punto in puntos
                if len(punto) == 2
            ]
            if len(coordenadas) != len(puntos):
                raise ValueError("Cada punto del polígono debe tener coordenadas [x, y].")
            dibujo.polygon(coordenadas, fill=relleno, outline=color)
            dibujo.line(coordenadas + [coordenadas[0]], fill=color, width=grosor)
        elif tipo in ("linea", "rectangulo", "elipse"):
            coordenadas = (
                _coordenada(elemento.get("x1", 0), ancho),
                _coordenada(elemento.get("y1", 0), alto),
                _coordenada(elemento.get("x2", 0), ancho),
                _coordenada(elemento.get("y2", 0), alto),
            )
            if tipo == "linea":
                dibujo.line(coordenadas, fill=color, width=grosor)
            elif tipo == "rectangulo":
                dibujo.rectangle(coordenadas, fill=relleno, outline=color, width=grosor)
            else:
                dibujo.ellipse(coordenadas, fill=relleno, outline=color, width=grosor)
        else:
            raise ValueError(f"Tipo de elemento no válido: {tipo}")

    carpeta = _ruta_segura(config.CARPETA_DATOS / "dibujos")
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = _nombre_archivo_seguro(titulo, "dibujo")
    ruta = _ruta_segura(_ruta_disponible(carpeta / f"{nombre}.png"))
    imagen.save(ruta, format="PNG")

    paint = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "mspaint.exe"
    if paint.is_file():
        try:
            subprocess.Popen([str(paint), str(ruta)])
        except OSError as error:
            return f"Dibujo guardado en {ruta}, pero Paint no pudo abrirlo: {error}"
        return f"Dibujo guardado y abierto en Paint: {ruta}"
    try:
        _abrir_archivo(ruta)
    except OSError as error:
        return f"Dibujo guardado en {ruta}, pero Windows no pudo abrirlo: {error}"
    return f"Dibujo guardado y abierto con la aplicación predeterminada: {ruta}. Paint no está instalado."


def _validar_destinatarios(destinatarios):
    partes = [parte.strip() for parte in re.split(r"[,;]", destinatarios) if parte.strip()]
    if not partes or any(
        not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", parte)
        for parte in partes
    ):
        raise ValueError("Indicá una o más direcciones de correo válidas.")
    return "; ".join(partes)


def _datos_correo(destinatarios, asunto, cuerpo):
    if len(asunto) > 200 or len(cuerpo) > 5000:
        raise ValueError("El asunto debe tener hasta 200 caracteres y el mensaje hasta 5000.")
    destino = _validar_destinatarios(destinatarios)
    if len(destino) > 1000:
        raise ValueError("La lista de destinatarios es demasiado larga.")
    return destino


def _confirmacion_envio_correo(destinatarios, asunto, cuerpo):
    destino = _datos_correo(destinatarios, asunto, cuerpo)
    return (
        "Voy a enviar este correo desde Outlook.\n"
        f"Para: {destino}\nAsunto: {asunto}\n\n{cuerpo}\n\n"
        "¿Confirmás el envío?"
    )


@herramienta(
    "Redacta un correo y lo deja abierto para que el usuario lo revise y envíe. "
    "Usa la aplicación de correo predeterminada; no envía el mensaje.",
    {
        "destinatarios": {"type": "string", "description": "Una o más direcciones, separadas por coma"},
        "asunto": {"type": "string"},
        "cuerpo": {"type": "string"},
    },
    ["destinatarios", "asunto", "cuerpo"],
)
def redactar_correo(destinatarios, asunto, cuerpo):
    destino = _datos_correo(destinatarios, asunto, cuerpo)
    try:
        import win32com.client
        import pywintypes
    except ImportError:
        win32com = None
    else:
        win32com = win32com.client

    if win32com:
        try:
            correo = win32com.Dispatch("Outlook.Application").CreateItem(0)
        except pywintypes.com_error:
            correo = None
        if correo is not None:
            correo.To = destino
            correo.Subject = asunto
            correo.Body = cuerpo
            correo.Display()
            return f"Dejé el correo abierto para revisar en Outlook: {destino}"

    if len(cuerpo) > 1500:
        raise RuntimeError(
            "La aplicación de correo no permite un borrador tan largo mediante mailto. "
            "Configurá Outlook clásico para abrir el borrador completo."
        )
    campos = quote_plus(asunto)
    cuerpo_codificado = quote_plus(cuerpo)
    para = quote(destino.replace("; ", ","), safe="@,")
    enlace = f"mailto:{para}?subject={campos}&body={cuerpo_codificado}"
    if not webbrowser.open(enlace):
        raise OSError("No pude abrir Outlook ni la aplicación de correo predeterminada.")
    return f"Dejé un borrador abierto para revisar: {destino}"


@herramienta(
    "Envía un correo con Outlook clásico. Usala solo cuando el usuario haya pedido enviarlo; "
    "siempre se muestra una confirmación con destinatario, asunto y cuerpo antes de enviar.",
    {
        "destinatarios": {"type": "string", "description": "Una o más direcciones, separadas por coma"},
        "asunto": {"type": "string"},
        "cuerpo": {"type": "string"},
    },
    ["destinatarios", "asunto", "cuerpo"],
    confirmar=True,
    texto_confirmacion=_confirmacion_envio_correo,
)
def enviar_correo(destinatarios, asunto, cuerpo):
    destino = _datos_correo(destinatarios, asunto, cuerpo)
    try:
        import win32com.client
    except ImportError as error:
        raise RuntimeError(
            "Para enviar correos instalá pywin32 y configurá una cuenta en Outlook clásico. "
            "También puedo dejarte el borrador abierto en la aplicación predeterminada."
        ) from error
    correo = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
    correo.To = destino
    correo.Subject = asunto
    correo.Body = cuerpo
    correo.Send()
    return f"Correo enviado desde Outlook a {destino}."


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
    "Ejecuta una consulta de diagnóstico de Windows de una lista fija. No admite argumentos, shell ni scripts.",
    {
        "comando": {
            "type": "string",
            "enum": sorted(_COMANDOS_CONSULTA),
            "description": "Consulta exacta permitida, sin argumentos",
        }
    },
    ["comando"],
    confirmar=True,
    texto_confirmacion=_confirmacion_comando,
)
def ejecutar_comando(comando):
    nombre = str(comando).strip().casefold()
    ejecutable = _COMANDOS_CONSULTA.get(nombre)
    if not ejecutable:
        raise ValueError("La consulta no está permitida.")
    raiz_windows = os.environ.get("SystemRoot")
    if not raiz_windows:
        raise RuntimeError("Windows no informó la ubicación del sistema.")
    ruta_ejecutable = Path(raiz_windows) / "System32" / ejecutable
    if not ruta_ejecutable.is_file():
        raise FileNotFoundError(f"No encontré la utilidad de Windows {ejecutable}.")
    r = subprocess.run(
        [str(ruta_ejecutable)],
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
        errors="replace",
    )
    salida = (r.stdout + r.stderr).strip()
    return salida[:2000] or f"(sin salida, código {r.returncode})"