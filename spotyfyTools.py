"""Spotify: control de reproducción por voz y datos en vivo para el HUD.

Requiere cuenta Premium para controlar la reproducción (limitación de Spotify).
Usa el flujo PKCE: solo hace falta el Client ID, sin secret.
"""
import subprocess
import threading
import time

import config
from tools import herramienta

PUBLICAR = lambda tipo, **datos: None  # main.py lo reemplaza para alimentar el HUD

SCOPE = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
_CACHE = config.CARPETA_DATOS / "spotify_token.json"
_sp = None
_lock = threading.Lock()
_poller_activo = False

AYUDA_CONFIG = (
    "Spotify no está configurado todavía. Creá una app en developer.spotify.com/dashboard, "
    "copiá su Client ID y guardalo desde Jarvis > Integraciones."
)


def configurado():
    return bool(config.SPOTIFY_CLIENT_ID.strip())


def _cliente():
    global _sp, _poller_activo
    if not configurado():
        raise RuntimeError(AYUDA_CONFIG)
    with _lock:
        if _sp is None:
            import spotipy
            from spotipy.oauth2 import SpotifyPKCE

            config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
            auth = SpotifyPKCE(
                client_id=config.SPOTIFY_CLIENT_ID.strip(),
                redirect_uri=config.SPOTIFY_REDIRECT_URI,
                scope=SCOPE,
                cache_path=str(_CACHE),
                open_browser=True,
            )
            _sp = spotipy.Spotify(auth_manager=auth, requests_timeout=10, retries=1)
        if not _poller_activo:
            _poller_activo = True
            threading.Thread(target=_bucle_estado, daemon=True).start()
    return _sp


def iniciar_si_hay_sesion():
    """Al arrancar: si ya te conectaste antes, reconecta solo y alimenta el HUD."""
    if configurado() and _CACHE.exists():
        threading.Thread(target=lambda: _intentar(_cliente), daemon=True).start()


def _intentar(fn):
    try:
        fn()
    except Exception as e:
        print(f"(Spotify: {e})")


def _resumen(pb):
    base = {"conectado": True, "reproduciendo": False, "titulo": "", "artista": "",
            "imagen": "", "progreso": 0, "duracion": 0}
    item = (pb or {}).get("item")
    if not item:
        return base
    imgs = (item.get("album") or {}).get("images") or item.get("images") or []
    artistas = item.get("artists") or []
    base.update(
        reproduciendo=bool(pb.get("is_playing")),
        titulo=item.get("name", ""),
        artista=", ".join(a.get("name", "") for a in artistas) or (item.get("show") or {}).get("name", ""),
        imagen=imgs[0]["url"] if imgs else "",
        progreso=pb.get("progress_ms") or 0,
        duracion=item.get("duration_ms") or 0,
    )
    return base


def _bucle_estado():
    while True:
        espera = 3
        try:
            PUBLICAR("spotify", **_resumen(_sp.current_playback()))
        except Exception:
            espera = 10
        time.sleep(espera)


def _traducir(e):
    """Convierte errores de Spotify en mensajes entendibles."""
    texto = str(e)
    if getattr(e, "http_status", None) == 403 or "PREMIUM" in texto.upper():
        return RuntimeError("Spotify solo deja controlar la reproducción desde otras apps con cuenta Premium.")
    return e


def _con_dispositivo(sp, accion):
    """Ejecuta accion(device_id=None); si no hay dispositivo activo, abre/usa uno."""
    import spotipy

    try:
        return accion(None)
    except spotipy.SpotifyException as e:
        if e.http_status == 404 or "NO_ACTIVE_DEVICE" in str(e):
            dispositivos = sp.devices().get("devices", [])
            if not dispositivos:
                subprocess.Popen('start "" spotify:', shell=True)  # abre la app de Spotify
                time.sleep(7)
                dispositivos = sp.devices().get("devices", [])
            if not dispositivos:
                raise RuntimeError("No hay ningún dispositivo de Spotify activo. Abrí Spotify en la PC y probá de nuevo.")
            return accion(dispositivos[0]["id"])
        raise _traducir(e)


def control(accion):
    """Control directo (lo usan los botones del HUD y la herramienta de voz)."""
    sp = _cliente()
    import spotipy

    try:
        if accion in ("pausar", "pausa"):
            sp.pause_playback()
        elif accion in ("continuar", "play"):
            _con_dispositivo(sp, lambda d: sp.start_playback(device_id=d))
        elif accion == "alternar":
            pb = sp.current_playback()
            if pb and pb.get("is_playing"):
                sp.pause_playback()
            else:
                _con_dispositivo(sp, lambda d: sp.start_playback(device_id=d))
        elif accion == "siguiente":
            sp.next_track()
        elif accion == "anterior":
            sp.previous_track()
        else:
            raise ValueError(f"Acción desconocida: {accion}")
    except spotipy.SpotifyException as e:
        raise _traducir(e)
    time.sleep(0.6)
    PUBLICAR("spotify", **_resumen(sp.current_playback()))


@herramienta(
    "Reproduce en Spotify una canción, artista, álbum o playlist, buscándola por nombre.",
    {
        "consulta": {"type": "string", "description": "Qué buscar, ej: 'Bohemian Rhapsody Queen'"},
        "tipo": {"type": "string", "enum": ["cancion", "artista", "album", "playlist"],
                 "description": "Tipo de resultado (por defecto cancion)"},
    },
    ["consulta"],
)
def spotify_reproducir(consulta, tipo="cancion"):
    sp = _cliente()
    t = {"cancion": "track", "artista": "artist", "album": "album", "playlist": "playlist"}.get(tipo, "track")
    items = [i for i in sp.search(q=consulta, type=t, limit=3)[t + "s"]["items"] if i]
    if not items:
        return f"No encontré '{consulta}' en Spotify."
    it = items[0]
    kw = {"uris": [it["uri"]]} if t == "track" else {"context_uri": it["uri"]}
    _con_dispositivo(sp, lambda d: sp.start_playback(device_id=d, **kw))
    time.sleep(0.8)
    PUBLICAR("spotify", **_resumen(sp.current_playback()))
    extra = ""
    if t == "track" and it.get("artists"):
        extra = " de " + it["artists"][0]["name"]
    return f"Reproduciendo {it['name']}{extra}."


@herramienta(
    "Controla Spotify: pausar, continuar, pasar a la siguiente canción o volver a la anterior.",
    {"accion": {"type": "string", "enum": ["pausar", "continuar", "siguiente", "anterior"]}},
    ["accion"],
)
def spotify_control(accion):
    control(accion)
    return f"Listo: {accion}."


@herramienta(
    "Cambia el volumen de Spotify (0 a 100).",
    {"porcentaje": {"type": "integer", "description": "Volumen de 0 a 100"}},
    ["porcentaje"],
)
def spotify_volumen(porcentaje):
    sp = _cliente()
    import spotipy

    try:
        sp.volume(max(0, min(100, int(porcentaje))))
    except spotipy.SpotifyException as e:
        raise _traducir(e)
    return f"Volumen de Spotify al {int(porcentaje)}%."


@herramienta("Dice qué canción está sonando ahora en Spotify.")
def spotify_que_suena():
    r = _resumen(_cliente().current_playback())
    if not r["titulo"]:
        return "No hay nada reproduciéndose en Spotify."
    estado = "Suena" if r["reproduciendo"] else "En pausa"
    return f"{estado}: {r['titulo']} de {r['artista']}."