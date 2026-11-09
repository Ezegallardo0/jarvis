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
import webbrowser
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from urllib.parse import quote, quote_plus, urlsplit

import httpx
import psutil

import config

HERRAMIENTAS = {}


def NOTIFICAR(texto):  # main.py lo reemplaza para hablar en voz alta
    print(texto)


def CONFIRMAR(texto):  # main.py lo reemplaza (voz o teclado)
    return input(f"{texto} [s/n]: ").strip().lower().startswith(("s", "y"))


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
    args = args or {}
    try:
        if h["confirmar"]:
            texto = h["texto_confirmacion"]
            if callable(texto):
                pregunta = texto(**args)
            elif texto:
                pregunta = texto.format(**args)
            else:
                pregunta = f"Voy a ejecutar {nombre} con {json.dumps(args, ensure_ascii=False)}. ¿Confirmás?"
            if not CONFIRMAR(pregunta):
                return "El usuario canceló la acción."
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
    "Ejecuta un comando de consola de Windows y devuelve su salida. Úsalo solo si ninguna otra herramienta sirve.",
    {"comando": {"type": "string", "description": "Comando de cmd/PowerShell"}},
    ["comando"],
    confirmar=True,
)
def ejecutar_comando(comando):
    r = subprocess.run(comando, shell=True, capture_output=True, text=True, timeout=30, errors="replace")
    salida = (r.stdout + r.stderr).strip()
    return salida[:2000] or f"(sin salida, código {r.returncode})"