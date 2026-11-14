"""Reconocimiento determinista de comandos locales antes de consultar una IA."""
from dataclasses import dataclass
import re
import unicodedata
from pathlib import Path

import tools


@dataclass(frozen=True)
class ResultadoLocal:
    intencion: str
    respuesta: str


def _normalizar(texto):
    sin_acentos = unicodedata.normalize("NFD", texto.casefold())
    return "".join(
        caracter
        for caracter in sin_acentos
        if unicodedata.category(caracter) != "Mn"
    )


def _ejecutar(intencion, herramienta, argumentos):
    try:
        respuesta = tools.ejecutar(herramienta, argumentos)
    except Exception as error:
        respuesta = f"No pude ejecutar {herramienta}: {error}"
    return ResultadoLocal(intencion, respuesta)


def _carpeta_conocida(nombre):
    opciones = {
        "descargas": ("Downloads", "Descargas"),
        "documentos": ("Documents", "Documentos"),
        "escritorio": ("Desktop", "Escritorio"),
        "proyectos": ("Projects", "Proyectos"),
    }
    for componente in opciones.get(nombre, ()):
        ruta = Path.home() / componente
        if ruta.is_dir():
            return str(ruta)
    return None


def _abrir_solicitado(normalizado, texto_original):
    sitios = {
        "google": "https://www.google.com/",
        "youtube": "https://www.youtube.com/",
        "youtube music": "https://music.youtube.com/",
        "twitch": "https://www.twitch.tv/",
        "spotify web": "https://open.spotify.com/",
    }
    if re.search(r"\b(?:abre|abrime|abrir|abri)\s+(?:el\s+)?navegador\b", normalizado):
        return _ejecutar(
            "OPEN_WEBSITE",
            "abrir_web",
            {"url": "https://www.google.com/"},
        )

    url = re.search(
        r"\b(?:abre|abrime|abrir|abri)\s+(?:(?:la|el)\s+)?"
        r"(?:pagina|sitio|web)\s+(https?://\S+)",
        normalizado,
    )
    if url:
        url_original = re.search(r"https?://\S+", texto_original, re.IGNORECASE)
        if url_original:
            return _ejecutar(
                "OPEN_WEBSITE",
                "abrir_web",
                {"url": url_original.group(0).rstrip(".,;:")},
            )

    apertura = re.match(
        r"\s*(?:jarvis[\s,.:;!?-]+)?"
        r"(?:(?:por favor|porfa)\s+)?"
        r"(?:(?:(?:me\s+)?(?:podes|podrias|puedes|quiero|necesito)\s+)(?:que\s+)?)?"
        r"(?:me\s+)?"
        r"(?:abrirme|abrir|abre|abrime|abreme|abri|abris|abras|abra|abran|"
        r"lanza|lanzar|inicia|iniciar)\s+"
        r"(?:(?:la|el|una|un)\s+)?"
        r"(?:(?:aplicacion|app|programa)(?:\s+de)?\s+)?",
        normalizado,
    )
    if not apertura:
        dominio = re.match(
            r"\s*(?:jarvis[\s,.:;!?-]+)?"
            r"(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?\s*$",
            normalizado,
        )
        if dominio:
            url_texto = texto_original.strip(" \t\r\n¿¡.!?,;:")
            return _ejecutar("OPEN_WEBSITE", "abrir_web", {"url": url_texto})
        return None

    nombre = normalizado[apertura.end():].strip(" \t\r\n.!?,;:")
    nombre = re.sub(r"[,;\s]+(?:por favor|porfa|ahora|ya|para mi)$", "", nombre)
    if not nombre:
        return ResultadoLocal(
            "OPEN_APPLICATION",
            "Decime el nombre de la aplicación o sitio que querés abrir.",
        )
    if re.search(r"\s+(?:y|luego|despues)\s+", nombre):
        return None
    nombre = tools.ALIAS_APPS.get(nombre, nombre)
    if nombre in sitios:
        return _ejecutar("OPEN_WEBSITE", "abrir_web", {"url": sitios[nombre]})
    aplicacion = tools.resolver_aplicacion(nombre)
    if not aplicacion:
        return ResultadoLocal(
            "OPEN_APPLICATION",
            f"No encontré una aplicación instalada que coincida con «{nombre}».",
        )
    return _ejecutar(
        "OPEN_APPLICATION",
        "abrir_aplicacion",
        {"nombre": aplicacion},
    )


def procesar_comando_local(texto):
    """Ejecuta solo órdenes locales explícitas que tienen parámetros inequívocos."""
    if not isinstance(texto, str) or not texto.strip():
        return None
    normalizado = _normalizar(texto.strip(" \t\r\n¿¡"))
    normalizado = re.sub(r"^jarvis[\s,.:;!?-]+", "", normalizado)

    coincide = re.search(
        r"\b(?:abre|abrime|abrir|abri)\b.{0,15}"
        r"\b(?:la\s+)?carpeta(?:\s+de)?\s+"
        r"(?P<nombre>descargas|documentos|escritorio|proyectos)\b",
        normalizado,
    )
    if coincide:
        nombre = coincide.group("nombre")
        ruta = _carpeta_conocida(nombre)
        if ruta:
            return _ejecutar(
                "OPEN_FOLDER",
                "abrir_carpeta",
                {"ruta": ruta},
            )
        return ResultadoLocal(
            "OPEN_FOLDER",
            f"No tengo registrada tu carpeta de {nombre}. Decime su ruta exacta para abrirla.",
        )

    abierto = _abrir_solicitado(normalizado, texto)
    if abierto is not None:
        return abierto

    cierre = re.match(
        r"\s*(?:por favor\s+)?(?:cerra|cerrar|cierra|cerrame)\s+"
        r"(?:(?:la|el)\s+)?(?:(?:aplicacion|programa|app)\s+)?"
        r"(?P<nombre>[\w .+-]+?)\s*[.!?]*$",
        normalizado,
    )
    if cierre:
        nombre = cierre.group("nombre").strip()
        aplicacion = tools.resolver_aplicacion(nombre)
        if not aplicacion:
            return ResultadoLocal(
                "CLOSE_APPLICATION",
                f"No encontré una aplicación permitida que coincida con «{nombre}».",
            )
        return _ejecutar(
            "CLOSE_APPLICATION",
            "cerrar_aplicacion",
            {"nombre": aplicacion},
        )

    coincide = re.search(
        r"\b(?:que hora es|decime la hora|decime la fecha|hora actual|"
        r"que dia es hoy|que fecha es hoy|fecha de hoy)\b",
        normalizado,
    )
    if coincide:
        return _ejecutar("GET_LOCAL_TIME", "hora_actual", {})

    consulta_sistema = re.search(
        r"\b(?:ram|memoria|cpu|procesador|bateria|disco|estado del sistema)\b",
        normalizado,
    )
    pregunta_sistema = re.search(
        r"\b(?:cuanto|cuanta|como|cual|decime|mostrame|consulta|informa|"
        r"revisa|que|uso|utiliza|utilizando|usando)\b",
        normalizado,
    )
    if consulta_sistema and pregunta_sistema:
        return _ejecutar("GET_SYSTEM_INFO", "info_sistema", {})

    coincide = re.search(
        r"\b(?:subi|subime|sube|aumenta|baja|bajame|reduce|silencia|"
        r"silencia)\b.{0,28}\bvolumen\b|\bvolumen\b.{0,20}\b"
        r"(?:subi|subime|sube|aumenta|baja|bajame|reduce|silencia)\b",
        normalizado,
    )
    if coincide:
        verbo = normalizado[coincide.start():coincide.end()]
        if re.search(r"\b(?:silencia)\b", verbo):
            accion = "silenciar"
        elif re.search(r"\b(?:baja|bajame|reduce)\b", verbo):
            accion = "bajar"
        else:
            accion = "subir"
        return _ejecutar(
            "CONTROL_VOLUME",
            "control_volumen",
            {"accion": accion},
        )

    coincide = re.search(
        r"\b(?:busca|buscame|buscar|encontra|encontrame|encontrar)\b"
        r".{0,50}\b(?:archivos?|documentos?)\b.{0,20}"
        r"\b(pdf|docx?|xlsx?|imagenes?|fotos?)\b",
        normalizado,
    )
    if coincide:
        extension = coincide.group(1).casefold()
        consulta = {
            "pdf": ".pdf",
            "doc": ".doc",
            "docx": ".docx",
            "xls": ".xls",
            "xlsx": ".xlsx",
            "imagen": ".png",
            "imagenes": ".png",
            "foto": ".jpg",
            "fotos": ".jpg",
        }.get(extension, f".{extension}")
        return _ejecutar(
            "SEARCH_FILES",
            "buscar_archivos",
            {"consulta": consulta},
        )

    coincide = re.search(
        r"\b(?:que|cuales|lista|listame|mostrame)\b.{0,40}"
        r"\b(?:aplicaciones|apps|programas)\b.{0,30}"
        r"\b(?:instaladas|instalados|tengo|hay)\b",
        normalizado,
    )
    if coincide:
        return _ejecutar(
            "LIST_INSTALLED_APPS",
            "listar_aplicaciones_instaladas",
            {},
        )

    coincide = re.search(
        r"\b(?:recordame|recorda me|recuerdame|recuerda me)\s+en\s+"
        r"(?P<minutos>\d{1,4})\s+minutos?\b(?:\s+(?P<mensaje>.+))?",
        texto,
        re.IGNORECASE,
    )
    if coincide:
        mensaje = (coincide.group("mensaje") or "").strip(" .,!¿?")
        if not mensaje:
            return ResultadoLocal(
                "CREATE_REMINDER",
                "¿Qué querés que te recuerde?",
            )
        return _ejecutar(
            "CREATE_REMINDER",
            "poner_recordatorio",
            {"minutos": int(coincide.group("minutos")), "mensaje": mensaje},
        )

    coincide = re.search(
        r"\b(?:recordame|recorda me|recuerdame|recuerda me)\s+a\s+"
        r"las\s+(?P<hora>\d{1,2}:\d{2})\b(?:\s+(?P<mensaje>.+))?",
        texto,
        re.IGNORECASE,
    )
    if coincide:
        mensaje = (coincide.group("mensaje") or "").strip(" .,!¿?")
        if not mensaje:
            return ResultadoLocal(
                "CREATE_REMINDER",
                "¿Qué querés que te recuerde a esa hora?",
            )
        return _ejecutar(
            "CREATE_REMINDER",
            "crear_alerta",
            {"hora": coincide.group("hora"), "mensaje": mensaje},
        )

    return None
