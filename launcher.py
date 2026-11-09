"""Controlador del HUD web y de los subsistemas de Jarvis."""
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    proyecto = Path(__file__).resolve().parent
    entorno = proyecto / "venv"
    if Path(sys.prefix).resolve() != entorno.resolve():
        ejecutable = entorno / "Scripts" / "pythonw.exe"
        if not ejecutable.is_file():
            ejecutable = entorno / "Scripts" / "python.exe"
        if not ejecutable.is_file():
            raise RuntimeError(
                f"No encontré el entorno virtual del proyecto en {entorno}. "
                "Crealo e instalá requirements.txt antes de iniciar Jarvis."
            )
        os.execv(
            str(ejecutable),
            [str(ejecutable), str(Path(__file__).resolve()), *sys.argv[1:]],
        )

import tkinter as tk
from tkinter import messagebox, ttk

import config
import sounddevice as sd
import ui
import voice

try:  # registra las herramientas de Spotify (la librería spotipy se carga recién al usarlas)
    import spotyfyTools
except Exception:  # pragma: no cover
    spotyfyTools = None

FONDO = "#07111C"
PANEL = "#0D1A28"
PANEL_CLARO = "#122337"
TEXTO = "#E8F2FA"
TEXTO_SUAVE = "#8DA4B8"
CIAN = "#47D7F5"


class _EntradasHud:
    """Conecta los mensajes del HUD con la cola del asistente."""

    def __init__(self, app):
        self.app = app

    def put(self, texto):
        self.app.desde_hud(texto)


class Lanzador:
    def __init__(self, raiz):
        self.raiz = raiz
        raiz.withdraw()
        raiz.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.cerrando = threading.Event()
        self.pedidos = queue.Queue()
        self._pedidos_lock = threading.Lock()
        self._pedido_en_curso = False
        self._respuesta_lista = threading.Event()
        self._respuesta_lista.set()
        self.bus = ui.Bus()
        self.hud_servidor = None
        self._hud_visto = False
        self._hud_ausente = None
        self._hud_vigilando = False
        self._hud_lanzado_en = None
        self.modo = None
        self.vision_descargando = False
        self.asistente_activo = False
        self.oido = None
        self._modo_lock = threading.Lock()
        self._voz_iniciando = False
        self._escucha_hilo = None
        (
            config.MICROFONO,
            config.AUDIO_SALIDA,
            config.PROVEEDOR_IA,
            config.PROVEEDOR_TTS,
            config.ELEVENLABS_VOICE_ID,
            config.UBICACION,
            config.SPOTIFY_CLIENT_ID,
            config.MODELO_VISION,
            config.MODELO,
            config.MODELO_GEMINI,
            config.MODELO_OPENAI,
            config.OLLAMA_HOST,
            config.WHISPER_MODELO,
            config.IDIOMA,
            config.VOLUMEN_VOZ,
            config.VELOCIDAD_VOZ,
            config.VOLUMEN_EFECTOS,
            config.TEMA_HUD,
        ) = self._leer_seleccion()
        try:
            config.GITHUB_TOKEN = self._clave_guardada_o_entorno("github")
        except Exception as error:
            messagebox.showerror(
                "Credenciales no disponibles",
                f"No pude leer las credenciales guardadas en Windows:\n{error}",
                parent=raiz,
            )
            config.GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
        config.COMPARTIENDO_PANTALLA = False
        if spotyfyTools:
            spotyfyTools.PUBLICAR = self.bus.publish

    def _crear_interfaz_anterior(self, raiz):
        """Interfaz nativa retirada; la configuración y el chat viven en el HUD."""
        self._configurar_estilos()
        self.shell = ttk.Frame(raiz, style="App.TFrame")
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.shell.columnconfigure(1, weight=1)
        self.shell.rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(self.shell, bg="#081725", width=210, padx=16, pady=22)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.page_host = ttk.Frame(self.shell, style="App.TFrame", padding=(24, 20))
        self.page_host.grid(row=0, column=1, sticky="nsew")
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)
        self.home_page = ttk.Frame(self.page_host, style="App.TFrame")
        self.settings_page = ttk.Frame(self.page_host, style="App.TFrame")
        self.connections_page = ttk.Frame(self.page_host, style="App.TFrame")
        self._crear_navegacion()

        self.settings_page.columnconfigure(0, weight=1)
        self.settings_page.rowconfigure(0, weight=1)
        self.settings_canvas = tk.Canvas(
            self.settings_page,
            bg=FONDO,
            highlightthickness=0,
        )
        self.settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scroll = ttk.Scrollbar(
            self.settings_page,
            orient="vertical",
            command=self.settings_canvas.yview,
        )
        settings_scroll.grid(row=0, column=1, sticky="ns")
        self.settings_canvas.configure(yscrollcommand=settings_scroll.set)
        principal = ttk.Frame(self.settings_canvas, padding=20, style="App.TFrame")
        self.principal = principal
        self._settings_window = self.settings_canvas.create_window(
            (0, 0), window=principal, anchor="nw"
        )
        principal.bind(
            "<Configure>",
            lambda _: self.settings_canvas.configure(
                scrollregion=self.settings_canvas.bbox("all")
            ),
        )
        self.settings_canvas.bind(
            "<Configure>",
            lambda evento: self.settings_canvas.itemconfigure(
                self._settings_window, width=evento.width
            ),
        )
        ttk.Label(
            principal,
            text=f"{config.NOMBRE.upper()}  /  ASISTENTE PERSONAL",
            style="Title.TLabel",
        ).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            principal,
            text="Prepará tu espacio. Cuando quieras, arrancamos.",
            style="Muted.TLabel",
        ).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 20)
        )

        entradas, salidas = self._dispositivos()
        self.entradas = {"Predeterminado de Windows": ""}
        self.salidas = {"Predeterminada de Windows": None}
        self.entradas.update(entradas)
        self.salidas.update(salidas)
        (
            microfono,
            salida,
            proveedor,
            proveedor_tts,
            elevenlabs_voice_id,
            ubicacion,
            spotify_client_id,
        ) = self._leer_seleccion()[:7]
        config.MICROFONO = microfono
        config.AUDIO_SALIDA = salida
        config.PROVEEDOR_IA = proveedor
        config.PROVEEDOR_TTS = proveedor_tts
        config.UBICACION = ubicacion
        config.ELEVENLABS_VOICE_ID = elevenlabs_voice_id
        config.SPOTIFY_CLIENT_ID = spotify_client_id
        self.spotify_client_id_var = tk.StringVar(value=spotify_client_id)
        self.ubicacion_var = tk.StringVar(value=ubicacion)
        self.entrada_var = tk.StringVar(value=self._etiqueta(self.entradas, microfono))
        self.salida_var = tk.StringVar(value=self._etiqueta(self.salidas, salida))
        self.proveedores = {
            "Ollama (local)": "ollama",
            "Gemini (rápido)": "gemini",
            "OpenAI / ChatGPT": "openai",
        }
        self.proveedor_var = tk.StringVar(
            value=next((label for label, code in self.proveedores.items() if code == proveedor), "Gemini (rápido)")
        )
        self._proveedor_modelo_visible = proveedor
        try:
            clave_modelo = self._clave_guardada_o_entorno(proveedor)
            clave_elevenlabs = self._clave_guardada_o_entorno("elevenlabs")
            github_token = self._clave_guardada_o_entorno("github")
        except Exception as error:
            messagebox.showerror(
                "No se pudieron leer las API keys",
                f"No pude leer las claves del Administrador de credenciales de Windows:\n{error}",
                parent=raiz,
            )
            clave_modelo = self._clave_entorno(proveedor)
            clave_elevenlabs = os.environ.get("ELEVENLABS_API_KEY", "")
            github_token = os.environ.get("GITHUB_TOKEN", "")
        self.github_token_var = tk.StringVar(value=github_token)
        config.GITHUB_TOKEN = github_token
        self.github_status = tk.StringVar(
            value="Token guardado" if github_token else "Sin conectar"
        )
        self.api_key_var = tk.StringVar(value=clave_modelo)
        self.proveedores_tts = {
            "Piper (español argentino, gratis)": "piper",
            "Windows (local)": "windows",
            "ElevenLabs": "elevenlabs",
        }
        self.proveedor_tts_var = tk.StringVar(
            value=next(
                (label for label, code in self.proveedores_tts.items() if code == proveedor_tts),
                "Windows (local)",
            )
        )
        self.elevenlabs_api_key_var = tk.StringVar(value=clave_elevenlabs)
        self.elevenlabs_api_key = ""
        self.elevenlabs_voice_id_var = tk.StringVar(
            value=config.ELEVENLABS_VOICE_ID
        )

        ttk.Label(principal, text="Micrófono (entrada)").grid(row=2, column=0, sticky="w", pady=4)
        self.entrada_menu = ttk.Combobox(
            principal, textvariable=self.entrada_var, values=list(self.entradas), state="readonly", width=56
        )
        self.entrada_menu.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(principal, text="Parlante/auricular (salida)").grid(row=3, column=0, sticky="w", pady=4)
        self.salida_menu = ttk.Combobox(
            principal, textvariable=self.salida_var, values=list(self.salidas), state="readonly", width=56
        )
        self.salida_menu.grid(row=3, column=1, sticky="ew", pady=4)

        ttk.Label(principal, text="Modelo de IA").grid(row=4, column=0, sticky="w", pady=4)
        self.proveedor_menu = ttk.Combobox(
            principal,
            textvariable=self.proveedor_var,
            values=list(self.proveedores),
            state="readonly",
            width=56,
        )
        self.proveedor_menu.grid(row=4, column=1, sticky="ew", pady=4)
        self.proveedor_menu.bind("<<ComboboxSelected>>", self._cambiar_proveedor)

        ttk.Label(principal, text="API key del modelo (se guarda en Windows)").grid(row=5, column=0, sticky="w", pady=4)
        self.api_key_entry = ttk.Entry(principal, textvariable=self.api_key_var, show="*", width=56)
        self.api_key_entry.grid(row=5, column=1, sticky="ew", pady=4)
        ttk.Label(
            principal,
            text="Se almacena en el Administrador de credenciales de Windows, no en un archivo.",
        ).grid(row=6, column=0, columnspan=2, sticky="w")
        self._cambiar_proveedor()

        ttk.Label(principal, text="Voz de respuesta").grid(row=7, column=0, sticky="w", pady=4)
        self.proveedor_tts_menu = ttk.Combobox(
            principal,
            textvariable=self.proveedor_tts_var,
            values=list(self.proveedores_tts),
            state="readonly",
            width=56,
        )
        self.proveedor_tts_menu.grid(row=7, column=1, sticky="ew", pady=4)

        ttk.Label(principal, text="API key de ElevenLabs (se guarda en Windows)").grid(
            row=8, column=0, sticky="w", pady=4
        )
        self.elevenlabs_api_key_entry = ttk.Entry(
            principal, textvariable=self.elevenlabs_api_key_var, show="*", width=56
        )
        self.elevenlabs_api_key_entry.grid(row=8, column=1, sticky="ew", pady=4)
        ttk.Label(principal, text="Voice ID de ElevenLabs").grid(
            row=9, column=0, sticky="w", pady=4
        )
        self.elevenlabs_voice_id_entry = ttk.Entry(
            principal, textvariable=self.elevenlabs_voice_id_var, width=56
        )
        self.elevenlabs_voice_id_entry.grid(row=9, column=1, sticky="ew", pady=4)
        ttk.Label(
            principal,
            text="Copiá el Voice ID de la voz elegida en ElevenLabs. Se guarda en la configuración local.",
        ).grid(row=10, column=0, columnspan=2, sticky="w")
        ttk.Label(
            principal,
            text=f"Voz local predeterminada: Claude ({config.PIPER_MODELO_VOZ}), gratis y sin conexión.",
        ).grid(row=11, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            principal,
            text="Permitir que Jarvis analice la pantalla cuando se lo pida (captura bajo demanda)",
            variable=self.compartir_pantalla_var,
            command=self._cambiar_permiso_pantalla,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            principal,
            text=f"Descargar modelo visual (~3 GB): {config.MODELO_VISION}",
            command=self.preparar_vision,
        ).grid(row=13, column=0, columnspan=2, sticky="w", pady=(4, 0))

        acciones = ttk.Frame(principal)
        acciones.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(16, 8))
        ttk.Button(acciones, text="Probar micrófono", command=self.probar_microfono).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(acciones, text="Probar salida", command=self.probar_salida).grid(
            row=0, column=1, padx=6
        )
        ttk.Button(
            acciones,
            text="Probar reconocimiento",
            command=self.probar_reconocimiento,
        ).grid(row=0, column=2, padx=6)
        ttk.Button(acciones, text="Probar voz", command=self.probar_voz).grid(
            row=0, column=3, padx=6
        )
        ttk.Button(
            acciones, text="Probar ElevenLabs", command=self.probar_elevenlabs
        ).grid(row=0, column=4, padx=6)
        ttk.Button(
            acciones,
            text="Iniciar con voz",
            command=lambda: self.iniciar(False),
            style="Accent.TButton",
        ).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Button(
            acciones,
            text="Iniciar en modo texto",
            command=lambda: self.iniciar(True),
            style="Accent.TButton",
        ).grid(
            row=1, column=1, sticky="w", padx=6, pady=(8, 0)
        )

        self.estado = tk.StringVar(value="Seleccioná los dispositivos y probalos antes de iniciar.")
        ttk.Label(principal, textvariable=self.estado, wraplength=780).grid(
            row=15, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            principal,
            text="La prueba del micrófono dura 5 segundos: hablá durante la medición.",
        ).grid(row=16, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.api_key_entry.bind("<FocusOut>", self._guardar_claves_al_perder_foco)
        self.elevenlabs_api_key_entry.bind("<FocusOut>", self._guardar_claves_al_perder_foco)

        if not entradas:
            self.estado.set("Windows no informa entradas de audio. Revisá permisos y conexión del micrófono.")
        raiz.columnconfigure(0, weight=1)
        raiz.rowconfigure(0, weight=1)
        self._crear_conexiones()
        self._crear_dashboard()
        self._mostrar_pagina("inicio")

    def _configurar_estilos(self):
        estilos = ttk.Style(self.raiz)
        estilos.theme_use("clam")
        estilos.configure(".", background=FONDO, foreground=TEXTO, font=("Segoe UI", 10))
        estilos.configure("App.TFrame", background=FONDO)
        estilos.configure("TFrame", background=FONDO)
        estilos.configure("Card.TFrame", background=PANEL)
        estilos.configure("TLabel", background=FONDO, foreground=TEXTO)
        estilos.configure("Muted.TLabel", foreground=TEXTO_SUAVE, font=("Segoe UI", 10))
        estilos.configure("Title.TLabel", font=("Segoe UI Semibold", 22), foreground=TEXTO)
        estilos.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground=TEXTO_SUAVE)
        estilos.configure(
            "Card.Title.TLabel",
            background=PANEL,
            foreground=TEXTO,
            font=("Segoe UI Semibold", 22),
        )
        estilos.configure(
            "Card.Subtitle.TLabel",
            background=PANEL,
            foreground=TEXTO_SUAVE,
            font=("Segoe UI", 10),
        )
        estilos.configure(
            "TButton",
            background=PANEL_CLARO,
            foreground=TEXTO,
            padding=(12, 8),
            borderwidth=0,
            focusthickness=0,
        )
        estilos.map(
            "TButton",
            background=[("active", "#1A344B"), ("disabled", PANEL)],
            foreground=[("disabled", TEXTO_SUAVE)],
        )
        estilos.configure(
            "Accent.TButton",
            background="#0E7895",
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 10),
            padding=(16, 9),
        )
        estilos.map("Accent.TButton", background=[("active", "#1298B8")])
        estilos.configure(
            "TEntry",
            fieldbackground=PANEL,
            foreground=TEXTO,
            insertcolor=TEXTO,
            padding=8,
            borderwidth=1,
        )
        estilos.configure(
            "TCombobox",
            fieldbackground=PANEL,
            background=PANEL_CLARO,
            foreground=TEXTO,
            arrowcolor=CIAN,
            padding=7,
        )
        estilos.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL)],
            foreground=[("readonly", TEXTO)],
            selectbackground=[("readonly", PANEL)],
            selectforeground=[("readonly", TEXTO)],
        )
        estilos.configure(
            "TCheckbutton",
            background=FONDO,
            foreground=TEXTO_SUAVE,
            padding=4,
        )
        estilos.map(
            "TCheckbutton",
            background=[("active", FONDO)],
            foreground=[("active", CIAN)],
        )
        estilos.configure(
            "Vertical.TScrollbar",
            background=PANEL_CLARO,
            troughcolor=FONDO,
            borderwidth=0,
            arrowsize=12,
        )
        estilos.configure(
            "Sidebar.TButton",
            background="#081725",
            foreground="#9BB5C9",
            anchor="w",
            padding=(12, 12),
            font=("Segoe UI Semibold", 10),
        )
        estilos.map(
            "Sidebar.TButton",
            background=[("active", "#12334A")],
            foreground=[("active", CIAN)],
        )

    def _crear_navegacion(self):
        tk.Label(
            self.sidebar,
            text="◉  J.A.R.V.I.S.",
            bg="#081725",
            fg=CIAN,
            font=("Segoe UI Semibold", 16),
            anchor="w",
        ).pack(fill="x", pady=(2, 4))
        tk.Label(
            self.sidebar,
            text="ASISTENTE PERSONAL",
            bg="#081725",
            fg=TEXTO_SUAVE,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x", pady=(0, 30))
        for texto, pagina in (
            ("⌂   Asistente general", "inicio"),
            ("⚙   Configuración", "ajustes"),
            ("⌘   Integraciones", "conexiones"),
        ):
            ttk.Button(
                self.sidebar,
                text=texto,
                style="Sidebar.TButton",
                command=lambda p=pagina: self._mostrar_pagina(p),
            ).pack(fill="x", pady=3)
        tk.Frame(self.sidebar, bg="#1A3042", height=1).pack(fill="x", pady=20)
        self.nav_status = tk.StringVar(value="●  LISTO PARA INICIAR")
        tk.Label(
            self.sidebar,
            textvariable=self.nav_status,
            bg="#081725",
            fg=TEXTO_SUAVE,
            font=("Segoe UI", 8),
            anchor="w",
            wraplength=170,
        ).pack(fill="x", side="bottom", pady=(0, 4))

    def _mostrar_pagina(self, nombre):
        for pagina in (self.home_page, self.settings_page, self.connections_page):
            pagina.grid_remove()
        paginas = {
            "inicio": self.home_page,
            "ajustes": self.settings_page,
            "conexiones": self.connections_page,
        }
        pagina = paginas[nombre]
        pagina.grid(row=0, column=0, sticky="nsew")
        self.pagina_actual = nombre

    def _crear_conexiones(self):
        self.connections_page.columnconfigure(0, weight=1)
        ttk.Label(
            self.connections_page,
            text="CONEXIONES",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.connections_page,
            text="Servicios conectados a Jarvis. Las claves secretas se guardan en Windows y los ajustes localmente.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 20))

        github = ttk.LabelFrame(
            self.connections_page,
            text="  GitHub  /  búsqueda de repositorios e issues  ",
            padding=18,
        )
        github.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        github.columnconfigure(0, weight=1)
        ttk.Label(
            github,
            text="Creá un token fine-grained con permisos Metadata, Issues, Pull requests y "
            "Contents: Read-only "
            "(elegí solo los repositorios necesarios). "
            "Jarvis solo realiza búsquedas y consultas de lectura.",
            style="Muted.TLabel",
            wraplength=720,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.github_entry = ttk.Entry(
            github,
            textvariable=self.github_token_var,
            show="*",
        )
        self.github_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Button(
            github,
            text="Guardar y probar",
            command=self.probar_github,
            style="Accent.TButton",
        ).grid(row=1, column=1, sticky="e")
        ttk.Button(
            github,
            text="Crear token en GitHub",
            command=lambda: webbrowser.open(
                "https://github.com/settings/personal-access-tokens/new"
            ),
        ).grid(row=2, column=1, sticky="e", pady=(8, 0))
        ttk.Label(
            github,
            textvariable=self.github_status,
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(
            github,
            text="Desconectar",
            command=self.desconectar_github,
        ).grid(row=3, column=1, sticky="e", pady=(8, 0))
        self.github_entry.bind("<FocusOut>", self._guardar_integraciones_al_perder_foco)

        spotify = ttk.LabelFrame(
            self.connections_page,
            text="  Spotify  /  control de reproducción  ",
            padding=18,
        )
        spotify.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        spotify.columnconfigure(0, weight=1)
        ttk.Label(
            spotify,
            text="Pegá el Client ID de tu app de Spotify. Configurá la URI de redirección "
            f"{config.SPOTIFY_REDIRECT_URI} en Spotify Developer Dashboard; no hace falta el secret.",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.spotify_client_id_entry = ttk.Entry(
            spotify,
            textvariable=self.spotify_client_id_var,
        )
        self.spotify_client_id_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Button(
            spotify,
            text="Guardar Client ID",
            command=self.guardar_spotify,
            style="Accent.TButton",
        ).grid(row=1, column=1, sticky="e")
        ttk.Button(
            spotify,
            text="Abrir Spotify Developer Dashboard",
            command=lambda: webbrowser.open("https://developer.spotify.com/dashboard"),
        ).grid(row=2, column=1, sticky="e", pady=(8, 0))
        self.spotify_status = tk.StringVar(
            value="Configurado" if config.SPOTIFY_CLIENT_ID else "Sin configurar"
        )
        ttk.Label(
            spotify,
            textvariable=self.spotify_status,
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        drive = ttk.LabelFrame(
            self.connections_page,
            text="  Google Drive  ",
            padding=18,
        )
        drive.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(
            drive,
            text="Aún no conectado. La integración con Drive requerirá iniciar sesión con Google "
            "y elegir permisos de archivos antes de poder buscar o crear documentos.",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            self.connections_page,
            text="Las búsquedas de GitHub son de solo lectura. Drive no accede a tus archivos hasta que configures y autorices esa integración.",
            style="Muted.TLabel",
            wraplength=800,
        ).grid(row=5, column=0, sticky="w", pady=(8, 0))
        self._crear_localizacion()

    def _crear_localizacion(self):
        ubicacion = ttk.LabelFrame(
            self.connections_page,
            text="  Ubicación aproximada  ",
            padding=18,
        )
        ubicacion.grid(row=6, column=0, sticky="ew", pady=(0, 14))
        ubicacion.columnconfigure(0, weight=1)
        ttk.Label(
            ubicacion,
            text="Detectar por IP puede revelar tu dirección IP al servicio de geolocalización. "
            "Jarvis solo consulta si aceptás; guarda ciudad y región, no tus coordenadas.",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.location_label = tk.StringVar(
            value=f"Ubicación guardada: {config.UBICACION or 'ninguna'}"
        )
        ttk.Label(
            ubicacion,
            textvariable=self.location_label,
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w")
        ttk.Button(
            ubicacion,
            text="Detectar ubicación por IP",
            command=self.detectar_ubicacion,
        ).grid(row=1, column=1, sticky="e", padx=(12, 0))
        ttk.Label(
            ubicacion,
            text="También podés escribir tu ciudad y guardarla sin consultar servicios externos.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 4))
        self.ubicacion_entry = ttk.Entry(ubicacion, textvariable=self.ubicacion_var)
        self.ubicacion_entry.grid(row=3, column=0, sticky="ew", padx=(0, 10))
        ttk.Button(
            ubicacion,
            text="Guardar ciudad",
            command=self.guardar_ubicacion_manual,
        ).grid(row=3, column=1, sticky="e")

    def _crear_dashboard(self):
        self.home_page.columnconfigure(0, weight=1)
        self.home_page.rowconfigure(1, weight=1)
        encabezado = ttk.Frame(self.home_page, style="App.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        encabezado.columnconfigure(0, weight=1)
        ttk.Label(
            encabezado,
            text="J.A.R.V.I.S.   /   ASISTENTE GENERAL",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.reloj_var = tk.StringVar()
        ttk.Label(
            encabezado,
            textvariable=self.reloj_var,
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="e")

        paneles = ttk.Frame(self.home_page, style="App.TFrame")
        paneles.grid(row=1, column=0, sticky="nsew")
        paneles.columnconfigure(0, weight=1)
        paneles.columnconfigure(1, weight=2)
        paneles.columnconfigure(2, weight=1)
        paneles.rowconfigure(0, weight=1)

        estado = ttk.LabelFrame(paneles, text="  ESTADO DEL SISTEMA  ", padding=16)
        estado.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.system_status_var = tk.StringVar(value="Sistema: listo")
        self.audio_status_var = tk.StringVar(value="Micrófono: detenido")
        self.network_status_var = tk.StringVar(value="Red: disponible")
        for row, variable in enumerate(
            (self.system_status_var, self.audio_status_var, self.network_status_var)
        ):
            ttk.Label(
                estado,
                textvariable=variable,
                style="Muted.TLabel",
                wraplength=200,
            ).grid(row=row, column=0, sticky="w", pady=7)
        ttk.Button(
            estado,
            text="Configurar audio",
            command=lambda: self._mostrar_pagina("ajustes"),
        ).grid(row=4, column=0, sticky="w", pady=(16, 0))

        nucleo = ttk.Frame(paneles, style="App.TFrame")
        nucleo.grid(row=0, column=1, sticky="nsew")
        nucleo.columnconfigure(0, weight=1)
        nucleo.rowconfigure(0, weight=1)
        self._orb_canvas = tk.Canvas(
            nucleo,
            width=340,
            height=340,
            bg=FONDO,
            highlightthickness=0,
        )
        self._orb_canvas.grid(row=0, column=0, sticky="nsew")
        self._core_label = ttk.Label(
            nucleo,
            text="¿En qué puedo ayudarte?",
            style="Subtitle.TLabel",
            anchor="center",
        )
        self._core_label.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        botones = ttk.Frame(nucleo, style="App.TFrame")
        botones.grid(row=2, column=0)
        ttk.Button(
            botones,
            text="Iniciar con voz",
            style="Accent.TButton",
            command=lambda: self.iniciar(False),
        ).grid(row=0, column=0, padx=5)
        ttk.Button(
            botones,
            text="Iniciar en texto",
            command=lambda: self.iniciar(True),
        ).grid(row=0, column=1, padx=5)
        ttk.Button(
            botones,
            text="Abrir HUD",
            command=self._boton_hud,
        ).grid(row=0, column=2, padx=5)

        informacion = ttk.LabelFrame(paneles, text="  INFORMACIÓN  ", padding=16)
        informacion.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
        self.clock_big_var = tk.StringVar()
        ttk.Label(
            informacion,
            textvariable=self.clock_big_var,
            font=("Segoe UI Light", 30),
            foreground=CIAN,
        ).grid(row=0, column=0, sticky="w", pady=(5, 0))
        self.date_var = tk.StringVar()
        ttk.Label(
            informacion,
            textvariable=self.date_var,
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 14))
        ttk.Label(
            informacion,
            textvariable=self.location_label,
            style="Muted.TLabel",
            wraplength=205,
        ).grid(row=2, column=0, sticky="w", pady=(4, 16))
        ttk.Label(
            informacion,
            text="USO DEL SISTEMA",
            foreground=CIAN,
        ).grid(row=3, column=0, sticky="w", pady=(4, 6))
        self.cpu_var = tk.StringVar(value="CPU  --")
        self.ram_var = tk.StringVar(value="RAM  --")
        self.disk_var = tk.StringVar(value="Disco  --")
        for row, variable in enumerate(
            (self.cpu_var, self.ram_var, self.disk_var), start=4
        ):
            ttk.Label(
                informacion,
                textvariable=variable,
                style="Muted.TLabel",
            ).grid(row=row, column=0, sticky="w", pady=5)

        self._crear_chat()
        self._actualizar_stats()

    def _crear_chat_dashboard(self):
        chat = tk.Frame(
            self.home_page,
            bg=PANEL,
            highlightbackground="#1D7188",
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        chat.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        chat.columnconfigure(0, weight=1)
        self.historial_ui = tk.Text(
            chat,
            wrap="word",
            state="disabled",
            height=5,
            bg=PANEL,
            fg=TEXTO,
            insertbackground=CIAN,
            selectbackground="#1D5368",
            selectforeground="#FFFFFF",
            font=("Segoe UI", 11),
            padx=8,
            pady=5,
            relief="flat",
            borderwidth=0,
            spacing1=2,
            spacing3=5,
            highlightthickness=0,
        )
        self.historial_ui.tag_configure(
            "assistant",
            foreground=CIAN,
            font=("Segoe UI Semibold", 9),
            spacing1=6,
        )
        self.historial_ui.tag_configure(
            "user",
            foreground="#B8C6D4",
            font=("Segoe UI Semibold", 9),
            spacing1=6,
        )
        self.historial_ui.tag_configure("body", foreground=TEXTO)
        self.historial_ui.grid(row=0, column=0, sticky="ew")
        entrada = ttk.Frame(chat, style="Card.TFrame", padding=(8, 6))
        entrada.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        entrada.columnconfigure(0, weight=1)
        self.mensaje = tk.StringVar()
        self.campo_mensaje = ttk.Entry(entrada, textvariable=self.mensaje)
        self.campo_mensaje.grid(row=0, column=0, sticky="ew")
        self.campo_mensaje.bind("<Return>", lambda _: self.enviar())
        ttk.Button(
            entrada,
            text="Enviar  >",
            style="Accent.TButton",
            command=self.enviar,
        ).grid(row=0, column=1, padx=(8, 0))
        self.estado_chat = tk.StringVar(value="Configurá tus conexiones o iniciá la conversación.")
        ttk.Label(
            chat,
            textvariable=self.estado_chat,
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._animar_nucleo()

    def _actualizar_stats(self):
        if self.cerrando.is_set():
            return
        ahora = datetime.now()
        dias = (
            "lunes",
            "martes",
            "miércoles",
            "jueves",
            "viernes",
            "sábado",
            "domingo",
        )
        meses = (
            "enero",
            "febrero",
            "marzo",
            "abril",
            "mayo",
            "junio",
            "julio",
            "agosto",
            "septiembre",
            "octubre",
            "noviembre",
            "diciembre",
        )
        self.reloj_var.set(
            f"{dias[ahora.weekday()].upper()} {ahora.day:02} "
            f"{meses[ahora.month - 1].upper()} {ahora.year}   {ahora:%H:%M}"
        )
        self.clock_big_var.set(ahora.strftime("%H:%M"))
        self.date_var.set(
            f"{dias[ahora.weekday()].capitalize()}, {ahora.day} "
            f"de {meses[ahora.month - 1]} de {ahora.year}"
        )
        self.network_status_var.set(
            "GitHub: token configurado" if config.GITHUB_TOKEN else "GitHub: sin token"
        )
        try:
            import psutil

            memoria = psutil.virtual_memory()
            disco = psutil.disk_usage(str(Path.home().anchor or "/"))
            self.cpu_var.set(f"CPU  {psutil.cpu_percent(interval=None):.0f}%")
            self.ram_var.set(f"RAM  {memoria.percent:.0f}%")
            self.disk_var.set(f"Disco  {disco.percent:.0f}%")
        except Exception as error:
            self.cpu_var.set(f"Uso del sistema no disponible: {error}")
        self.raiz.after(1000, self._actualizar_stats)

    def _actualizar_estado_nucleo(self, texto):
        if hasattr(self, "_core_label"):
            self._core_label.configure(text=texto)

    def _guardar_integraciones_al_perder_foco(self, evento):
        if evento.widget is self.github_entry:
            try:
                token = self.github_token_var.get().strip()
                self._guardar_clave("github", token)
                config.GITHUB_TOKEN = token
                self.github_status.set("Token guardado. Pulsá «Guardar y probar» para verificarlo.")
            except Exception as error:
                messagebox.showerror(
                    "No se pudo guardar el token",
                    f"No pude guardar el token de GitHub en Windows:\n{error}",
                    parent=self.raiz,
                )

    def probar_github(self):
        token = self.github_token_var.get().strip()
        if not token:
            self.github_status.set("Pegá un token personal para conectar GitHub.")
            return
        try:
            self._guardar_clave("github", token)
            config.GITHUB_TOKEN = token
        except Exception as error:
            messagebox.showerror(
                "No se pudo guardar el token",
                f"No pude guardar el token en el Administrador de credenciales de Windows:\n{error}",
                parent=self.raiz,
            )
            return
        self.github_status.set("Probando conexión con GitHub...")
        threading.Thread(
            target=self._probar_github_en_segundo_plano,
            args=(token,),
            daemon=True,
        ).start()

    def desconectar_github(self):
        if not messagebox.askyesno(
            "Desconectar GitHub",
            "¿Querés borrar el token de GitHub del Administrador de credenciales de Windows?",
            parent=self.raiz,
        ):
            return
        try:
            self._eliminar_clave("github")
        except Exception as error:
            messagebox.showerror(
                "No se pudo borrar el token",
                f"No pude borrar el token del Administrador de credenciales:\n{error}",
                parent=self.raiz,
            )
            return
        self.github_token_var.set("")
        config.GITHUB_TOKEN = ""
        self.github_status.set("Sin conectar")

    def _probar_github_en_segundo_plano(self, token):
        try:
            import httpx

            respuesta = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            if respuesta.status_code in (401, 403):
                raise ValueError("GitHub rechazó el token o sus permisos (HTTP "
                                 f"{respuesta.status_code}).")
            respuesta.raise_for_status()
            usuario = respuesta.json().get("login", "usuario")
            estado = f"Conectado como {usuario}. Búsquedas de solo lectura."
            config.GITHUB_TOKEN = token
        except Exception as error:
            estado = f"No se pudo conectar con GitHub: {error}"
        if not self.cerrando.is_set():
            self.raiz.after(0, self.github_status.set, estado)

    def guardar_ubicacion_manual(self):
        try:
            self._guardar()
        except (OSError, KeyError) as error:
            messagebox.showerror("Ubicación", f"No pude guardar la ciudad:\n{error}", parent=self.raiz)
            return
        self.location_label.set(f"Ubicación guardada: {config.UBICACION or 'ninguna'}")
        self._estado_visible("Ubicación guardada en este dispositivo.")

    def detectar_ubicacion(self):
        if not messagebox.askyesno(
            "Permitir ubicación aproximada",
            "Jarvis enviará tu dirección IP a ipapi.co para estimar ciudad y región. "
            "La ubicación puede ser imprecisa. ¿Querés continuar?",
            parent=self.raiz,
        ):
            return
        self.location_label.set("Detectando ubicación aproximada...")
        threading.Thread(target=self._detectar_ubicacion_en_segundo_plano, daemon=True).start()

    def _detectar_ubicacion_en_segundo_plano(self):
        try:
            import httpx

            respuesta = httpx.get(
                "https://ipapi.co/json/",
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
            if datos.get("error"):
                raise RuntimeError(datos.get("reason", "El servicio no pudo ubicar esta conexión."))
            ubicacion = ", ".join(
                parte
                for parte in (datos.get("city"), datos.get("region"), datos.get("country_name"))
                if parte
            )
            if not ubicacion:
                raise RuntimeError("El servicio no devolvió una ciudad aproximada.")
            self.raiz.after(0, self._guardar_ubicacion_detectada, ubicacion)
        except Exception as error:
            if not self.cerrando.is_set():
                self.raiz.after(
                    0,
                    self.location_label.set,
                    f"No se pudo detectar la ubicación: {error}",
                )

    def _guardar_ubicacion_detectada(self, ubicacion):
        self.ubicacion_var.set(ubicacion)
        try:
            self._guardar()
        except (OSError, KeyError) as error:
            self.location_label.set(f"Ubicación detectada, pero no se pudo guardar: {error}")
            return
        self.location_label.set(f"Ubicación guardada: {ubicacion}")
        self._estado_visible("Ubicación aproximada guardada. Podés cambiarla cuando quieras.")

    def _cambiar_permiso_pantalla(self):
        config.COMPARTIENDO_PANTALLA = self.compartir_pantalla_var.get()
        self.bus.publish("pantalla", on=config.COMPARTIENDO_PANTALLA)
        self._publicar_info()
        if config.COMPARTIENDO_PANTALLA:
            estado = "Acceso a pantalla habilitado. Jarvis solo captura al pedírselo."
        else:
            estado = "Acceso a pantalla desactivado."
        self._estado_visible(estado)

    def _estado_visible(self, texto):
        self.bus.publish("estado", valor="espera", texto=texto)

    def preparar_vision(self):
        if self.vision_descargando:
            return
        self.vision_descargando = True
        self._estado_visible(
            f"Descargando {config.MODELO_VISION} desde Ollama; puede tardar según tu conexión..."
        )
        threading.Thread(target=self._descargar_modelo_vision, daemon=True).start()

    def _descargar_modelo_vision(self):
        try:
            import ollama

            cliente = ollama.Client(host=config.OLLAMA_HOST)
            for progreso in cliente.pull(config.MODELO_VISION, stream=True):
                estado = getattr(progreso, "status", "Descargando modelo visual...")
                total = getattr(progreso, "total", None)
                completado = getattr(progreso, "completed", None)
                if total and completado is not None:
                    estado += f" ({100 * completado / total:.0f}%)"
                self.raiz.after(0, self._estado_visible, estado)
            resultado = f"Modelo visual listo: {config.MODELO_VISION}. Habilitá el permiso para analizar la pantalla."
        except Exception as error:
            resultado = f"No pude descargar el modelo visual: {error}. Verificá que Ollama esté abierto y tenga internet."
        finally:
            self.vision_descargando = False
        self.raiz.after(0, self._estado_visible, resultado)

    @staticmethod
    def _dispositivos():
        entradas, salidas = {}, {}
        try:
            dispositivos = sd.query_devices()
            apis = sd.query_hostapis()
        except Exception as error:
            messagebox.showerror("Audio no disponible", f"No pude consultar los dispositivos de audio:\n{error}")
            return entradas, salidas

        for indice, dispositivo in enumerate(dispositivos):
            api = apis[dispositivo["hostapi"]]["name"]
            etiqueta = (
                f"{indice}: {dispositivo['name']} ({api}, "
                f"{int(dispositivo['default_samplerate'])} Hz)"
            )
            if dispositivo["max_input_channels"]:
                entradas[etiqueta] = indice
            if dispositivo["max_output_channels"]:
                salidas[etiqueta] = indice
        return entradas, salidas

    @staticmethod
    def _etiqueta(opciones, valor):
        if isinstance(valor, dict):
            nombre = str(valor.get("name", "")).casefold()
            hostapi = str(valor.get("hostapi", "")).casefold()
            coincidencia = next(
                (
                    etiqueta
                    for etiqueta, indice in opciones.items()
                    if indice != ""
                    and sd.query_devices(indice)["name"].casefold() == nombre
                    and sd.query_hostapis(
                        sd.query_devices(indice)["hostapi"]
                    )["name"].casefold()
                    == hostapi
                ),
                None,
            )
            if coincidencia is None:
                coincidencia = next(
                    (
                        etiqueta
                        for etiqueta, indice in opciones.items()
                        if indice != ""
                        and sd.query_devices(indice)["name"].casefold() == nombre
                    ),
                    None,
                )
            if coincidencia is not None:
                return coincidencia
        return next((nombre for nombre, indice in opciones.items() if indice == valor), next(iter(opciones)))

    @staticmethod
    def _clave_entorno(proveedor):
        variable = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY",
            "github": "GITHUB_TOKEN",
        }.get(proveedor, "")
        return os.environ.get(variable, "") if variable else ""

    @staticmethod
    def _almacen_claves():
        import keyring

        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            keyring.set_keyring(WinVaultKeyring())
        return keyring

    @classmethod
    def _clave_guardada_o_entorno(cls, proveedor):
        guardada = cls._almacen_claves().get_password("Jarvis", proveedor)
        return guardada or cls._clave_entorno(proveedor)

    @classmethod
    def _clave_local_guardada(cls, proveedor):
        return bool(cls._almacen_claves().get_password("Jarvis", proveedor))

    @classmethod
    def _guardar_clave(cls, proveedor, clave):
        if clave:
            cls._almacen_claves().set_password("Jarvis", proveedor, clave)

    @classmethod
    def _eliminar_clave(cls, proveedor):
        almacen = cls._almacen_claves()
        try:
            almacen.delete_password("Jarvis", proveedor)
        except almacen.errors.PasswordDeleteError:
            pass

    def _cambiar_proveedor(self, evento=None):
        etiqueta = evento.widget.get() if evento else self.proveedor_var.get()
        proveedor = self.proveedores[etiqueta]
        anterior = self._proveedor_modelo_visible
        if proveedor != anterior:
            try:
                self._guardar_clave(anterior, self.api_key_var.get().strip())
            except Exception as error:
                messagebox.showerror(
                    "No se pudo guardar la API key",
                    f"No pude guardar la clave en el Administrador de credenciales de Windows:\n{error}",
                    parent=self.raiz,
                )
                self.proveedor_var.set(
                    next(
                        etiqueta
                        for etiqueta, codigo in self.proveedores.items()
                        if codigo == anterior
                    )
                )
                return
            self._proveedor_modelo_visible = proveedor
            try:
                self.api_key_var.set(self._clave_guardada_o_entorno(proveedor))
            except Exception as error:
                messagebox.showerror(
                    "No se pudo leer la API key",
                    f"No pude leer la clave del Administrador de credenciales de Windows:\n{error}",
                    parent=self.raiz,
                )
                self.api_key_var.set(self._clave_entorno(proveedor))
        if proveedor == "ollama":
            self.api_key_entry.state(["disabled"])
        else:
            self.api_key_entry.state(["!disabled"])

    def _guardar_claves(self):
        self._guardar_clave(
            self._proveedor_modelo_visible, self.api_key_var.get().strip()
        )
        self._guardar_clave(
            "elevenlabs", self.elevenlabs_api_key_var.get().strip()
        )
        token_github = self.github_token_var.get().strip()
        self._guardar_clave("github", token_github)
        config.GITHUB_TOKEN = token_github

    def _guardar_claves_al_perder_foco(self, evento):
        if evento.widget not in (
            self.api_key_entry,
            self.elevenlabs_api_key_entry,
        ):
            return
        try:
            self._guardar_claves()
        except Exception as error:
            messagebox.showerror(
                "No se pudieron guardar las API keys",
                f"No pude guardar las claves en el Administrador de credenciales de Windows:\n{error}",
                parent=self.raiz,
            )

    @staticmethod
    def _leer_preferencia_entera(datos, clave, predeterminado, minimo, maximo):
        valor = datos.get(clave, predeterminado)
        if isinstance(valor, bool):
            return predeterminado
        try:
            valor = int(valor)
        except (TypeError, ValueError):
            return predeterminado
        return max(minimo, min(maximo, valor))

    @staticmethod
    def _validar_preferencia_entera(datos, clave, predeterminado, minimo, maximo):
        valor = datos.get(clave, predeterminado)
        if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
            raise ValueError(
                f"El valor de {clave.replace('_', ' ')} debe ser un entero "
                f"entre {minimo} y {maximo}."
            )
        return valor

    @staticmethod
    def _leer_seleccion():
        ruta = config.CARPETA_DATOS / "dispositivos_audio.json"
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return (
                config.MICROFONO,
                config.AUDIO_SALIDA,
                config.PROVEEDOR_IA,
                config.PROVEEDOR_TTS,
                config.ELEVENLABS_VOICE_ID,
                config.UBICACION,
                config.SPOTIFY_CLIENT_ID,
                config.MODELO_VISION,
                config.MODELO,
                config.MODELO_GEMINI,
                config.MODELO_OPENAI,
                config.OLLAMA_HOST,
                config.WHISPER_MODELO,
                config.IDIOMA,
                config.VOLUMEN_VOZ,
                config.VELOCIDAD_VOZ,
                config.VOLUMEN_EFECTOS,
                config.TEMA_HUD,
            )
        except (OSError, ValueError) as error:
            messagebox.showerror("Configuración de audio", f"No pude leer la selección guardada:\n{error}")
            return (
                config.MICROFONO,
                config.AUDIO_SALIDA,
                config.PROVEEDOR_IA,
                config.PROVEEDOR_TTS,
                config.ELEVENLABS_VOICE_ID,
                config.UBICACION,
                config.SPOTIFY_CLIENT_ID,
                config.MODELO_VISION,
                config.MODELO,
                config.MODELO_GEMINI,
                config.MODELO_OPENAI,
                config.OLLAMA_HOST,
                config.WHISPER_MODELO,
                config.IDIOMA,
                config.VOLUMEN_VOZ,
                config.VELOCIDAD_VOZ,
                config.VOLUMEN_EFECTOS,
                config.TEMA_HUD,
            )
        return (
            datos.get("microfono", config.MICROFONO),
            datos.get("salida", config.AUDIO_SALIDA),
            datos.get("proveedor", config.PROVEEDOR_IA),
            (
                config.PROVEEDOR_TTS
                if datos.get("tts_version", 0) < 1
                else datos.get("proveedor_tts", config.PROVEEDOR_TTS)
            ),
            datos.get("elevenlabs_voice_id", config.ELEVENLABS_VOICE_ID),
            datos.get("ubicacion", config.UBICACION),
            datos.get("spotify_client_id", config.SPOTIFY_CLIENT_ID),
            datos.get("vision_modelo", config.MODELO_VISION),
            datos.get("modelo_ollama", config.MODELO),
            datos.get("modelo_gemini", config.MODELO_GEMINI),
            datos.get("modelo_openai", config.MODELO_OPENAI),
            datos.get("ollama_host", config.OLLAMA_HOST),
            datos.get("modelo_whisper", config.WHISPER_MODELO),
            datos.get("idioma", config.IDIOMA),
            Lanzador._leer_preferencia_entera(
                datos, "volumen_voz", config.VOLUMEN_VOZ, 0, 100
            ),
            Lanzador._leer_preferencia_entera(
                datos, "velocidad_voz", config.VELOCIDAD_VOZ, 70, 130
            ),
            Lanzador._leer_preferencia_entera(
                datos, "volumen_efectos", config.VOLUMEN_EFECTOS, 0, 100
            ),
            (
                datos["tema_hud"]
                if datos.get("tema_hud") in {"cian", "violeta", "verde"}
                else config.TEMA_HUD
            ),
        )

    def _guardar(self):
        microfono_id = self.entradas[self.entrada_var.get()]
        if microfono_id == "":
            microfono = ""
        else:
            dispositivo = sd.query_devices(microfono_id)
            hostapi = sd.query_hostapis(dispositivo["hostapi"])["name"]
            microfono = {"name": dispositivo["name"], "hostapi": hostapi}
        salida = self.salidas[self.salida_var.get()]
        proveedor = self.proveedores[self.proveedor_var.get()]
        proveedor_tts = self.proveedores_tts[self.proveedor_tts_var.get()]
        voice_id = self.elevenlabs_voice_id_var.get().strip()
        ubicacion = self.ubicacion_var.get().strip()
        spotify_client_id = self.spotify_client_id_var.get().strip()
        config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
        ruta = config.CARPETA_DATOS / "dispositivos_audio.json"
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(
                {
                    "microfono": microfono,
                    "salida": salida,
                    "proveedor": proveedor,
                    "proveedor_tts": proveedor_tts,
                    "tts_version": 1,
                    "elevenlabs_voice_id": voice_id,
                    "ubicacion": ubicacion,
                    "spotify_client_id": spotify_client_id,
                    "vision_modelo": config.MODELO_VISION,
                    "modelo_ollama": config.MODELO,
                    "modelo_gemini": config.MODELO_GEMINI,
                    "modelo_openai": config.MODELO_OPENAI,
                    "ollama_host": config.OLLAMA_HOST,
                    "modelo_whisper": config.WHISPER_MODELO,
                    "idioma": config.IDIOMA,
                    "volumen_voz": config.VOLUMEN_VOZ,
                    "velocidad_voz": config.VELOCIDAD_VOZ,
                    "volumen_efectos": config.VOLUMEN_EFECTOS,
                    "tema_hud": config.TEMA_HUD,
                    "volumen_voz": config.VOLUMEN_VOZ,
                    "velocidad_voz": config.VELOCIDAD_VOZ,
                    "volumen_efectos": config.VOLUMEN_EFECTOS,
                    "tema_hud": config.TEMA_HUD,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporal.replace(ruta)
        config.MICROFONO = microfono
        config.AUDIO_SALIDA = salida
        config.PROVEEDOR_IA = proveedor
        config.PROVEEDOR_TTS = proveedor_tts
        config.ELEVENLABS_VOICE_ID = voice_id
        config.UBICACION = ubicacion
        config.SPOTIFY_CLIENT_ID = spotify_client_id
        return microfono, salida, proveedor

    def guardar_spotify(self):
        try:
            self._guardar()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de Spotify", str(error), parent=self.raiz)
            return
        self.spotify_status.set(
            "Client ID guardado. Usá Spotify desde el HUD para autorizar la cuenta."
            if config.SPOTIFY_CLIENT_ID
            else "Sin configurar"
        )
        self._publicar_info()
        self._estado_visible("Configuración de Spotify guardada.")

    def probar_microfono(self):
        try:
            microfono, _, _ = self._guardar()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de audio", str(error))
            return
        self.estado.set("Midiendo el micrófono durante 5 segundos; hablá ahora...")
        threading.Thread(
            target=self._medir_microfono, args=(microfono,), daemon=True
        ).start()

    def _medir_microfono(self, microfono):
        try:
            resultado = voice.medir_microfono(microfono if microfono != "" else None)
            nombre, _, frecuencia, niveles, desbordados = resultado
            maximo = max(niveles, default=0)
            estado = (
                f"{nombre} ({frecuencia} Hz): niveles "
                + ", ".join(f"{nivel:.4f}" for nivel in niveles)
                + (". Se detectó audio." if maximo >= 0.001 else ". No se detectó audio.")
            )
            if any(desbordados):
                estado += " Hubo desbordamiento de audio; probá cerrar otras apps de audio."
        except Exception as error:
            estado = f"Falló la prueba del micrófono: {error}"
        self.raiz.after(0, self.estado.set, estado)

    def probar_reconocimiento(self):
        try:
            microfono, _, _ = self._guardar()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de audio", str(error))
            return
        self.estado.set("Cargando Whisper y escuchando una frase; hablá ahora...")
        threading.Thread(
            target=self._probar_reconocimiento,
            args=(microfono,),
            daemon=True,
        ).start()

    def _probar_reconocimiento(self, microfono):
        try:
            texto = voice.probar_reconocimiento(
                microfono if microfono != "" else None
            )
            estado = f"Whisper reconoció: «{texto}»"
        except Exception as error:
            estado = f"Falló la prueba de reconocimiento: {error}"
        self.raiz.after(0, self.estado.set, estado)

    def probar_salida(self):
        try:
            _, salida, _ = self._guardar()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de audio", str(error))
            return
        threading.Thread(target=self._reproducir_prueba, args=(salida,), daemon=True).start()

    def probar_voz(self):
        try:
            self._guardar()
            self._guardar_claves()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de voz", str(error))
            return
        except Exception as error:
            messagebox.showerror(
                "No se pudieron guardar las API keys",
                f"No pude guardar las claves en el Administrador de credenciales de Windows:\n{error}",
                parent=self.raiz,
            )
            return
        self.elevenlabs_api_key = self.elevenlabs_api_key_var.get().strip()
        self.estado.set("Probando la voz seleccionada...")
        threading.Thread(target=self._reproducir_prueba_voz, daemon=True).start()

    def _reproducir_prueba_voz(self):
        error = voice.hablar(
            "Hola, soy Jarvis. Esta es una prueba de voz.",
            api_key=self.elevenlabs_api_key,
        )
        estado = error or (
            "Prueba de voz completada."
            if config.PROVEEDOR_TTS == "elevenlabs"
            else "Prueba de voz local completada."
        )
        self.raiz.after(0, self.estado.set, estado)

    def probar_elevenlabs(self):
        try:
            self._guardar()
            self._guardar_claves()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de voz", str(error))
            return
        except Exception as error:
            messagebox.showerror(
                "No se pudieron guardar las API keys",
                f"No pude guardar las claves en el Administrador de credenciales de Windows:\n{error}",
                parent=self.raiz,
            )
            return

        api_key = self.elevenlabs_api_key_var.get().strip() or os.environ.get(
            "ELEVENLABS_API_KEY", ""
        )
        if not api_key:
            self.estado.set("Falta la API key de ElevenLabs. Pegala en el campo correspondiente.")
            return
        voice_id = self.elevenlabs_voice_id_var.get().strip()
        if not voice_id or voice_id == "TU_VOICE_ID":
            self.estado.set(
                "Falta un Voice ID válido. Copialo desde la voz elegida en ElevenLabs."
            )
            return

        self.estado.set(
            f"Probando ElevenLabs con la voz {voice_id}..."
        )
        threading.Thread(
            target=self._reproducir_prueba_elevenlabs,
            args=(api_key, voice_id),
            daemon=True,
        ).start()

    def _reproducir_prueba_elevenlabs(self, api_key, voice_id):
        try:
            codigo = voice.probar_elevenlabs(
                "Hola, soy Jarvis. ¿En qué puedo ayudarte?",
                api_key=api_key,
                voice_id=voice_id,
            )
            estado = (
                f"ElevenLabs respondió HTTP {codigo}: audio generado y reproducido "
                f"con la voz {voice_id}."
            )
        except Exception as error:
            estado = f"Falló la prueba de ElevenLabs: {error}"
        self.raiz.after(0, self.estado.set, estado)

    def _reproducir_prueba(self, salida):
        try:
            import numpy as np

            info = sd.query_devices(salida, "output") if salida is not None else sd.query_devices(kind="output")
            frecuencia = int(info["default_samplerate"])
            tiempo = np.arange(int(frecuencia * 0.5)) / frecuencia
            tono = (0.15 * np.sin(2 * np.pi * 660 * tiempo)).astype(np.float32)
            sd.play(tono, samplerate=frecuencia, device=salida, blocking=True)
            estado = f"Prueba de salida reproducida por: {info['name']}."
        except Exception as error:
            estado = f"Falló la prueba de salida: {error}"
        self.raiz.after(0, self.estado.set, estado)

    def iniciar(self, texto):
        if self.asistente_activo:
            return True
        proveedor = config.PROVEEDOR_IA
        try:
            api_key = self._clave_guardada_o_entorno(proveedor)
            elevenlabs_api_key = self._clave_guardada_o_entorno("elevenlabs")
        except Exception as error:
            self.bus.publish("msg", rol="jarvis", texto=f"No pude leer las credenciales guardadas: {error}")
            self.bus.publish("estado", valor="desconectado", texto="Revisá las credenciales desde Ajustes.")
            return False
        if proveedor != "ollama" and not api_key:
            self.bus.publish("msg", rol="jarvis", texto=f"Falta la API key de {proveedor}. Guardala desde Ajustes.")
            self.bus.publish("ajustes")
            return False
        proveedor_tts = config.PROVEEDOR_TTS
        if not texto and proveedor_tts == "elevenlabs" and not (
            elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY")
        ):
            self.bus.publish("msg", rol="jarvis", texto="Falta la API key de ElevenLabs. Guardala desde Ajustes.")
            self.bus.publish("ajustes")
            return False
        self.modo = "texto" if texto else "voz"
        self.elevenlabs_api_key = elevenlabs_api_key
        config.USAR_VOZ = not texto
        self.asistente_activo = True
        proveedor_nombre = proveedor.capitalize()
        self.bus.publish("estado", valor="pensando", texto=f"Conectando con {proveedor_nombre}…")
        self._publicar_info()
        threading.Thread(
            target=self._iniciar_asistente,
            args=(proveedor, api_key),
            daemon=True,
        ).start()
        return True

    def _crear_chat(self):
        if not hasattr(self, "historial_ui"):
            self._crear_chat_dashboard()
        self.campo_mensaje.focus_set()

    def _animar_nucleo(self):
        canvas = getattr(self, "_orb_canvas", None)
        if canvas is None or not canvas.winfo_exists() or self.cerrando.is_set():
            return
        canvas.delete("all")
        centro_x = centro_y = 52
        self._fase_nucleo = (self._fase_nucleo + 7) % 360
        canvas.create_oval(9, 9, 95, 95, outline="#1A4257", width=1)
        canvas.create_oval(18, 18, 86, 86, outline="#1F6077", width=1)
        canvas.create_arc(
            13,
            13,
            91,
            91,
            start=self._fase_nucleo,
            extent=92,
            style="arc",
            outline=CIAN,
            width=2,
        )
        canvas.create_arc(
            23,
            23,
            81,
            81,
            start=-self._fase_nucleo * 1.4,
            extent=72,
            style="arc",
            outline="#32AFCB",
            width=2,
        )
        for indice in range(24):
            angulo = math.radians(indice * 15 + self._fase_nucleo)
            interior = 40
            exterior = interior + (4 if indice % 3 == 0 else 1)
            canvas.create_line(
                centro_x + math.cos(angulo) * interior,
                centro_y + math.sin(angulo) * interior,
                centro_x + math.cos(angulo) * exterior,
                centro_y + math.sin(angulo) * exterior,
                fill="#347D91" if indice % 3 else CIAN,
                width=1,
            )
        canvas.create_oval(34, 34, 70, 70, fill="#102B3D", outline="#257D96", width=1)
        canvas.create_oval(42, 42, 62, 62, fill=CIAN, outline="#B7F5FF", width=1)
        canvas.create_oval(48, 48, 56, 56, fill="#E8FCFF", outline="")
        self.raiz.after(70, self._animar_nucleo)

    def _agregar_mensaje(self, quien, texto):
        self.bus.publish(
            "msg",
            rol="jarvis" if quien == config.NOMBRE else "usuario",
            texto=texto,
            voz="voz" in quien.lower(),
        )

    def _estado(self, texto):
        bajo = texto.lower()
        if "procesando" in bajo:
            valor = "pensando"
        elif "escuch" in bajo:
            valor = "escuchando"
        else:
            valor = "espera"
        self.bus.publish("estado", valor=valor, texto=texto)

    def _encolar_pedido(self, texto, origen):
        import tools

        with self._pedidos_lock:
            if not self._pedido_en_curso:
                tools.reiniciar_detencion()
            self._respuesta_lista.clear()
            self.pedidos.put((texto, origen))

    def enviar(self):
        texto = self.mensaje.get().strip()
        if not texto:
            return
        if not self.asistente_activo and not self.iniciar(True):
            return
        self.mensaje.set("")
        self._agregar_mensaje("Tú", texto)
        self._encolar_pedido(texto, "texto")

    def _confirmar(self, texto):
        if self.bus.hay_hud():
            return self.bus.preguntar(texto, timeout=60)
        resultado = threading.Event()
        respuesta = {"ok": False}

        def preguntar():
            if not self.cerrando.is_set():
                respuesta["ok"] = messagebox.askyesno("Confirmar acción", texto, parent=self.raiz)
            resultado.set()

        self.raiz.after(0, preguntar)
        while not resultado.wait(0.1):
            if self.cerrando.is_set():
                return False
        return respuesta["ok"]

    def _iniciar_asistente(self, proveedor, api_key):
        from brain import Cerebro

        import tools
        import visionTools

        _ = visionTools
        tools.NOTIFICAR = self._notificar
        tools.CONFIRMAR = self._confirmar
        try:
            cerebro = Cerebro(provider=proveedor, api_key=api_key or None)
        except Exception as error:
            self._agregar_mensaje(config.NOMBRE, f"No pude iniciar el modelo: {error}")
            self._estado("Revisá el proveedor y la API key y volvé a iniciar.")
            self.asistente_activo = False
            return
        error = cerebro.verificar()
        if error:
            self._agregar_mensaje(config.NOMBRE, error)
            self._estado("No se pudo iniciar. Corregí el error y volvé a abrir Jarvis.")
            self.asistente_activo = False
            return

        oido = None
        if self.modo == "voz":
            self._estado("Iniciando micrófono y reconocimiento de voz...")
            try:
                oido = voice.Oido(dispositivo=config.MICROFONO or None)
            except Exception as error:
                self._agregar_mensaje(config.NOMBRE, f"No pude iniciar la voz: {error}")
                self.modo = "texto"
                config.USAR_VOZ = False
                self._publicar_info()
                self._estado(
                    "Jarvis quedó en modo texto. Corregí el micrófono en Ajustes "
                    "y usá «Cambiar a voz» para volver a intentarlo."
                )
        self.oido = oido

        self._agregar_mensaje(config.NOMBRE, "En línea. Escribime abajo.")
        self._publicar_info()
        self._estado(
            "Micrófono activo. Hablá normalmente; Jarvis te escucha."
            if oido
            else "Modo texto listo."
        )
        if oido:
            self._iniciar_escucha(oido)

        while not self.cerrando.is_set():
            with self._pedidos_lock:
                try:
                    pedido, origen = self.pedidos.get_nowait()
                except queue.Empty:
                    pedido = origen = None
                else:
                    self._pedido_en_curso = True
                    tools.reiniciar_detencion()
            if pedido is None:
                time.sleep(0.2)
                continue
            self.bus.publish("detencion", activa=False, pendientes=0)
            if origen == "voz":
                self._agregar_mensaje("Tú (voz)", pedido)
            respuesta_modo = voice.cambiar_modo_silencio(pedido)
            if respuesta_modo:
                self._agregar_mensaje(config.NOMBRE, respuesta_modo)
                self._estado(
                    "Modo silencio activo; sigo escuchando."
                    if config.VOZ_SILENCIADA
                    else "Escuchando; hablá cuando quieras."
                )
                if not config.VOZ_SILENCIADA and self.modo == "voz":
                    self._hablar(respuesta_modo)
                with self._pedidos_lock:
                    self._pedido_en_curso = False
                    if self.pedidos.empty():
                        self._respuesta_lista.set()
                self.bus.publish("detencion", activa=False, pendientes=0)
                continue
            self._estado("Procesando...")
            try:
                try:
                    respuesta = cerebro.responder(pedido)
                except Exception as error:
                    respuesta = f"Tuve un problema: {error}"
                self._agregar_mensaje(config.NOMBRE, respuesta)
                error_voz = None
                if self.modo == "voz" and config.USAR_VOZ:
                    error_voz = self._hablar(respuesta)
                self._estado(
                    error_voz
                    or (
                        "Escuchando; hablá cuando quieras."
                        if self.modo == "voz"
                        else "Listo."
                    )
                )
            finally:
                with self._pedidos_lock:
                    self._pedido_en_curso = False
                    if self.pedidos.empty():
                        self._respuesta_lista.set()
                self.bus.publish("detencion", activa=False, pendientes=0)

    def _notificar(self, texto):
        self._agregar_mensaje(config.NOMBRE, texto)
        if self.modo == "voz" and config.USAR_VOZ:
            error = self._hablar(texto)
            if error:
                self._estado(error)

    def _hablar(self, texto):
        if config.VOZ_SILENCIADA:
            return None
        oido = getattr(self, "oido", None)
        self.bus.publish("estado", valor="hablando", texto="Respondiendo…")
        if oido:
            oido.pausar_escucha.set()
        try:
            if oido:
                with oido.bloqueo_audio:
                    resultado = voice.hablar(
                        texto,
                        api_key=self.elevenlabs_api_key,
                        oido=oido,
                    )
            else:
                resultado = voice.hablar(
                    texto, api_key=self.elevenlabs_api_key
                )
            if isinstance(resultado, voice.InterrupcionVoz):
                pedido = resultado.texto.strip()
                if pedido and not self.cerrando.is_set():
                    self._agregar_mensaje("Tú (voz)", pedido)
                    self._encolar_pedido(pedido, "voz")
                elif not self.cerrando.is_set():
                    self._estado("Te escuché, pero no pude reconocer lo que dijiste.")
                return None
            return resultado
        finally:
            if oido and self.modo == "voz" and config.USAR_VOZ:
                oido.pausar_escucha.clear()
            self.bus.publish("estado", valor="escuchando" if oido else "espera", texto="")

    def _iniciar_escucha(self, oido):
        if self._escucha_hilo and self._escucha_hilo.is_alive():
            return
        self._escucha_hilo = threading.Thread(
            target=self._escuchar, args=(oido,), daemon=True
        )
        self._escucha_hilo.start()

    def _escuchar(self, oido):
        while not self.cerrando.is_set():
            if self.modo != "voz" or self.oido is not oido:
                time.sleep(0.1)
                continue
            try:
                pedido = oido.escuchar_continuo()
                if (
                    self.modo != "voz"
                    or self.oido is not oido
                    or self.cerrando.is_set()
                ):
                    continue
                if not pedido or self.cerrando.is_set():
                    continue

                self._encolar_pedido(pedido, "voz")
                while (
                    not self._respuesta_lista.wait(0.1)
                    and not self.cerrando.is_set()
                    and self.modo == "voz"
                    and self.oido is oido
                ):
                    pass
            except Exception as error:
                with self._modo_lock:
                    es_escucha_actual = self.oido is oido
                    if es_escucha_actual:
                        self.oido = None
                    if es_escucha_actual and self.modo == "voz":
                        self.modo = "texto"
                        config.USAR_VOZ = False
                    if self._escucha_hilo is threading.current_thread():
                        self._escucha_hilo = None
                self._publicar_info()
                self._agregar_mensaje(
                    config.NOMBRE,
                    f"Se interrumpió el micrófono: {error}. Podés seguir por texto "
                    "y volver a intentar con «Cambiar a voz».",
                )
                self._estado("Jarvis quedó en modo texto.")
                return

    def alternar_modo(self):
        if not self.asistente_activo:
            return self.iniciar(self.modo == "voz")

        if self.modo == "voz":
            with self._modo_lock:
                self.modo = "texto"
                config.USAR_VOZ = False
                if self.oido:
                    self.oido.pausar_escucha.set()
            self._publicar_info()
            self._estado("Modo texto activo. El micrófono quedó en pausa.")
            return True

        try:
            if config.PROVEEDOR_TTS == "elevenlabs":
                self.elevenlabs_api_key = self._clave_guardada_o_entorno(
                    "elevenlabs"
                )
                if not self.elevenlabs_api_key:
                    self._agregar_mensaje(
                        config.NOMBRE,
                        "Falta la API key de ElevenLabs. Guardala en Ajustes "
                        "o elegí Piper/Windows para la voz de respuesta.",
                    )
                    self.bus.publish("ajustes")
                    return False
        except Exception as error:
            self._agregar_mensaje(
                config.NOMBRE, f"No pude leer la clave de voz guardada: {error}"
            )
            return False

        with self._modo_lock:
            self.modo = "voz"
            config.USAR_VOZ = True
            if self.oido:
                self.oido.pausar_escucha.clear()
                self._iniciar_escucha(self.oido)
                iniciar = False
            elif self._voz_iniciando:
                iniciar = False
            else:
                self._voz_iniciando = True
                iniciar = True
        self._publicar_info()
        self._estado("Activando micrófono y reconocimiento de voz…")
        if iniciar:
            threading.Thread(target=self._activar_voz, daemon=True).start()
        return True

    def _activar_voz(self):
        try:
            oido = voice.Oido(dispositivo=config.MICROFONO or None)
        except Exception as error:
            with self._modo_lock:
                self._voz_iniciando = False
                if self.modo == "voz":
                    self.modo = "texto"
                    config.USAR_VOZ = False
            self._publicar_info()
            self._agregar_mensaje(
                config.NOMBRE,
                f"No pude activar el micrófono: {error}. Revisá la entrada "
                "en Ajustes; Jarvis sigue disponible en modo texto.",
            )
            self._estado("Modo texto activo. Corregí el micrófono y reintentá.")
            return

        with self._modo_lock:
            self._voz_iniciando = False
            if self.cerrando.is_set() or self.modo != "voz":
                oido.pausar_escucha.set()
                return
            self.oido = oido
            oido.pausar_escucha.clear()
            self._iniciar_escucha(oido)
        self._publicar_info()
        self._estado("Micrófono activo. Hablá normalmente; Jarvis te escucha.")

    # ------------------------------------------------------------------ HUD
    def _boton_hud(self):
        if self.asistente_activo:
            self._abrir_hud(forzar=True)
        else:
            self.iniciar(True)

    def abrir_hud_inicial(self):
        """Al abrir la app: muestra solo el HUD con sus botones de inicio."""
        if not self._abrir_hud(inicial=True):
            messagebox.showerror(
                config.NOMBRE,
                "No se pudo abrir la interfaz HUD. Verificá que Microsoft Edge o Google Chrome esté instalado.",
            )

    def _iniciar_desde_hud(self, solo_texto):
        if not self.iniciar(solo_texto):
            self._mostrar_ajustes_desde_hud()  # falta configurar algo: mostramos los ajustes

    def desde_hud(self, texto):
        texto = texto.strip()
        if not texto:
            return
        self._agregar_mensaje("Tú", texto)
        if not self.asistente_activo and not self.iniciar(True):
            return
        self._encolar_pedido(texto, "texto")

    def _configuracion_hud(self):
        dispositivos = sd.query_devices()
        apis = sd.query_hostapis()
        microfonos = []
        salidas = []
        for indice, dispositivo in enumerate(dispositivos):
            api = apis[dispositivo["hostapi"]]["name"]
            etiqueta = (
                f'{indice}: {dispositivo["name"]} ({api}, '
                f'{int(dispositivo["default_samplerate"])} Hz)'
            )
            if (
                dispositivo["max_input_channels"]
                and api.casefold() != "windows wdm-ks"
            ):
                microfonos.append({"id": indice, "label": etiqueta})
            if dispositivo["max_output_channels"]:
                salidas.append({"id": indice, "label": etiqueta})

        def seleccionar_dispositivo(valor, opciones):
            if valor in (None, ""):
                return ""
            if isinstance(valor, dict):
                indice = voice.Oido._elegir_microfono_con(sd, valor)
                return indice if any(opcion["id"] == indice for opcion in opciones) else ""
            try:
                indice = int(valor)
            except (TypeError, ValueError):
                texto = str(valor).casefold()
                return next(
                    (
                        opcion["id"]
                        for opcion in opciones
                        if texto in dispositivos[opcion["id"]]["name"].casefold()
                    ),
                    "",
                )
            return indice if any(opcion["id"] == indice for opcion in opciones) else ""

        proveedor = config.PROVEEDOR_IA
        microfono_configurado = config.MICROFONO
        if microfono_configurado in (None, ""):
            microfono_configurado = voice.Oido._elegir_microfono_con(sd)
        return {
            "microfonos": microfonos,
            "salidas": salidas,
            "microfono": seleccionar_dispositivo(microfono_configurado, microfonos),
            "salida": seleccionar_dispositivo(config.AUDIO_SALIDA, salidas),
            "proveedor": proveedor,
            "api_key_guardada": self._clave_local_guardada(proveedor),
            "proveedor_tts": config.PROVEEDOR_TTS,
            "elevenlabs_key_guardada": self._clave_local_guardada("elevenlabs"),
            "elevenlabs_voice_id": config.ELEVENLABS_VOICE_ID,
            "github_token_guardado": self._clave_local_guardada("github"),
            "spotify_client_id": config.SPOTIFY_CLIENT_ID,
            "ubicacion": config.UBICACION,
            "vision_modelo": config.MODELO_VISION,
            "modelo_ollama": config.MODELO,
            "modelo_gemini": config.MODELO_GEMINI,
            "modelo_openai": config.MODELO_OPENAI,
            "ollama_host": config.OLLAMA_HOST,
            "modelo_whisper": config.WHISPER_MODELO,
            "idioma": config.IDIOMA,
            "volumen_voz": config.VOLUMEN_VOZ,
            "velocidad_voz": config.VELOCIDAD_VOZ,
            "volumen_efectos": config.VOLUMEN_EFECTOS,
            "tema_hud": config.TEMA_HUD,
        }

    def _guardar_configuracion_hud(self, datos):
        proveedores = {"ollama", "gemini", "openai"}
        voces = {"piper", "windows", "elevenlabs"}
        proveedor = str(datos.get("proveedor", "")).lower()
        proveedor_tts = str(datos.get("proveedor_tts", "")).lower()
        if proveedor not in proveedores or proveedor_tts not in voces:
            raise ValueError("Elegí un proveedor de IA y una voz disponibles.")

        dispositivos = sd.query_devices()
        apis = sd.query_hostapis()

        def elegir_dispositivo(campo, canales):
            valor = datos.get(campo)
            if valor in (None, ""):
                return None if canales == "output" else ""
            try:
                indice = int(valor)
            except (TypeError, ValueError) as error:
                raise ValueError(f"El dispositivo de {canales} seleccionado no es válido.") from error
            if not 0 <= indice < len(dispositivos) or not dispositivos[indice][f"max_{canales}_channels"]:
                raise ValueError(f"El dispositivo de {canales} seleccionado ya no está disponible.")
            if (
                canales == "input"
                and apis[dispositivos[indice]["hostapi"]]["name"].casefold()
                == "windows wdm-ks"
            ):
                raise ValueError(
                    "Esa entrada usa WDM-KS, que no es compatible de forma estable. "
                    "Elegí la misma entrada con WASAPI o DirectSound."
                )
            return indice

        microfono = elegir_dispositivo("microfono", "input")
        if microfono in (None, ""):
            microfono_guardado = ""
        else:
            dispositivo = dispositivos[microfono]
            microfono_guardado = {
                "name": dispositivo["name"],
                "hostapi": apis[dispositivo["hostapi"]]["name"],
            }
        salida = elegir_dispositivo("salida", "output")
        voice_id = str(datos.get("elevenlabs_voice_id", "")).strip()[:200]
        spotify_client_id = str(datos.get("spotify_client_id", "")).strip()[:200]
        ubicacion = str(datos.get("ubicacion", "")).strip()[:200]
        vision_modelo = str(datos.get("vision_modelo", "")).strip()[:200]
        if not vision_modelo:
            raise ValueError("Indicá el nombre del modelo visual.")
        modelo_ollama = str(datos.get("modelo_ollama", "")).strip()[:200]
        modelo_gemini = str(datos.get("modelo_gemini", "")).strip()[:200]
        modelo_openai = str(datos.get("modelo_openai", "")).strip()[:200]
        ollama_host = str(datos.get("ollama_host", "")).strip()[:500]
        modelo_whisper = str(datos.get("modelo_whisper", "")).strip()[:100]
        idioma = str(datos.get("idioma", "")).strip()[:10]
        if not all((modelo_ollama, modelo_gemini, modelo_openai, ollama_host, modelo_whisper, idioma)):
            raise ValueError("Completá los modelos de IA, Ollama, Whisper y el idioma.")
        if not ollama_host.startswith(("http://", "https://")):
            raise ValueError("La dirección de Ollama debe comenzar con http:// o https://.")
        volumen_voz = self._validar_preferencia_entera(
            datos, "volumen_voz", config.VOLUMEN_VOZ, 0, 100
        )
        velocidad_voz = self._validar_preferencia_entera(
            datos, "velocidad_voz", config.VELOCIDAD_VOZ, 70, 130
        )
        volumen_efectos = self._validar_preferencia_entera(
            datos, "volumen_efectos", config.VOLUMEN_EFECTOS, 0, 100
        )
        tema_hud = str(datos.get("tema_hud", config.TEMA_HUD)).lower()
        if tema_hud not in {"cian", "violeta", "verde"}:
            raise ValueError("Elegí un tema visual disponible.")
        clave_ia = str(datos.get("api_key", "")).strip()
        clave_elevenlabs = str(datos.get("elevenlabs_api_key", "")).strip()
        token_github = str(datos.get("github_token", "")).strip()

        if clave_ia:
            self._guardar_clave(proveedor, clave_ia)
        elif datos.get("borrar_api_key"):
            self._eliminar_clave(proveedor)
        if clave_elevenlabs:
            self._guardar_clave("elevenlabs", clave_elevenlabs)
        elif datos.get("borrar_elevenlabs_key"):
            self._eliminar_clave("elevenlabs")
        if token_github:
            self._guardar_clave("github", token_github)
        elif datos.get("borrar_github_token"):
            self._eliminar_clave("github")

        requiere_reinicio = self.asistente_activo and any(
            nuevo != actual
            for nuevo, actual in (
                (microfono_guardado, config.MICROFONO),
                (salida, config.AUDIO_SALIDA),
                (proveedor, config.PROVEEDOR_IA),
                (modelo_ollama, config.MODELO),
                (modelo_gemini, config.MODELO_GEMINI),
                (modelo_openai, config.MODELO_OPENAI),
                (ollama_host, config.OLLAMA_HOST),
                (modelo_whisper, config.WHISPER_MODELO),
                (idioma, config.IDIOMA),
            )
        )
        config.MICROFONO = microfono_guardado
        config.AUDIO_SALIDA = salida
        config.PROVEEDOR_IA = proveedor
        config.PROVEEDOR_TTS = proveedor_tts
        config.ELEVENLABS_VOICE_ID = voice_id
        config.SPOTIFY_CLIENT_ID = spotify_client_id
        config.UBICACION = ubicacion
        config.MODELO_VISION = vision_modelo
        config.MODELO = modelo_ollama
        config.MODELO_GEMINI = modelo_gemini
        config.MODELO_OPENAI = modelo_openai
        config.OLLAMA_HOST = ollama_host
        config.WHISPER_MODELO = modelo_whisper
        config.IDIOMA = idioma
        config.VOLUMEN_VOZ = volumen_voz
        config.VELOCIDAD_VOZ = velocidad_voz
        config.VOLUMEN_EFECTOS = volumen_efectos
        config.TEMA_HUD = tema_hud
        config.GITHUB_TOKEN = self._clave_guardada_o_entorno("github")
        config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
        ruta = config.CARPETA_DATOS / "dispositivos_audio.json"
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(
                {
                    "microfono": microfono_guardado,
                    "salida": salida,
                    "proveedor": proveedor,
                    "proveedor_tts": proveedor_tts,
                    "tts_version": 1,
                    "elevenlabs_voice_id": voice_id,
                    "ubicacion": ubicacion,
                    "spotify_client_id": spotify_client_id,
                    "vision_modelo": vision_modelo,
                    "modelo_ollama": modelo_ollama,
                    "modelo_gemini": modelo_gemini,
                    "modelo_openai": modelo_openai,
                    "ollama_host": ollama_host,
                    "modelo_whisper": modelo_whisper,
                    "idioma": idioma,
                    "volumen_voz": volumen_voz,
                    "velocidad_voz": velocidad_voz,
                    "volumen_efectos": volumen_efectos,
                    "tema_hud": tema_hud,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporal.replace(ruta)
        self._publicar_info()
        configuracion = self._configuracion_hud()
        configuracion["requiere_reinicio"] = requiere_reinicio
        return configuracion

    def _probar_audio_hud(self, prueba):
        import numpy as np

        if prueba == "microfono":
            nombre, indice, frecuencia, niveles, desbordados = voice.medir_microfono(
                config.MICROFONO or None
            )
            nivel = max(niveles, default=0)
            if nivel < 0.001:
                return (
                    f"No se detectó audio en {nombre} (dispositivo {indice}). "
                    "Revisá el permiso de micrófono de Windows o elegí otra entrada."
                )
            aviso = " Se detectaron cortes de audio." if any(desbordados) else ""
            return f"Micrófono detectado: {nombre} ({frecuencia} Hz, nivel máximo {nivel:.4f}).{aviso}"
        if prueba == "reconocimiento":
            return voice.probar_reconocimiento(config.MICROFONO or None)
        if prueba == "salida":
            dispositivo = config.AUDIO_SALIDA
            info = sd.query_devices(dispositivo, "output") if dispositivo is not None else sd.query_devices(kind="output")
            frecuencia = int(info["default_samplerate"])
            tiempo = np.arange(int(frecuencia * 0.5)) / frecuencia
            tono = (0.15 * np.sin(2 * np.pi * 660 * tiempo)).astype(np.float32)
            sd.play(tono, samplerate=frecuencia, device=dispositivo, blocking=True)
            return f"Sonido de prueba reproducido por {info['name']}."
        if prueba == "voz":
            usar_voz = config.USAR_VOZ
            config.USAR_VOZ = True
            try:
                resultado = voice.hablar(
                    "Hola, soy Jarvis. Esta es una prueba de voz.",
                    api_key=self._clave_guardada_o_entorno("elevenlabs"),
                )
            finally:
                config.USAR_VOZ = usar_voz
            return resultado or "Prueba de voz completada."
        raise ValueError("Prueba de audio desconocida.")

    def _probar_github_hud(self):
        token = config.GITHUB_TOKEN
        if not token:
            raise ValueError("Pegá y guardá primero un token de GitHub.")
        import httpx

        respuesta = httpx.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        if respuesta.status_code in (401, 403):
            raise ValueError(
                f"GitHub rechazó el token o sus permisos (HTTP {respuesta.status_code})."
            )
        respuesta.raise_for_status()
        usuario = respuesta.json().get("login", "usuario")
        return f"Conectado como {usuario}. Búsquedas de solo lectura."

    def _detectar_ubicacion_hud(self):
        if not self.bus.preguntar(
            "Jarvis enviará tu dirección IP a ipapi.co para estimar ciudad y región. "
            "La ubicación puede ser imprecisa. ¿Querés continuar?",
            timeout=60,
        ):
            raise ValueError("No se consultó la ubicación.")
        import httpx

        respuesta = httpx.get(
            "https://ipapi.co/json/",
            timeout=httpx.Timeout(8.0, connect=4.0),
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
        if datos.get("error"):
            raise RuntimeError("El servicio de ubicación no devolvió resultados.")
        ubicacion = ", ".join(
            parte
            for parte in (datos.get("city"), datos.get("region"), datos.get("country_name"))
            if parte
        )
        if not ubicacion:
            raise RuntimeError("El servicio no devolvió una ciudad aproximada.")
        configuracion = self._configuracion_hud()
        configuracion.update(
            ubicacion=ubicacion,
            api_key="",
            elevenlabs_api_key="",
            github_token="",
        )
        self._guardar_configuracion_hud(configuracion)
        return ubicacion

    def _publicar_info(self):
        nombres_ia = {"ollama": "Ollama", "gemini": "Gemini", "openai": "OpenAI"}
        nombres_tts = {"piper": "Piper", "windows": "Windows", "elevenlabs": "ElevenLabs"}

        self.bus.publish(
            "info",
            nombre=config.NOMBRE,
            modo=self.modo or "texto",
            ia=nombres_ia.get(config.PROVEEDOR_IA, config.PROVEEDOR_IA),
            tts=nombres_tts.get(config.PROVEEDOR_TTS, config.PROVEEDOR_TTS),
            github=bool(config.GITHUB_TOKEN),
            spotify=bool(spotyfyTools and spotyfyTools.configurado()),
            vision=bool(config.COMPARTIENDO_PANTALLA),
            tema=config.TEMA_HUD,
            volumen_efectos=config.VOLUMEN_EFECTOS,
        )

    def _accion_hud(self, nombre):
        """Acciones que pide el HUD (corren en un hilo del servidor)."""
        if nombre == "pantalla_sync":
            self.raiz.after(0, lambda: self.compartir_pantalla_var.set(config.COMPARTIENDO_PANTALLA))
            self._publicar_info()
        elif nombre == "ajustes":
            self.bus.publish("ajustes")
        elif nombre == "preparar_vision":
            self.raiz.after(0, self.preparar_vision)
        elif nombre in ("iniciar_voz", "iniciar_texto"):
            self.raiz.after(0, lambda: self._iniciar_desde_hud(nombre == "iniciar_texto"))
        elif nombre == "alternar_modo":
            self.raiz.after(0, self.alternar_modo)
        elif nombre == "detener_automatizacion":
            self._detener_automatizacion()
        elif nombre == "salir":
            self.raiz.after(0, self.cerrar)

    def _detener_automatizacion(self):
        import tools

        descartados = 0
        with self._pedidos_lock:
            tools.solicitar_detencion()
            while True:
                try:
                    self.pedidos.get_nowait()
                    descartados += 1
                except queue.Empty:
                    break
            en_curso = self._pedido_en_curso
            if not en_curso:
                self._respuesta_lista.set()
        try:
            tools.registrar_accion("detener_automatizacion", "solicitada")
            aviso_registro = ""
        except OSError as error:
            aviso_registro = (
                f" Advertencia: no pude guardar esta detención en el registro ({error})."
            )
        self.bus.responder(False)
        self.bus.publish(
            "detencion",
            activa=en_curso,
            pendientes=descartados,
        )
        self._agregar_mensaje(
            config.NOMBRE,
            (
                "Detención solicitada. Pararé entre acciones y descarté "
                f"{descartados} pedido(s) pendiente(s).{aviso_registro}"
                if en_curso
                else (
                    "No había una automatización en curso; descarté "
                    f"{descartados} pedido(s) pendiente(s).{aviso_registro}"
                )
            ),
        )
        if en_curso:
            self._estado(
                "Detención solicitada. Esperando que termine la operación actual..."
            )
        else:
            self._estado("Sin automatizaciones pendientes.")

    def _mostrar_ajustes_desde_hud(self):
        self.bus.publish("ajustes")

    def _abrir_hud(self, inicial=False, forzar=False):
        if self.hud_servidor is None:
            accion_spotify = spotyfyTools.control if spotyfyTools else None
            servidor = ui.Servidor(
                self.bus,
                _EntradasHud(self),
                accion_spotify=accion_spotify,
                accion_app=self._accion_hud,
                leer_configuracion=self._configuracion_hud,
                guardar_configuracion=self._guardar_configuracion_hud,
                probar_audio=self._probar_audio_hud,
                probar_github=self._probar_github_hud,
                detectar_ubicacion=self._detectar_ubicacion_hud,
            )
            try:
                servidor.iniciar()
            except OSError as error:
                self._estado_visible(f"No pude iniciar el HUD: {error}")
                return False
            self.hud_servidor = servidor
            if spotyfyTools:
                spotyfyTools.iniciar_si_hay_sesion()
        if not inicial:
            self._publicar_info()
        if self.bus.hay_hud() and not forzar:
            return True  # ya hay una ventana del HUD abierta
        if not self._lanzar_ventana(self.hud_servidor.url):
            self._estado_visible("No pude abrir un navegador para mostrar el HUD.")
            return False
        self._hud_lanzado_en = time.monotonic()
        self.raiz.withdraw()  # una sola interfaz a la vista: el HUD
        if not self._hud_vigilando:
            self._hud_vigilando = True
            self.raiz.after(3000, self._hud_vigilar)
        return True

    @staticmethod
    def _lanzar_ventana(url):
        """Abre el HUD como ventana de aplicación (sin barra de direcciones) en Edge o Chrome."""
        candidatos = [
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for ruta in candidatos:
            if ruta and Path(ruta).is_file():
                try:
                    subprocess.Popen(
                        [
                            ruta,
                            f"--app={url}",
                            "--window-size=1520,920",
                            "--window-position=50,30",
                        ]
                    )
                except OSError:
                    continue
                return True
        return bool(webbrowser.open(url))

    def _hud_vigilar(self):
        """Mantiene el proceso oculto mientras la ventana del HUD esté abierta."""
        if self.cerrando.is_set():
            return
        if self.bus.hay_hud():
            self._hud_visto = True
            self._hud_ausente = None
            self._hud_lanzado_en = None
        elif not self._hud_visto and self._hud_lanzado_en and time.monotonic() - self._hud_lanzado_en > 25:
            self._hud_lanzado_en = None
            messagebox.showerror(
                config.NOMBRE,
                "No se pudo conectar con el HUD. Cerrá el aviso y volvé a iniciar Jarvis.",
            )
            self.cerrar()
            return
        elif self._hud_visto:
            if self._hud_ausente is None:
                self._hud_ausente = time.monotonic()
            elif time.monotonic() - self._hud_ausente > 20:
                self._hud_visto = False
                self._hud_ausente = None
                self.cerrar()
                return
        self.raiz.after(2000, self._hud_vigilar)

    def cerrar(self):
        with self._pedidos_lock:
            tools = sys.modules.get("tools")
            if tools is not None:
                tools.solicitar_detencion()
            while True:
                try:
                    self.pedidos.get_nowait()
                except queue.Empty:
                    break
        self.cerrando.set()
        self.raiz.destroy()


def _instancia_unica():
    """Evita abrir dos Jarvis a la vez (se pelearían por el micrófono y el HUD)."""
    import socket

    candado = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        candado.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        candado.bind(("127.0.0.1", 48621))
        candado.listen(1)
    except OSError:
        candado.close()
        return None
    return candado


def main():
    raiz = tk.Tk()
    raiz.withdraw()  # no mostrar esta ventana: la interfaz principal es el HUD
    candado = _instancia_unica()
    if candado is None:
        messagebox.showinfo(
            config.NOMBRE,
            f"{config.NOMBRE} ya está en ejecución.\n\nSi no ves su ventana, cerralo desde el "
            "Administrador de tareas (procesos «Python») y volvé a abrir run.vbs.",
        )
        raiz.destroy()
        return
    lanzador = Lanzador(raiz)
    lanzador._candado = candado  # se mantiene abierto mientras viva el programa
    raiz.after(300, lanzador.abrir_hud_inicial)
    raiz.mainloop()


if __name__ == "__main__":
    main()
