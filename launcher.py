"""Controlador del HUD web y de los subsistemas de Jarvis."""
import argparse
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

if __name__ == "__main__" and not getattr(sys, "frozen", False):
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
from tkinter import messagebox, simpledialog, ttk

import config
import ai_providers
import connections_catalog
import preferencias_usuario
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

    def put_mensaje(self, texto, solicitud_id):
        if not self.app.desde_hud(texto, solicitud_id=solicitud_id):
            raise RuntimeError("El asistente no aceptó el mensaje.")


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
        self.live_activo = False
        self.live_silenciado = False
        self._live_cancelar_generacion = threading.Event()
        self.bus = ui.Bus()
        self.hud_servidor = None
        self._hud_visto = False
        self._hud_ausente = None
        self._hud_vigilando = False
        self._hud_lanzado_en = None
        self.modo = None
        self.ia_estado = "sin_verificar"
        self.ia_error = ""
        self.ia_proveedor_activo = config.PROVEEDOR_IA
        self.vision_error = ""
        self.ultima_latencia_ms = None
        self.vision_descargando = False
        self.asistente_activo = False
        self.oido = None
        self._modo_lock = threading.Lock()
        self._voz_iniciando = False
        self._escucha_hilo = None
        self.cerebro = None
        self._cerebro_ready = threading.Event()
        self._cerebro_lock = threading.RLock()
        self._live_manager = None
        self._live_thread = None
        self._live_previous_mode = "texto"
        self._live_state = "idle"
        self._live_providers = []
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
            config.MODELO_CURSOR,
            config.PROVEEDOR_IA_ALTERNATIVO,
            config.OLLAMA_HOST,
            config.WHISPER_MODELO,
            config.IDIOMA,
            config.VOLUMEN_VOZ,
            config.VELOCIDAD_VOZ,
            config.VOLUMEN_EFECTOS,
            config.TEMA_HUD,
            config.MODO_ENSEMBLE,
            config.ESTRATEGIA_ENSEMBLE,
            config.MODELO_GROQ_API,
            config.MODELO_CLOUDFLARE,
            config.CLOUDFLARE_ACCOUNT_ID,
            config.CARTESIA_VOICE_ID,
            config.MICROFONO_SENSIBILIDAD,
            config.INICIO_AUTOMATICO,
        ) = self._leer_seleccion()
        try:
            config.GITHUB_TOKEN = self._clave_guardada_o_entorno("github")
            self.cartesia_api_key = self._clave_guardada_o_entorno("cartesia")
        except Exception as error:
            messagebox.showerror(
                "Credenciales no disponibles",
                f"No pude leer las credenciales guardadas en Windows:\n{error}",
                parent=raiz,
            )
            config.GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
            self.cartesia_api_key = self._clave_entorno("cartesia")
        import tools

        config.COMPARTIENDO_PANTALLA = tools.permiso_control_pc() is True
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
            "Groq": "groq",
            "Cloudflare Workers AI": "cloudflare",
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
            "Cartesia": "cartesia",
        }
        self.proveedor_tts_var = tk.StringVar(
            value=next(
                (label for label, code in self.proveedores_tts.items() if code == proveedor_tts),
                "Windows (local)",
            )
        )
        self.elevenlabs_api_key_var = tk.StringVar(value=clave_elevenlabs)
        try:
            clave_cartesia = self._clave_guardada_o_entorno("cartesia")
        except Exception as error:
            messagebox.showerror(
                "No se pudo leer la API key de Cartesia",
                f"No pude leer la clave del Administrador de credenciales de Windows:\n{error}",
                parent=raiz,
            )
            clave_cartesia = self._clave_entorno("cartesia")
        self.cartesia_api_key_var = tk.StringVar(value=clave_cartesia)
        self.elevenlabs_api_key = ""
        self.cartesia_api_key = ""
        self.elevenlabs_voice_id_var = tk.StringVar(
            value=config.ELEVENLABS_VOICE_ID
        )
        self.cartesia_voice_id_var = tk.StringVar(value=config.CARTESIA_VOICE_ID)

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
        ttk.Label(principal, text="API key de Cartesia (se guarda en Windows)").grid(
            row=11, column=0, sticky="w", pady=4
        )
        self.cartesia_api_key_entry = ttk.Entry(
            principal, textvariable=self.cartesia_api_key_var, show="*", width=56
        )
        self.cartesia_api_key_entry.grid(row=11, column=1, sticky="ew", pady=4)
        ttk.Label(principal, text="Voice ID de Cartesia").grid(
            row=12, column=0, sticky="w", pady=4
        )
        self.cartesia_voice_id_entry = ttk.Entry(
            principal, textvariable=self.cartesia_voice_id_var, width=56
        )
        self.cartesia_voice_id_entry.grid(row=12, column=1, sticky="ew", pady=4)
        ttk.Label(
            principal,
            text="API key y Voice ID desde play.cartesia.ai. Si no están configurados, Jarvis usa Piper.",
        ).grid(row=13, column=0, columnspan=2, sticky="w")
        ttk.Label(
            principal,
            text=f"Voz local predeterminada: Claude ({config.PIPER_MODELO_VOZ}), gratis y sin conexión.",
        ).grid(row=14, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            principal,
            text="Permitir que Jarvis analice la pantalla cuando se lo pida (captura bajo demanda)",
            variable=self.compartir_pantalla_var,
            command=self._cambiar_permiso_pantalla,
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            principal,
            text=f"Descargar modelo visual (~3 GB): {config.MODELO_VISION}",
            command=self.preparar_vision,
        ).grid(row=16, column=0, columnspan=2, sticky="w", pady=(4, 0))

        acciones = ttk.Frame(principal)
        acciones.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(16, 8))
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
            command=self.iniciar,
            style="Accent.TButton",
        ).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )

        self.estado = tk.StringVar(value="Seleccioná los dispositivos y probalos antes de iniciar.")
        ttk.Label(principal, textvariable=self.estado, wraplength=780).grid(
            row=18, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            principal,
            text="La prueba del micrófono dura 5 segundos: hablá durante la medición.",
        ).grid(row=19, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.api_key_entry.bind("<FocusOut>", self._guardar_claves_al_perder_foco)
        self.elevenlabs_api_key_entry.bind("<FocusOut>", self._guardar_claves_al_perder_foco)
        self.cartesia_api_key_entry.bind("<FocusOut>", self._guardar_claves_al_perder_foco)

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
            command=self.iniciar,
        ).grid(row=0, column=0, padx=5)
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
        self.estado_chat = tk.StringVar(value="Hablale a Jarvis; el historial queda visible aquí.")
        ttk.Label(
            chat,
            textvariable=self.estado_chat,
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
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
            "groq": "GROQ_API_KEY",
            "cloudflare": "CLOUDFLARE_API_KEY",
            "cursor": "CURSOR_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY",
            "cartesia": "CARTESIA_API_KEY",
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

    def _clave_para_core(self, proveedor):
        try:
            return self._clave_guardada_o_entorno(proveedor)
        except Exception as error:
            mensaje = f"No pude leer la credencial de {proveedor} en Windows: {error}"
            print(f"[credenciales] {mensaje}")
            if proveedor == config.PROVEEDOR_IA:
                self.ia_error = mensaje
            return self._clave_entorno(proveedor)

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
        self._guardar_clave(
            "cartesia", self.cartesia_api_key_var.get().strip()
        )
        token_github = self.github_token_var.get().strip()
        self._guardar_clave("github", token_github)
        config.GITHUB_TOKEN = token_github

    def _guardar_claves_al_perder_foco(self, evento):
        if evento.widget not in (
            self.api_key_entry,
            self.elevenlabs_api_key_entry,
            self.cartesia_api_key_entry,
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
                config.MODELO_CURSOR,
                config.PROVEEDOR_IA_ALTERNATIVO,
                config.OLLAMA_HOST,
                config.WHISPER_MODELO,
                config.IDIOMA,
                config.VOLUMEN_VOZ,
                config.VELOCIDAD_VOZ,
                config.VOLUMEN_EFECTOS,
                config.TEMA_HUD,
                config.MODO_ENSEMBLE,
                config.ESTRATEGIA_ENSEMBLE,
                config.MODELO_GROQ_API,
                config.MODELO_CLOUDFLARE,
                config.CLOUDFLARE_ACCOUNT_ID,
                config.CARTESIA_VOICE_ID,
                config.MICROFONO_SENSIBILIDAD,
                config.INICIO_AUTOMATICO,
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
                config.MODELO_CURSOR,
                config.PROVEEDOR_IA_ALTERNATIVO,
                config.OLLAMA_HOST,
                config.WHISPER_MODELO,
                config.IDIOMA,
                config.VOLUMEN_VOZ,
                config.VELOCIDAD_VOZ,
                config.VOLUMEN_EFECTOS,
                config.TEMA_HUD,
                config.MODO_ENSEMBLE,
                config.ESTRATEGIA_ENSEMBLE,
                config.MODELO_GROQ_API,
                config.MODELO_CLOUDFLARE,
                config.CLOUDFLARE_ACCOUNT_ID,
                config.CARTESIA_VOICE_ID,
                config.MICROFONO_SENSIBILIDAD,
                config.INICIO_AUTOMATICO,
            )
        modelos_rotos = {
            "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash",
            "gemini-3.8-flash", "gemini-4-argon", "gemini-2.5-flash", "gemini-2.0-flash",
            "grok-4.7", "grok-4.6", "grok-4.5", "grok-4.3", "grok-3-mini",
            "gpt-6-luna", "gpt-6-astra", "gpt-6.1-sol",
            "@cf/zai-org/glm-4.7-flash",
            "llama-3.3-70b-versatile",
        }
        def _modelo(clave, predeterminado):
            valor = str(datos.get(clave, predeterminado) or predeterminado).strip()
            return predeterminado if valor in modelos_rotos else valor

        modelo_ollama = datos.get("modelo_ollama", config.MODELO)
        if modelo_ollama == "qwen2.5:7b":
            modelo_ollama = config.MODELO
        modelo_whisper = datos.get("modelo_whisper", config.WHISPER_MODELO)
        if modelo_whisper == "small":
            modelo_whisper = config.WHISPER_MODELO
        estrategia_ensemble = datos.get(
            "estrategia_ensemble", config.ESTRATEGIA_ENSEMBLE
        )
        if estrategia_ensemble == "mejor":
            estrategia_ensemble = config.ESTRATEGIA_ENSEMBLE

        return (
            datos.get("microfono", config.MICROFONO),
            datos.get("salida", config.AUDIO_SALIDA),
            datos.get("proveedor", config.PROVEEDOR_IA),
            (
                config.PROVEEDOR_TTS
                if datos.get("tts_version", 0) < 2
                else datos.get("proveedor_tts", config.PROVEEDOR_TTS)
            ),
            datos.get("elevenlabs_voice_id", config.ELEVENLABS_VOICE_ID),
            datos.get("ubicacion", config.UBICACION),
            datos.get("spotify_client_id", config.SPOTIFY_CLIENT_ID),
            datos.get("vision_modelo", config.MODELO_VISION),
            modelo_ollama,
            _modelo("modelo_gemini", config.MODELO_GEMINI),
            _modelo("modelo_openai", config.MODELO_OPENAI),
            _modelo("modelo_cursor", config.MODELO_CURSOR),
            (
                datos.get("proveedor_ia_alternativo", config.PROVEEDOR_IA_ALTERNATIVO)
                if datos.get("proveedor_ia_alternativo", config.PROVEEDOR_IA_ALTERNATIVO)
                in {"", "gemini", "openai", "groq", "cloudflare", "cursor"}
                else ""
            ),
            datos.get("ollama_host", config.OLLAMA_HOST),
            modelo_whisper,
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
            (
                datos["modo_ensemble"]
                if datos.get("modo_ensemble") in {"auto", "on", "off"}
                else config.MODO_ENSEMBLE
            ),
            (
                estrategia_ensemble
                if estrategia_ensemble in {"mejor", "rapido", "verificar", "votar"}
                else config.ESTRATEGIA_ENSEMBLE
            ),
            _modelo("modelo_groq_api", config.MODELO_GROQ_API),
            _modelo("modelo_cloudflare", config.MODELO_CLOUDFLARE),
            str(datos.get("cloudflare_account_id", config.CLOUDFLARE_ACCOUNT_ID))[:100],
            str(datos.get("cartesia_voice_id", config.CARTESIA_VOICE_ID))[:200],
            Lanzador._leer_preferencia_entera(
                datos, "microfono_sensibilidad", config.MICROFONO_SENSIBILIDAD, 0, 100
            ),
            (
                datos.get("inicio_automatico")
                if isinstance(datos.get("inicio_automatico"), bool)
                else config.INICIO_AUTOMATICO
            ),
        )

    @staticmethod
    def _actualizar_inicio_automatico(activar):
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("Windows no proporcionó la carpeta de inicio del usuario.")
        carpeta = (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
        acceso = carpeta / "Jarvis.vbs"
        if not activar:
            if acceso.exists():
                acceso.unlink()
            return

        carpeta.mkdir(parents=True, exist_ok=True)
        ejecutable = Path(sys.executable)
        if getattr(sys, "frozen", False):
            comando = f'"{ejecutable}" --background'
        else:
            ejecutable_sin_consola = ejecutable.with_name("pythonw.exe")
            if ejecutable_sin_consola.is_file():
                ejecutable = ejecutable_sin_consola
            comando = f'"{ejecutable}" "{Path(__file__).resolve()}" --background'
        carpeta_trabajo = str(Path(__file__).resolve().parent)
        comando_vbs = comando.replace('"', '""')
        carpeta_vbs = carpeta_trabajo.replace('"', '""')
        contenido = (
            "Option Explicit\r\n"
            "Dim shell\r\n"
            'Set shell = CreateObject("WScript.Shell")\r\n'
            f'shell.CurrentDirectory = "{carpeta_vbs}"\r\n'
            f'shell.Run "{comando_vbs}", 0, False\r\n'
        )
        acceso.write_text(contenido, encoding="utf-8")

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
        cartesia_voice_id = self.cartesia_voice_id_var.get().strip()[:200]
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
                    "tts_version": 2,
                    "elevenlabs_voice_id": voice_id,
                    "cartesia_voice_id": cartesia_voice_id,
                    "microfono_sensibilidad": config.MICROFONO_SENSIBILIDAD,
                    "ubicacion": ubicacion,
                    "spotify_client_id": spotify_client_id,
                    "vision_modelo": config.MODELO_VISION,
                    "modelo_ollama": config.MODELO,
                    "modelo_gemini": config.MODELO_GEMINI,
                    "modelo_openai": config.MODELO_OPENAI,
                    "modelo_groq_api": config.MODELO_GROQ_API,
                    "modelo_cloudflare": config.MODELO_CLOUDFLARE,
                    "cloudflare_account_id": config.CLOUDFLARE_ACCOUNT_ID,
                    "proveedor_ia_alternativo": config.PROVEEDOR_IA_ALTERNATIVO,
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
        config.CARTESIA_VOICE_ID = cartesia_voice_id
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
        self.cartesia_api_key = self.cartesia_api_key_var.get().strip()
        self.estado.set("Probando la voz seleccionada...")
        threading.Thread(target=self._reproducir_prueba_voz, daemon=True).start()

    def _reproducir_prueba_voz(self):
        error = voice.hablar(
            "Hola, soy Jarvis. Esta es una prueba de voz.",
            api_key=self.elevenlabs_api_key,
            cartesia_api_key=self.cartesia_api_key,
            cartesia_voice_id=self.cartesia_voice_id_var.get().strip(),
        )
        estado = error or (
            "Prueba de voz completada."
            if config.PROVEEDOR_TTS in {"elevenlabs", "cartesia"}
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

    def iniciar(self, modo="voz"):
        if self.asistente_activo:
            return True
        import tools

        try:
            permiso = tools.permiso_control_pc()
            if permiso is None:
                permiso = messagebox.askyesno(
                    "Permiso para controlar el ordenador",
                    "¿Autorizás a Jarvis a controlar este ordenador cuando se lo pidas?\n\n"
                    "Con permiso, podrá abrir aplicaciones y leer, crear, modificar o "
                    "eliminar archivos en ubicaciones accesibles para tu usuario, y "
                    "usar integraciones conectadas para enviar o publicar cuando se "
                    "lo pidas. También podrá analizar capturas de pantalla necesarias "
                    "para completar las tareas que le indiques; ese análisis se hace "
                    "localmente con Ollama y no es una grabación continua. Las acciones "
                    "solo se ejecutan ante instrucciones tuyas. La autorización se guarda "
                    "para próximos inicios y se puede revocar en Configuración.",
                    parent=self.raiz,
                )
                tools.establecer_permiso_control(permiso)
            config.COMPARTIENDO_PANTALLA = permiso is True
            self.bus.publish("pantalla", on=config.COMPARTIENDO_PANTALLA)
        except (OSError, ValueError) as error:
            messagebox.showerror(
                "No se pudo guardar el permiso",
                f"No iniciaré acciones del ordenador porque no pude guardar el permiso: {error}",
                parent=self.raiz,
            )
            return False
        proveedor = config.PROVEEDOR_IA
        api_key = self._clave_para_core(proveedor)
        elevenlabs_api_key = self._clave_para_core("elevenlabs")
        cartesia_api_key = self._clave_para_core("cartesia")
        self.modo = modo
        self.elevenlabs_api_key = elevenlabs_api_key
        self.cartesia_api_key = cartesia_api_key
        config.USAR_VOZ = True
        self.asistente_activo = True
        proveedor_nombre = proveedor.capitalize()
        self.bus.publish(
            "estado",
            valor="pensando",
            texto=f"Iniciando el Core local y comprobando {proveedor_nombre}…",
        )
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

    def _agregar_mensaje(self, quien, texto, solicitud_id=None, error=False):
        self.bus.publish(
            "msg",
            rol="jarvis" if quien == config.NOMBRE else "usuario",
            texto=texto,
            voz="voz" in quien.lower(),
            solicitud_id=solicitud_id,
            error=error,
        )

    def _estado(self, texto, solicitud_id=None):
        bajo = texto.lower()
        if "error" in bajo or "no pude" in bajo or "no respondió" in bajo:
            valor = "error"
        elif "procesando" in bajo:
            valor = "pensando"
        elif "respondiendo" in bajo or "recibiendo respuesta" in bajo:
            valor = "respondiendo"
        elif "escuch" in bajo:
            valor = "escuchando"
        else:
            valor = "espera"
        self.bus.publish(
            "estado", valor=valor, texto=texto, solicitud_id=solicitud_id
        )

    def _encolar_pedido(
        self, texto, origen, contexto_archivos=None, solicitud_id=None
    ):
        import tools

        with self._pedidos_lock:
            if not self._pedido_en_curso:
                tools.reiniciar_detencion()
            self._respuesta_lista.clear()
            if solicitud_id is not None:
                self.pedidos.put(
                    (texto, origen, contexto_archivos, solicitud_id)
                )
            elif contexto_archivos is not None:
                self.pedidos.put((texto, origen, contexto_archivos))
            else:
                self.pedidos.put((texto, origen))

    def enviar(self):
        self._estado_visible("Escribí tu mensaje en el chat del HUD.")

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
        from brain import Cerebro, crear_cerebro

        if not hasattr(self, "_cerebro_ready"):
            self._cerebro_ready = threading.Event()
        if not hasattr(self, "_cerebro_lock"):
            self._cerebro_lock = threading.RLock()
        import tools
        tools.NOTIFICAR = self._notificar
        tools.CONFIRMAR = self._confirmar
        try:
            import visionTools
        except Exception as error:
            self.vision_error = str(error)
            print(f"[vision] Herramientas visuales no disponibles: {error}")
        else:
            _ = visionTools
        try:
            if proveedor == "ollama":
                cerebro = Cerebro(provider="ollama")
            else:
                # Usar ensemble si hay 2+ keys configuradas (y modo no es "off")
                try:
                    cerebro = crear_cerebro(self._clave_para_core)
                except Exception as error:
                    print(f"[ia:{proveedor}] No pude preparar el ensemble: {error}")
                    cerebro = Cerebro(provider=proveedor, api_key=api_key)
        except Exception as error:
            self.ia_estado = "error"
            self.ia_error = f"No pude preparar {proveedor}: {error}"
            print(f"[ia:{proveedor}] {self.ia_error}")
            try:
                cerebro = Cerebro(provider="ollama")
            except Exception as error_local:
                self.ia_error += f" El Core local no pudo inicializarse: {error_local}"
                self._agregar_mensaje(config.NOMBRE, self.ia_error)
                self._estado("No se pudo iniciar el Core local.")
                self.asistente_activo = False
                return
        error = None
        if self.modo != "espera":
            try:
                error = cerebro.verificar()
            except Exception as error:
                error = f"No pude comprobar el proveedor {proveedor}: {error}"
                print(f"[ia:{proveedor}] {error}")
        if error:
            self.ia_estado = "error"
            self.ia_error = error
            self._publicar_info()
            self._agregar_mensaje(config.NOMBRE, error)
            self._agregar_mensaje(
                config.NOMBRE,
                "El Core local sigue disponible para comandos reconocidos aunque la IA no responda.",
            )
        else:
            self.ia_estado = getattr(cerebro, "ia_estado", "sin_verificar")
            self.ia_error = ""
        self.ia_proveedor_activo = (
            getattr(cerebro, "proveedor", None)
            or getattr(cerebro, "proveedor_principal", None)
            or proveedor
        )
        self.cerebro = cerebro
        self._cerebro_ready.set()

        if self.modo == "texto":
            self.oido = None
            self._agregar_mensaje(config.NOMBRE, "Modo texto activo. Escribime en el chat.")
            self._publicar_info()
            self._estado("Modo texto activo. Escribí un mensaje cuando quieras.")
        else:
            oido = None
            self._estado("Iniciando micrófono y reconocimiento de voz...")
            try:
                oido = voice.Oido(dispositivo=config.MICROFONO or None)
            except Exception as error:
                self.oido = None
                self._agregar_mensaje(
                    config.NOMBRE,
                    f"No pude iniciar el micrófono: {error}. Voy a reintentar la conexión "
                    "automáticamente; revisá la entrada en Ajustes si el problema continúa.",
                )
                print(f"[audio] No se pudo iniciar el micrófono: {error}")
                self._estado("Micrófono no disponible. Reintentando la conexión...")
                self._publicar_info()
                self._iniciar_escucha(None)
            else:
                self.oido = oido
            if self.modo == "espera":
                self._publicar_info()
                self._estado("En segundo plano. Di “Jarvis” para activarme.")
                if oido is not None:
                    self._iniciar_escucha(oido)
                return
            if self.ia_estado == "error":
                self._agregar_mensaje(
                    config.NOMBRE,
                    "Core local activo. La IA no está disponible; puedo atender los comandos locales reconocidos.",
                )
            else:
                self._agregar_mensaje(config.NOMBRE, "En línea. Escribime o hablame.")
            self._publicar_info()
            if oido is not None:
                self._estado(
                    "Micrófono activo. Hablá normalmente; también podés escribir."
                )
                saludo = (
                    f"Hola, {config.NOMBRE_USUARIO}. Soy {config.NOMBRE}. "
                    if config.NOMBRE_USUARIO
                    else f"Hola, soy {config.NOMBRE}. "
                )
                if self.ia_estado == "error":
                    saludo += (
                        "El Core local está activo, pero la IA no está disponible. "
                        "Puedo atender comandos locales reconocidos. ¿Qué necesitás?"
                    )
                else:
                    saludo += "Ya estoy en línea y te escucho. ¿Qué necesitás?"
                self._agregar_mensaje(config.NOMBRE, saludo)
                error_voz = self._hablar(saludo)
                if error_voz:
                    self._estado(error_voz)
                self._iniciar_escucha(oido)
                if getattr(self, "live_activo", False):
                    self._publicar_live("listening")

        while not self.cerrando.is_set() and self.asistente_activo:
            with self._pedidos_lock:
                try:
                    solicitud = self.pedidos.get_nowait()
                    pedido, origen = solicitud[:2]
                    contexto_archivos = (
                        solicitud[2] if len(solicitud) > 2 else None
                    )
                    solicitud_id = solicitud[3] if len(solicitud) > 3 else None
                except queue.Empty:
                    pedido = origen = None
                    contexto_archivos = None
                    solicitud_id = None
                else:
                    self._pedido_en_curso = True
                    tools.reiniciar_detencion()
            if pedido is None:
                time.sleep(0.2)
                continue
            self.bus.publish("detencion", activa=False, pendientes=0)
            if origen in ("voz", "texto"):
                self._agregar_mensaje(
                    "Tú (voz)" if origen == "voz" else "Tú (texto)",
                    pedido,
                    solicitud_id=solicitud_id,
                )
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
            self._estado("Procesando tu mensaje...", solicitud_id=solicitud_id)
            inicio_pedido = time.perf_counter()
            error_respuesta = False
            respuesta_local = False
            voz_stream_activa = False
            try:
                try:
                    if contexto_archivos is not None:
                        responder_archivos = getattr(
                            cerebro, "responder_sobre_archivos", None
                        )
                        if not callable(responder_archivos):
                            raise RuntimeError(
                                "El motor de conversación no admite análisis de adjuntos."
                            )
                        proveedor_actual = config.PROVEEDOR_IA
                        proveedor_alternativo = (
                            config.PROVEEDOR_IA_ALTERNATIVO or None
                        )
                        clave_actual = self._clave_para_core(proveedor_actual)
                        clave_alternativa = (
                            self._clave_para_core(proveedor_alternativo)
                            if proveedor_alternativo
                            else None
                        )
                        self._configurar_cerebro(
                            cerebro,
                            proveedor_actual,
                            clave_actual,
                            proveedor_alternativo,
                            clave_alternativa,
                        )
                        proveedor_solicitud = (
                            getattr(cerebro, "proveedor", None)
                            or getattr(cerebro, "proveedor_principal", None)
                            or proveedor_actual
                        )
                        modelo_solicitud = getattr(cerebro, "modelo", None) or (
                            config.MODELO
                            if proveedor_solicitud == "ollama"
                            else ai_providers.obtener_modelo(proveedor_solicitud)
                        )
                        print(
                            f"[chat] Procesando {solicitud_id or 'adjunto'} con "
                            f"proveedor={proveedor_solicitud}, modelo={modelo_solicitud}."
                        )
                        respuesta = responder_archivos(
                            pedido, contexto_archivos
                        )
                    else:
                        import core_engine

                        resultado_local = core_engine.procesar_comando_local(pedido)
                        if resultado_local is not None:
                            respuesta_local = True
                            respuesta = resultado_local.respuesta
                            print(
                                f"[chat] Solicitud {solicitud_id or origen} "
                                f"resuelta localmente ({resultado_local.intencion})."
                            )
                        else:
                            proveedor_actual = config.PROVEEDOR_IA
                            proveedor_alternativo = config.PROVEEDOR_IA_ALTERNATIVO or None
                            clave_actual = self._clave_para_core(proveedor_actual)
                            clave_alternativa = (
                                self._clave_para_core(proveedor_alternativo)
                                if proveedor_alternativo
                                else None
                            )
                            self._configurar_cerebro(
                                cerebro,
                                proveedor_actual,
                                clave_actual,
                                proveedor_alternativo,
                                clave_alternativa,
                            )
                            proveedor_solicitud = (
                                getattr(cerebro, "proveedor", None)
                                or getattr(cerebro, "proveedor_principal", None)
                                or proveedor_actual
                            )
                            modelo_solicitud = getattr(cerebro, "modelo", None) or (
                                config.MODELO
                                if proveedor_solicitud == "ollama"
                                else ai_providers.obtener_modelo(proveedor_solicitud)
                            )
                            print(
                                f"[chat] Procesando {solicitud_id or origen} con "
                                f"proveedor={proveedor_solicitud}, modelo={modelo_solicitud}."
                            )
                            if (
                                origen == "voz"
                                and getattr(self, "live_activo", False)
                                and callable(getattr(cerebro, "responder_en_vivo", None))
                            ):
                                respuesta = self._responder_en_vivo(cerebro, pedido)
                                voz_stream_activa = True
                            else:
                                respuesta = self._responder_cerebro(cerebro, pedido)
                except Exception as error:
                    error_respuesta = True
                    proveedor_error = (
                        getattr(cerebro, "proveedor", None)
                        or getattr(cerebro, "proveedor_principal", None)
                        or config.PROVEEDOR_IA
                    )
                    clave_error = getattr(cerebro, "api_key", None)
                    detalle_error = ai_providers.error_publico(
                        proveedor_error,
                        error,
                        clave_error if isinstance(clave_error, str) else None,
                    )
                    respuesta = f"Tuve un problema al procesar el pedido: {detalle_error}"
                    print(
                        f"[chat] Error en solicitud {solicitud_id or origen}: "
                        f"{detalle_error}"
                    )
                if (
                    not respuesta_local
                    and getattr(cerebro, "ia_estado", None) == "error"
                ):
                    error_respuesta = True
                self.ia_proveedor_activo = (
                    getattr(cerebro, "proveedor", None)
                    or getattr(cerebro, "proveedor_principal", None)
                    or self.ia_proveedor_activo
                )
                self.ultima_latencia_ms = round(
                    (time.perf_counter() - inicio_pedido) * 1000
                )
                print(
                    f"[chat] Respuesta recibida para {solicitud_id or origen} "
                    f"({len(respuesta)} caracteres, {self.ultima_latencia_ms} ms)."
                )
                self._estado("Recibiendo respuesta...", solicitud_id=solicitud_id)
                self._agregar_mensaje(
                    config.NOMBRE,
                    respuesta,
                    solicitud_id=solicitud_id,
                    error=error_respuesta,
                )
                error_voz = None
                if (
                    self.modo == "voz"
                    and origen == "voz"
                    and config.USAR_VOZ
                    and not voz_stream_activa
                ):
                    error_voz = self._hablar(respuesta)
                if error_respuesta:
                    self.ia_estado = "error"
                    self.ia_error = "El proveedor no respondió al último pedido."
                    self._publicar_info()
                    mensaje_error = (
                        f"No pude completar el pedido con {config.PROVEEDOR_IA}. "
                        "Revisá la respuesta de JARVIS y la configuración del proveedor."
                    )
                    print(f"[chat] La solicitud {solicitud_id or origen} terminó con error.")
                    self._estado(mensaje_error, solicitud_id=solicitud_id)
                else:
                    self.ia_estado = getattr(cerebro, "ia_estado", self.ia_estado)
                    if self.ia_estado == "conectado":
                        self.ia_error = ""
                    self._publicar_info()
                    self._estado(
                        error_voz
                        or (
                            "Escuchando; hablá cuando quieras."
                            if self.modo == "voz"
                            else "Listo."
                        )
                    )
            finally:
                voz_stream_activa = False
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

    def _responder_en_vivo(self, cerebro, pedido):
        cola_voz = queue.Queue()
        fin_cola = object()
        interrumpido = threading.Event()
        cancelar_salida = threading.Event()
        error_voz = []
        texto_pendiente = ""
        respuesta_completa = []

        def reproducir():
            while True:
                fragmento = cola_voz.get()
                if fragmento is fin_cola:
                    return
                if (
                    interrumpido.is_set()
                    or cancelar_salida.is_set()
                    or self._live_cancelar_generacion.is_set()
                ):
                    continue
                limpio = voice.limpiar_texto_voz(fragmento)
                if limpio:
                    resultado = self._hablar(
                        limpio,
                        al_interrumpir=interrumpido.set,
                    )
                    if resultado:
                        error_voz.append(resultado)
                        cancelar_salida.set()
                        return

        hilo_voz = threading.Thread(target=reproducir, daemon=True)
        hilo_voz.start()

        def recibir_fragmento(fragmento):
            nonlocal texto_pendiente
            if (
                self._live_cancelar_generacion.is_set()
                or cancelar_salida.is_set()
                or interrumpido.is_set()
                or not getattr(self, "live_activo", False)
            ):
                return False
            respuesta_completa.append(fragmento)
            texto_pendiente += fragmento
            self.bus.publish(
                "live_text",
                speaker="jarvis",
                text=fragmento,
                append=True,
            )
            listos, texto_pendiente = voice.separar_fragmentos(texto_pendiente)
            for listo in listos:
                cola_voz.put(listo)
            return True

        try:
            respuesta = cerebro.responder_en_vivo(
                pedido,
                recibir_fragmento,
            )
            if (
                getattr(self, "live_activo", False)
                and not self._live_cancelar_generacion.is_set()
                and not respuesta_completa
            ):
                listos, _ = voice.separar_fragmentos(respuesta, final=True)
                for listo in listos:
                    cola_voz.put(listo)
            elif getattr(self, "live_activo", False) and not self._live_cancelar_generacion.is_set():
                listos, texto_pendiente = voice.separar_fragmentos(
                    texto_pendiente, final=True
                )
                for listo in listos:
                    cola_voz.put(listo)
        finally:
            cola_voz.put(fin_cola)
            hilo_voz.join()

        if error_voz:
            self._estado(error_voz[0])
        if getattr(self, "live_activo", False):
            self._publicar_live(
                "interrupted" if interrumpido.is_set() else "listening"
            )
        return respuesta

    def _hablar(self, texto, al_interrumpir=None):
        if config.VOZ_SILENCIADA:
            return None
        oido = getattr(self, "oido", None)
        if getattr(self, "live_silenciado", False):
            oido = None
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
                        cartesia_api_key=self.cartesia_api_key,
                        cartesia_voice_id=config.CARTESIA_VOICE_ID,
                    )
            else:
                resultado = voice.hablar(
                    texto,
                    api_key=self.elevenlabs_api_key,
                    cartesia_api_key=self.cartesia_api_key,
                    cartesia_voice_id=config.CARTESIA_VOICE_ID,
                )
            if isinstance(resultado, voice.InterrupcionVoz):
                if al_interrumpir:
                    al_interrumpir()
                pedido = resultado.texto.strip()
                if pedido and not self.cerrando.is_set():
                    self._agregar_mensaje("Tú (voz)", pedido)
                    self._encolar_pedido(pedido, "voz")
                elif not self.cerrando.is_set():
                    self._estado("Te escuché, pero no pude reconocer lo que dijiste.")
                return None
            return resultado
        finally:
            if (
                oido
                and self.modo == "voz"
                and config.USAR_VOZ
                and not getattr(self, "live_silenciado", False)
            ):
                oido.pausar_escucha.clear()
            self.bus.publish("estado", valor="escuchando" if oido else "espera", texto="")
            if getattr(self, "live_activo", False):
                self._publicar_live(
                    "muted" if getattr(self, "live_silenciado", False) else "listening"
                )

    def _iniciar_escucha(self, oido):
        if self._escucha_hilo and self._escucha_hilo.is_alive():
            return
        self._escucha_hilo = threading.Thread(
            target=self._escuchar, args=(oido,), daemon=True
        )
        self._escucha_hilo.start()

    def _escuchar(self, oido):
        espera_reconexion = 1
        while not self.cerrando.is_set():
            if self.modo not in ("voz", "espera"):
                time.sleep(0.1)
                continue
            modo_escucha = self.modo
            oido = self.oido
            if oido is None:
                try:
                    oido = voice.Oido(dispositivo=config.MICROFONO or None)
                except Exception as error:
                    print(f"[audio] No se pudo reconectar el micrófono: {error}")
                    self._estado(
                        "Micrófono no disponible. Reintentando en "
                        f"{espera_reconexion} s..."
                    )
                    if self.cerrando.wait(espera_reconexion):
                        return
                    espera_reconexion = min(espera_reconexion * 2, 30)
                    continue
                with self._modo_lock:
                    if self.cerrando.is_set() or self.modo not in ("voz", "espera"):
                        oido.pausar_escucha.set()
                        return
                    self.oido = oido
                print("[audio] Micrófono reconectado.")
                self._publicar_info()
                if getattr(self, "live_activo", False):
                    self._publicar_live("listening")
                self._estado("Micrófono reconectado. Hablá normalmente; Jarvis te escucha.")
                espera_reconexion = 1
            try:
                if modo_escucha == "espera":
                    pedido = oido.esperar_activacion()
                else:
                    pedido = oido.escuchar_continuo()
                if self.oido is not oido or self.cerrando.is_set():
                    continue
                if modo_escucha == "espera":
                    with self._modo_lock:
                        if self.modo != "espera":
                            continue
                        self.modo = "voz"
                    self.raiz.after(0, self._mostrar_hud_al_despertar)
                    self._publicar_info()
                    if not pedido:
                        self._hablar("Te escucho. ¿Qué necesitás?")
                        continue
                elif self.modo != "voz":
                    continue
                if not pedido or self.cerrando.is_set():
                    continue
                if voice.normalizar(pedido).strip() in {
                    "cerrar jarvis",
                    "apagar jarvis",
                    "apagate jarvis",
                    "terminar jarvis",
                    "salir de jarvis",
                }:
                    self.raiz.after(0, self.cerrar)
                    return

                self._encolar_pedido(pedido, "voz")
                while (
                    not self._respuesta_lista.wait(0.1)
                    and not self.cerrando.is_set()
                    and self.modo == "voz"
                    and self.oido is oido
                ):
                    pass
                espera_reconexion = 1
            except Exception as error:
                with self._modo_lock:
                    es_escucha_actual = self.oido is oido
                    if es_escucha_actual:
                        self.oido = None
                if not es_escucha_actual:
                    continue
                print(f"[audio] Se interrumpió la escucha: {error}")
                self._publicar_info()
                if getattr(self, "live_activo", False):
                    self._publicar_live("error")
                self._agregar_mensaje(
                    config.NOMBRE,
                    f"Se interrumpió el micrófono: {error}. Voy a reintentar la conexión "
                    "automáticamente.",
                )
                self._estado(
                    "Micrófono interrumpido. Reintentando en "
                    f"{espera_reconexion} s..."
                )
                if self.cerrando.wait(espera_reconexion):
                    return
                espera_reconexion = min(espera_reconexion * 2, 30)

    def alternar_modo(self):
        if not self.asistente_activo:
            return self.iniciar("voz")
        with self._modo_lock:
            if self.modo == "voz":
                self.modo = "texto"
                if self.oido is not None:
                    self.oido.pausar_escucha.set()
                mensaje = "Modo texto activo. El micrófono está pausado."
            else:
                self.modo = "voz"
                if self.oido is not None:
                    self.oido.pausar_escucha.clear()
                else:
                    self._iniciar_escucha(None)
                mensaje = "Modo voz activo. Hablá o escribí a Jarvis."
        self._publicar_info()
        self._estado(mensaje)
        return True

    def iniciar_live(self):
        if getattr(self, "live_activo", False):
            self._publicar_live(self._live_state)
            return True
        if not self.asistente_activo:
            if not self.iniciar("texto"):
                return False
        self._live_previous_mode = self.modo or "texto"
        with self._modo_lock:
            self.modo = "texto"
            oido = self.oido
            if oido is not None:
                oido.pausar_escucha.set()
                oido.microfono_silenciado.set()
                try:
                    oido.sd.stop()
                except Exception as error:
                    print(f"[live] No pude pausar el micrófono anterior: {error}")
        import tools
        from live_voice import LIVE_MODELS, LiveVoiceManager

        tools.NOTIFICAR = self._notificar
        tools.CONFIRMAR = self._confirmar
        selected_provider = config.LIVE_VOICE_PROVIDER
        if selected_provider not in LIVE_MODELS:
            selected_provider = "gemini"
        try:
            manager = LiveVoiceManager(
                credential_resolver=self._clave_para_core,
                tool_schemas=tools.esquemas,
                tool_executor=self._ejecutar_herramienta_live,
                on_event=self._evento_live,
                preferred_provider=selected_provider,
            )
            available = manager.available_providers()
        except Exception as error:
            self._agregar_mensaje(
                config.NOMBRE,
                f"No pude preparar JARVIS LIVE: {error}",
                error=True,
            )
            self._publicar_live("error")
            return False
        configured = [item["id"] for item in available if item["configured"]]
        if not configured:
            self._agregar_mensaje(
                config.NOMBRE,
                "JARVIS LIVE nativo necesita una clave de Gemini u OpenAI guardada "
                "en el Administrador de credenciales. El micrófono no se activó.",
                error=True,
            )
            self._publicar_live("error")
            return False
        if selected_provider not in configured:
            selected_provider = configured[0]
            config.LIVE_VOICE_PROVIDER = selected_provider
            manager.select_provider(selected_provider)
        self._live_manager = manager
        self._live_providers = available
        self.live_activo = True
        self.live_silenciado = False
        self._live_cancelar_generacion.clear()
        self._live_state = "connecting"
        self._publicar_live("connecting")
        self._live_thread = threading.Thread(target=manager.run, daemon=True)
        self._live_thread.start()
        self._publicar_info()
        return True

    def silenciar_live(self):
        if not self.live_activo:
            return False
        self.live_silenciado = not self.live_silenciado
        if self._live_manager is not None:
            self._live_manager.set_muted(self.live_silenciado)
        self._live_state = "muted" if self.live_silenciado else "listening"
        self._publicar_live(self._live_state)
        return True

    def finalizar_live(self):
        if not self.live_activo:
            self._publicar_live("idle")
            return True
        self.live_activo = False
        self._live_cancelar_generacion.set()
        self.live_silenciado = False
        manager = self._live_manager
        self._live_manager = None
        if manager is not None:
            manager.stop()
        with self._modo_lock:
            self.modo = self._live_previous_mode
            oido = self.oido
            if oido is not None and self.modo == "voz":
                oido.microfono_silenciado.clear()
                oido.pausar_escucha.clear()
        if oido is not None and self.modo == "voz":
            self._iniciar_escucha(oido)
        self._live_state = "idle"
        self._publicar_live("idle")
        self._publicar_info()
        self._estado(
            "Modo voz activo." if self.modo == "voz"
            else "Modo texto activo. El micrófono está pausado."
        )
        return True

    def _publicar_live(self, estado):
        from live_voice import LIVE_LABELS, LIVE_MODELS, LIVE_VOICES

        proveedor = config.LIVE_VOICE_PROVIDER
        self.bus.publish(
            "live",
            active=self.live_activo,
            muted=self.live_silenciado,
            state=estado,
            provider=LIVE_LABELS.get(proveedor, proveedor),
            provider_id=proveedor,
            model=LIVE_MODELS.get(proveedor, ""),
            mode="Speech-to-Speech nativo",
            providers=self._live_providers,
            voice=self._voz_live_actual(proveedor),
            voices=list(LIVE_VOICES.get(proveedor, ())),
        )

    @staticmethod
    def _voz_live_actual(proveedor):
        return (
            config.LIVE_GEMINI_VOICE
            if proveedor == "gemini"
            else config.LIVE_OPENAI_VOICE
        )

    def _evento_live(self, evento, **datos):
        from live_voice import LIVE_VOICES

        if evento == "state":
            self._live_state = datos.get("state", "error")
            self.bus.publish(
                "live",
                active=self.live_activo,
                muted=self.live_silenciado,
                state=self._live_state,
                provider=datos.get("provider", config.LIVE_VOICE_PROVIDER),
                provider_id=config.LIVE_VOICE_PROVIDER,
                model=datos.get("model", ""),
                mode="Speech-to-Speech nativo",
                providers=self._live_providers,
                voice=self._voz_live_actual(config.LIVE_VOICE_PROVIDER),
                voices=list(LIVE_VOICES.get(config.LIVE_VOICE_PROVIDER, ())),
                detail=datos.get("detail", ""),
            )
            if datos.get("detail"):
                self._agregar_mensaje(
                    config.NOMBRE, datos["detail"], error=True
                )
        elif evento == "provider_selected":
            config.LIVE_VOICE_PROVIDER = datos["provider"]
            self._live_state = "reconnecting"
            self._publicar_live(self._live_state)
        elif evento == "connected":
            self._live_state = "listening"
            self.bus.publish(
                "live",
                active=self.live_activo,
                muted=self.live_silenciado,
                state=self._live_state,
                provider=datos.get("provider", config.LIVE_VOICE_PROVIDER),
                provider_id=config.LIVE_VOICE_PROVIDER,
                model=datos.get("model", ""),
                mode="Speech-to-Speech nativo",
                providers=self._live_providers,
                voice=self._voz_live_actual(config.LIVE_VOICE_PROVIDER),
                voices=list(LIVE_VOICES.get(config.LIVE_VOICE_PROVIDER, ())),
            )
        elif evento in ("speech_started", "interrupted"):
            self._live_state = (
                "interrupted" if self._live_state == "speaking" or evento == "interrupted"
                else "listening"
            )
            self._publicar_live(self._live_state)
        elif evento in ("turn_end_detected", "speech_ended", "thinking"):
            self._live_state = "thinking"
            self._publicar_live(self._live_state)
        elif evento == "playback_started":
            self._live_state = "speaking"
            self._publicar_live(self._live_state)
        elif evento in ("turn_complete", "playback_finished"):
            self._live_state = "listening"
            self._publicar_live(self._live_state)
        elif evento == "transcript":
            self.bus.publish(
                "live_text",
                speaker=(
                    "usuario"
                    if datos.get("speaker") == "user"
                    else datos.get("speaker", "usuario")
                ),
                text=datos.get("text", ""),
            )
        elif evento == "user_final":
            texto = datos.get("text", "").strip()
            if texto:
                self._agregar_mensaje("usuario", texto)
        elif evento == "assistant_final":
            texto = datos.get("text", "").strip()
            if texto:
                self._agregar_mensaje(config.NOMBRE, texto)
        elif evento == "tool_error":
            self._agregar_mensaje(
                config.NOMBRE,
                datos.get("detail", "Falló una herramienta de JARVIS LIVE."),
                error=True,
            )
        elif evento == "metrics":
            self.bus.publish("live_metrics", **datos)
        elif evento == "audio_status":
            self.bus.publish(
                "live_metrics",
                audio_status=datos.get("detail", ""),
            )
        elif evento == "reconnecting":
            self._live_state = "reconnecting"
            self._publicar_live(self._live_state)
        elif evento == "audio_ready":
            self._live_state = "listening"
            self._publicar_live(self._live_state)
        elif evento == "audio_close_error":
            self._agregar_mensaje(
                config.NOMBRE,
                "No pude liberar un recurso de audio: "
                f"{datos.get('detail', 'error desconocido')}",
                error=True,
            )
        elif evento == "resume_handle":
            return
        else:
            print(f"[live] Evento no reconocido: {evento}")

    def _ejecutar_herramienta_live(self, nombre, argumentos):
        if nombre == "__client_delegation__":
            texto = str(argumentos.get("text", "")).strip()
            if not texto:
                return "No recibí una instrucción para el Core Engine."
            cerebro = getattr(self, "cerebro", None)
            listo = getattr(self, "_cerebro_ready", None)
            if cerebro is None or listo is None or not listo.is_set():
                return "El Core Engine todavía no está disponible."
            import core_engine

            local = core_engine.procesar_comando_local(texto)
            if local is not None:
                return local.respuesta
            lock = getattr(self, "_cerebro_lock", None)
            if lock is None:
                lock = self._cerebro_lock = threading.RLock()
            with lock:
                return cerebro.responder(texto)
        import tools

        return tools.ejecutar(nombre, argumentos)

    def _configurar_cerebro(self, cerebro, *argumentos):
        with self._cerebro_lock:
            return cerebro.configurar(*argumentos)

    def _responder_cerebro(self, cerebro, texto):
        with self._cerebro_lock:
            return cerebro.responder(texto)

    def _seleccionar_proveedor_live(self, proveedor):
        manager = self._live_manager
        try:
            if manager is not None:
                manager.select_provider(proveedor)
                if (
                    self.live_activo
                    and (
                        self._live_thread is None
                        or not self._live_thread.is_alive()
                    )
                ):
                    self._live_thread = threading.Thread(
                        target=manager.run,
                        daemon=True,
                    )
                    self._live_thread.start()
            else:
                from live_voice import LIVE_MODELS
                if proveedor not in LIVE_MODELS:
                    raise ValueError(f"Proveedor LIVE no válido: {proveedor}")
                if not self._clave_para_core(proveedor):
                    raise ValueError(f"No hay una clave guardada para {proveedor}.")
                config.LIVE_VOICE_PROVIDER = proveedor
                self._publicar_live("idle")
        except Exception as error:
            self._agregar_mensaje(
                config.NOMBRE,
                f"No pude cambiar el proveedor LIVE: {error}",
                error=True,
            )
            return False
        return True

    def _seleccionar_voz_live(self, proveedor, voz):
        from live_voice import LIVE_VOICES

        if proveedor not in LIVE_VOICES or voz not in LIVE_VOICES[proveedor]:
            return False
        atributo = (
            "LIVE_GEMINI_VOICE" if proveedor == "gemini" else "LIVE_OPENAI_VOICE"
        )
        if getattr(config, atributo) == voz:
            return True
        setattr(config, atributo, voz)
        if self.live_activo and self._live_manager is not None:
            self._live_state = "reconnecting"
            self._publicar_live(self._live_state)
            self._live_manager.restart_session()
        else:
            self._publicar_live("idle")
        return True

    def desde_hud(self, texto, solicitud_id=None):
        texto = str(texto).strip()
        if not texto:
            return False
        self._encolar_pedido(texto, "texto", solicitud_id=solicitud_id)
        if not self.asistente_activo:
            self.raiz.after(0, self._iniciar_texto_desde_hud)
        return True

    def adjuntar_desde_hud(self, pregunta, contexto):
        pregunta = str(pregunta).strip()
        if not pregunta:
            return False
        self._encolar_pedido(pregunta, "texto", contexto_archivos=contexto)
        if not self.asistente_activo:
            self.raiz.after(0, self._iniciar_texto_desde_hud)
        return True

    def _iniciar_texto_desde_hud(self):
        if not self.asistente_activo:
            self.iniciar("texto")

    def _activar_voz(self):
        try:
            oido = voice.Oido(dispositivo=config.MICROFONO or None)
        except Exception as error:
            with self._modo_lock:
                self._voz_iniciando = False
                if self.modo == "voz":
                    self.modo = None
                    self.asistente_activo = False
                    config.USAR_VOZ = True
            self._publicar_info()
            self._agregar_mensaje(
                config.NOMBRE,
                f"No pude activar el micrófono: {error}. Revisá la entrada "
                "en Ajustes y volvé a iniciar por voz.",
            )
            self._estado("Micrófono no disponible. Corregí la entrada y reintentá.")
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
        if self.live_activo:
            self._publicar_live("listening")
        self._estado("Micrófono activo. Hablá normalmente; Jarvis te escucha.")

    # ------------------------------------------------------------------ HUD
    def _boton_hud(self):
        if self.asistente_activo:
            self._abrir_hud(forzar=True)
        else:
            self.iniciar()

    def abrir_hud_inicial(self):
        """Abre el HUD sin activar el micrófono hasta que el usuario lo solicite."""
        if not self._abrir_hud(inicial=True):
            messagebox.showerror(
                config.NOMBRE,
                "No se pudo abrir la interfaz HUD. Verificá que Microsoft Edge o Google Chrome esté instalado.",
            )
            return
        self._estado_visible(
            "Micrófono inactivo. Iniciá JARVIS LIVE para comenzar una conversación por voz."
        )

    def _iniciar_desde_hud(self):
        if not self.iniciar():
            self._mostrar_ajustes_desde_hud()  # falta configurar algo: mostramos los ajustes

    def iniciar_por_activacion(self):
        if not self.iniciar("espera"):
            self._estado_visible("No pude activar la escucha por palabra.")

    def _mostrar_hud_al_despertar(self):
        if self.bus.hay_hud():
            return
        if not self._abrir_hud(forzar=True):
            self._estado("No pude abrir el HUD, pero sigo escuchando.")

    def _configuracion_hud(self):
        import tools

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
        claves_mascaradas = {}
        for nombre in ai_providers.PROVEEDORES:
            clave = self._clave_guardada_o_entorno(nombre)
            if clave:
                claves_mascaradas[nombre] = f"••••••{clave[-4:]}"
        claves_guardadas = {
            nombre: nombre in claves_mascaradas
            for nombre in ai_providers.PROVEEDORES
        }
        microfono_configurado = config.MICROFONO
        if microfono_configurado in (None, ""):
            microfono_configurado = voice.Oido._elegir_microfono_con(sd)
        return {
            "microfonos": microfonos,
            "salidas": salidas,
            "microfono": seleccionar_dispositivo(microfono_configurado, microfonos),
            "microfono_sensibilidad": config.MICROFONO_SENSIBILIDAD,
            "salida": seleccionar_dispositivo(config.AUDIO_SALIDA, salidas),
            "proveedor": proveedor,
            "api_key_guardada": claves_guardadas.get(proveedor, False),
            "api_keys_guardadas": claves_guardadas,
            "api_key_mascarada": claves_mascaradas.get(proveedor, ""),
            "api_keys_mascaradas": claves_mascaradas,
            "proveedor_ia_alternativo": config.PROVEEDOR_IA_ALTERNATIVO,
            "clave_alternativa_guardada": bool(
                claves_guardadas.get(config.PROVEEDOR_IA_ALTERNATIVO, False)
            ),
            "proveedor_tts": config.PROVEEDOR_TTS,
            "elevenlabs_key_guardada": bool(
                self._clave_guardada_o_entorno("elevenlabs")
            ),
            "elevenlabs_voice_id": config.ELEVENLABS_VOICE_ID,
            "cartesia_key_guardada": bool(
                self._clave_guardada_o_entorno("cartesia")
            ),
            "cartesia_voice_id": config.CARTESIA_VOICE_ID,
            "github_token_guardado": self._clave_local_guardada("github"),
            "permiso_control_pc": tools.permiso_control_pc() is True,
            "asistente_activo": bool(self.asistente_activo),
            "ia_estado": self.ia_estado,
            "ia_error": self.ia_error,
            "motor": self._estado_motor(),
            "spotify_client_id": config.SPOTIFY_CLIENT_ID,
            "integraciones": connections_catalog.catalogo_conexiones(
                spotify_client_id=config.SPOTIFY_CLIENT_ID,
                github_token_guardado=bool(
                    self._clave_guardada_o_entorno("github")
                ),
                ubicacion=config.UBICACION,
                google_estado=__import__("google_integration").estado_conexion(),
            ),
            "ubicacion": config.UBICACION,
            "vision_modelo": config.MODELO_VISION,
            "modelo_ollama": config.MODELO,
            "modelo_gemini": config.MODELO_GEMINI,
            "modelo_openai": config.MODELO_OPENAI,
            "modelo_cursor": config.MODELO_CURSOR,
            "modelo_groq_api": config.MODELO_GROQ_API,
            "modelo_cloudflare": config.MODELO_CLOUDFLARE,
            "cloudflare_account_id": config.CLOUDFLARE_ACCOUNT_ID,
            "modelos_ia": config.MODELOS_IA,
            "ollama_host": config.OLLAMA_HOST,
            "modelo_whisper": config.WHISPER_MODELO,
            "idioma": config.IDIOMA,
            "volumen_voz": config.VOLUMEN_VOZ,
            "velocidad_voz": config.VELOCIDAD_VOZ,
            "volumen_efectos": config.VOLUMEN_EFECTOS,
            "tema_hud": config.TEMA_HUD,
            "modo_ensemble": getattr(config, "MODO_ENSEMBLE", "auto"),
            "estrategia_ensemble": getattr(config, "ESTRATEGIA_ENSEMBLE", "rapido"),
            "inicio_automatico": config.INICIO_AUTOMATICO,
            "ensemble_proveedores_activos": [
                p for p in ai_providers.PROVEEDORES
                if self._clave_guardada_o_entorno(p)
                and (p != "cloudflare" or config.CLOUDFLARE_ACCOUNT_ID)
            ],
        }

    def _probar_ia_hud(self, comparar=False):
        proveedores = (
            list(ai_providers.PROVEEDORES)
            if comparar
            else [config.PROVEEDOR_IA]
        )
        resultados = []
        for proveedor in proveedores:
            if proveedor == "ollama":
                inicio = time.perf_counter()
                try:
                    import ollama

                    cliente = ollama.Client(host=config.OLLAMA_HOST)
                    modelos = [item.model for item in cliente.list().models]
                    presente = (
                        config.MODELO in modelos
                        if ":" in config.MODELO
                        else any(modelo.split(":")[0] == config.MODELO for modelo in modelos)
                    )
                except Exception:
                    resultados.append({
                        "proveedor": proveedor,
                        "modelo": config.MODELO,
                        "estado": "error",
                        "error": "No se pudo conectar con Ollama.",
                    })
                else:
                    resultados.append({
                        "proveedor": proveedor,
                        "modelo": config.MODELO,
                        "estado": "conectado" if presente else "error",
                        "tiempo_ms": round((time.perf_counter() - inicio) * 1000),
                        **({} if presente else {"error": "El modelo no está descargado en Ollama."}),
                    })
                continue
            if proveedor not in ai_providers.PROVEEDORES:
                resultados.append({
                    "proveedor": proveedor,
                    "modelo": "",
                    "estado": "no_disponible",
                    "error": "La prueba de conexión abarca los proveedores configurados.",
                })
                continue
            modelo = ai_providers.obtener_modelo(proveedor)
            try:
                clave = self._clave_guardada_o_entorno(proveedor)
            except Exception:
                resultados.append({
                    "proveedor": proveedor,
                    "modelo": modelo,
                    "estado": "error",
                    "error": "No se pudo leer la credencial local de este proveedor.",
                })
                continue
            if not clave:
                resultados.append({
                    "proveedor": proveedor,
                    "modelo": modelo,
                    "estado": "sin_configurar",
                    "error": "No hay una API key guardada para este proveedor.",
                })
                continue
            try:
                tiempo_ms = ai_providers.probar_conexion(proveedor, clave, modelo)
            except Exception as error:
                resultados.append({
                    "proveedor": proveedor,
                    "modelo": modelo,
                    "estado": "error",
                    "error": ai_providers.error_publico(proveedor, error, clave),
                })
            else:
                resultados.append({
                    "proveedor": proveedor,
                    "modelo": modelo,
                    "estado": "conectado",
                    "tiempo_ms": tiempo_ms,
                })
        resultado_activo = next(
            (r for r in resultados if r["proveedor"] == config.PROVEEDOR_IA),
            None,
        )
        if resultado_activo:
            if resultado_activo["estado"] == "conectado":
                self.ia_estado = "conectado"
                self.ia_error = ""
            else:
                self.ia_estado = "error"
                self.ia_error = resultado_activo.get("error", "")
            self._publicar_info()
        return {
            "comparacion": bool(comparar),
            "resultados": resultados,
            "nota": (
                "Cada proveedor recibió el mismo mensaje breve. La latencia es de ida y vuelta "
                "e incluye red y generación; puede generar consumo de API."
            ),
        }

    def _guardar_configuracion_hud(self, datos):
        proveedores = {"ollama", *ai_providers.PROVEEDORES}
        voces = {"piper", "windows", "elevenlabs", "cartesia"}
        proveedor = str(datos.get("proveedor", "")).lower()
        proveedor_tts = str(datos.get("proveedor_tts", "")).lower()
        proveedor_alternativo = str(
            datos.get("proveedor_ia_alternativo", config.PROVEEDOR_IA_ALTERNATIVO)
        ).lower()
        if (
            proveedor not in proveedores
            or proveedor_tts not in voces
            or proveedor_alternativo not in {"", *ai_providers.PROVEEDORES}
            or proveedor_alternativo == proveedor
        ):
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
        cartesia_voice_id = str(datos.get("cartesia_voice_id", "")).strip()[:200]
        spotify_client_id = str(datos.get("spotify_client_id", "")).strip()[:200]
        ubicacion = str(datos.get("ubicacion", "")).strip()[:200]
        vision_modelo = str(datos.get("vision_modelo", "")).strip()[:200]
        if not vision_modelo:
            raise ValueError("Indicá el nombre del modelo visual.")
        modelo_ollama = str(datos.get("modelo_ollama", "")).strip()[:200]
        modelo_gemini = str(datos.get("modelo_gemini", "")).strip()[:200]
        modelo_openai = str(datos.get("modelo_openai", "")).strip()[:200]
        modelo_cursor = str(datos.get("modelo_cursor", config.MODELO_CURSOR)).strip()[:200]
        modelo_groq_api = str(datos.get("modelo_groq_api", config.MODELO_GROQ_API)).strip()[:200]
        modelo_cloudflare = str(
            datos.get("modelo_cloudflare", config.MODELO_CLOUDFLARE)
        ).strip()[:200]
        cloudflare_account_id = str(
            datos.get("cloudflare_account_id", config.CLOUDFLARE_ACCOUNT_ID)
        ).strip()[:100]
        ollama_host = str(datos.get("ollama_host", "")).strip()[:500]
        modelo_whisper = str(datos.get("modelo_whisper", "")).strip()[:100]
        idioma = str(datos.get("idioma", "")).strip()[:10]
        if not all((
            modelo_ollama,
            modelo_gemini,
            modelo_openai,
            modelo_groq_api,
            modelo_cloudflare,
            ollama_host,
            modelo_whisper,
            idioma,
        )):
            raise ValueError("Completá los modelos de IA, Ollama, Whisper y el idioma.")
        if not ollama_host.startswith(("http://", "https://")):
            raise ValueError("La dirección de Ollama debe comenzar con http:// o https://.")
        if proveedor == "cloudflare" and not cloudflare_account_id:
            raise ValueError("Configurá el Account ID de Cloudflare en Conexiones.")
        modo_ensemble = str(datos.get("modo_ensemble", config.MODO_ENSEMBLE)).lower()
        if modo_ensemble not in {"auto", "on", "off"}:
            modo_ensemble = "auto"
        estrategia_ensemble = str(datos.get("estrategia_ensemble", config.ESTRATEGIA_ENSEMBLE)).lower()
        if estrategia_ensemble not in {"mejor", "rapido", "verificar", "votar"}:
            estrategia_ensemble = "mejor"
        volumen_voz = self._validar_preferencia_entera(
            datos, "volumen_voz", config.VOLUMEN_VOZ, 0, 100
        )
        velocidad_voz = self._validar_preferencia_entera(
            datos, "velocidad_voz", config.VELOCIDAD_VOZ, 70, 130
        )
        microfono_sensibilidad = self._validar_preferencia_entera(
            datos,
            "microfono_sensibilidad",
            config.MICROFONO_SENSIBILIDAD,
            0,
            100,
        )
        volumen_efectos = self._validar_preferencia_entera(
            datos, "volumen_efectos", config.VOLUMEN_EFECTOS, 0, 100
        )
        tema_hud = str(datos.get("tema_hud", config.TEMA_HUD)).lower()
        if tema_hud not in {"cian", "violeta", "verde"}:
            raise ValueError("Elegí un tema visual disponible.")
        clave_ia = str(datos.get("api_key", "")).strip()
        claves_api = datos.get("api_keys", {})
        if not isinstance(claves_api, dict):
            raise ValueError("Las API keys enviadas no tienen un formato válido.")
        borrar_claves_api = datos.get("borrar_api_keys", {})
        if not isinstance(borrar_claves_api, dict):
            raise ValueError("La selección de claves para eliminar no es válida.")
        clave_elevenlabs = str(datos.get("elevenlabs_api_key", "")).strip()
        clave_cartesia = str(datos.get("cartesia_api_key", "")).strip()
        borrar_clave_cartesia = datos.get("borrar_cartesia_key", False)
        if not isinstance(borrar_clave_cartesia, bool):
            raise ValueError("La selección para borrar la API key de Cartesia no es válida.")
        token_github = str(datos.get("github_token", "")).strip()
        permiso_control_pc = datos.get("permiso_control_pc")
        if permiso_control_pc is not None and not isinstance(permiso_control_pc, bool):
            raise ValueError("El permiso de control no tiene un valor válido.")
        inicio_automatico = datos.get(
            "inicio_automatico", config.INICIO_AUTOMATICO
        )
        if not isinstance(inicio_automatico, bool):
            raise ValueError("La opción de inicio automático debe ser verdadera o falsa.")

        if proveedor == "ollama" and (clave_ia or datos.get("borrar_api_key")):
            raise ValueError("Ollama es local y no admite una API key.")
        if datos.get("borrar_api_key"):
            self._eliminar_clave(proveedor)
        elif clave_ia:
            self._guardar_clave(proveedor, clave_ia)
        for codigo in ai_providers.PROVEEDORES:
            clave = str(claves_api.get(codigo, "")).strip()
            if borrar_claves_api.get(codigo):
                self._eliminar_clave(codigo)
            elif clave:
                self._guardar_clave(codigo, clave)
        if datos.get("borrar_elevenlabs_key"):
            self._eliminar_clave("elevenlabs")
        elif clave_elevenlabs:
            self._guardar_clave("elevenlabs", clave_elevenlabs)
        if borrar_clave_cartesia:
            self._eliminar_clave("cartesia")
        elif clave_cartesia:
            self._guardar_clave("cartesia", clave_cartesia)
        if datos.get("borrar_github_token"):
            self._eliminar_clave("github")
        elif token_github:
            self._guardar_clave("github", token_github)

        self._actualizar_inicio_automatico(inicio_automatico)

        requiere_reinicio = self.asistente_activo and any(
            nuevo != actual
            for nuevo, actual in (
                (microfono_guardado, config.MICROFONO),
                (salida, config.AUDIO_SALIDA),
                (ollama_host, config.OLLAMA_HOST),
                (modelo_whisper, config.WHISPER_MODELO),
                (idioma, config.IDIOMA),
                (inicio_automatico, config.INICIO_AUTOMATICO),
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
        config.MODELO_CURSOR = modelo_cursor
        config.MODELO_GROQ_API = modelo_groq_api
        config.MODELO_CLOUDFLARE = modelo_cloudflare
        config.CLOUDFLARE_ACCOUNT_ID = cloudflare_account_id
        config.PROVEEDOR_IA_ALTERNATIVO = proveedor_alternativo
        config.OLLAMA_HOST = ollama_host
        config.WHISPER_MODELO = modelo_whisper
        config.IDIOMA = idioma
        config.VOLUMEN_VOZ = volumen_voz
        config.VELOCIDAD_VOZ = velocidad_voz
        config.MICROFONO_SENSIBILIDAD = microfono_sensibilidad
        config.VOLUMEN_EFECTOS = volumen_efectos
        config.TEMA_HUD = tema_hud
        config.MODO_ENSEMBLE = modo_ensemble
        config.ESTRATEGIA_ENSEMBLE = estrategia_ensemble
        config.INICIO_AUTOMATICO = inicio_automatico
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
                    "tts_version": 2,
                    "elevenlabs_voice_id": voice_id,
                    "cartesia_voice_id": cartesia_voice_id,
                    "ubicacion": ubicacion,
                    "spotify_client_id": spotify_client_id,
                    "vision_modelo": vision_modelo,
                    "modelo_ollama": modelo_ollama,
                    "modelo_gemini": modelo_gemini,
                    "modelo_openai": modelo_openai,
                    "modelo_cursor": modelo_cursor,
                    "modelo_groq_api": modelo_groq_api,
                    "modelo_cloudflare": modelo_cloudflare,
                    "cloudflare_account_id": cloudflare_account_id,
                    "proveedor_ia_alternativo": proveedor_alternativo,
                    "ollama_host": ollama_host,
                    "modelo_whisper": modelo_whisper,
                    "idioma": idioma,
                    "volumen_voz": volumen_voz,
                    "velocidad_voz": velocidad_voz,
                    "microfono_sensibilidad": microfono_sensibilidad,
                    "volumen_efectos": volumen_efectos,
                    "tema_hud": tema_hud,
                    "modo_ensemble": modo_ensemble,
                    "estrategia_ensemble": estrategia_ensemble,
                    "inicio_automatico": inicio_automatico,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporal.replace(ruta)
        import tools

        if permiso_control_pc is not None:
            tools.establecer_permiso_control(
                permiso_control_pc
            )
            config.COMPARTIENDO_PANTALLA = permiso_control_pc
            self.bus.publish("pantalla", on=permiso_control_pc)
        self.elevenlabs_api_key = self._clave_guardada_o_entorno("elevenlabs")
        self.cartesia_api_key = self._clave_guardada_o_entorno("cartesia")
        config.CARTESIA_VOICE_ID = cartesia_voice_id
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
                    cartesia_api_key=self._clave_guardada_o_entorno("cartesia"),
                    cartesia_voice_id=config.CARTESIA_VOICE_ID,
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
        import tools

        if tools.permiso_control_pc() is not True and not self.bus.preguntar(
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
        nombres_ia = {
            "ollama": "Ollama",
            **{codigo: proveedor.nombre for codigo, proveedor in ai_providers.PROVEEDORES.items()},
        }
        nombres_tts = {
            "piper": "Piper",
            "windows": "Windows",
            "elevenlabs": "ElevenLabs",
            "cartesia": "Cartesia",
        }
        modelo = (
            config.MODELO
            if config.PROVEEDOR_IA == "ollama"
            else ai_providers.obtener_modelo(config.PROVEEDOR_IA)
        )

        self.bus.publish(
            "info",
            nombre=config.NOMBRE,
            modo=self.modo or "texto",
            live_activo=getattr(self, "live_activo", False),
            live_silenciado=getattr(self, "live_silenciado", False),
            ia=nombres_ia.get(config.PROVEEDOR_IA, config.PROVEEDOR_IA),
            ia_estado=self.ia_estado,
            ia_error=self.ia_error,
            modelo=modelo,
            tts=nombres_tts.get(config.PROVEEDOR_TTS, config.PROVEEDOR_TTS),
            github=bool(config.GITHUB_TOKEN),
            spotify=bool(spotyfyTools and spotyfyTools.configurado()),
            vision=bool(config.COMPARTIENDO_PANTALLA),
            tema=config.TEMA_HUD,
            volumen_efectos=config.VOLUMEN_EFECTOS,
            motor=self._estado_motor(),
        )

    def _estado_motor(self):
        import tools

        principal = config.PROVEEDOR_IA
        alternativo = config.PROVEEDOR_IA_ALTERNATIVO or ""
        activo = self.ia_proveedor_activo
        if activo == principal:
            estado_principal = self.ia_estado
        elif self.ia_estado == "conectado" and alternativo and activo == alternativo:
            estado_principal = "fallback"
        else:
            estado_principal = "error" if self.ia_estado == "error" else "sin_verificar"

        if not alternativo:
            estado_alternativo = "sin_configurar"
        elif activo == alternativo and self.ia_estado == "conectado":
            estado_alternativo = "conectado"
        elif activo == alternativo and self.ia_estado == "error":
            estado_alternativo = "error"
        elif alternativo and self._clave_para_core(alternativo):
            estado_alternativo = "configurado_sin_probar"
        else:
            estado_alternativo = "sin_credencial" if alternativo else "sin_configurar"

        if self.ia_estado == "error":
            modo = "offline"
        elif self.ia_estado == "conectado":
            modo = "local" if activo == "ollama" else "online"
        else:
            modo = "sin_verificar"

        resumen = tools.resumen_motor()
        return {
            "core": "activo" if self.asistente_activo else "disponible",
            "ia_principal": principal,
            "ia_principal_estado": estado_principal,
            "ia_secundaria": alternativo,
            "ia_secundaria_estado": estado_alternativo,
            "proveedor_activo": activo or "",
            "modo": modo,
            "live_activo": getattr(self, "live_activo", False),
            "live_silenciado": getattr(self, "live_silenciado", False),
            "herramientas": resumen["herramientas"],
            "acciones_recientes": resumen["acciones_recientes"],
            "errores_recientes": resumen["errores_recientes"],
            "error_registro": resumen["error_registro"],
            "latencia_ms": self.ultima_latencia_ms,
            "ia_error": self.ia_error,
        }

    def _accion_hud(self, nombre):
        """Acciones que pide el HUD (corren en un hilo del servidor)."""
        if nombre == "pantalla_sync":
            self.raiz.after(0, lambda: self.compartir_pantalla_var.set(config.COMPARTIENDO_PANTALLA))
            self._publicar_info()
        elif nombre == "ajustes":
            self.bus.publish("ajustes")
        elif nombre == "preparar_vision":
            self.raiz.after(0, self.preparar_vision)
        elif nombre == "iniciar_voz":
            self.raiz.after(0, self._iniciar_desde_hud)
        elif nombre == "live_start":
            self.raiz.after(0, self.iniciar_live)
        elif nombre == "live_mute":
            self.raiz.after(0, self.silenciar_live)
        elif nombre == "live_stop":
            self.raiz.after(0, self.finalizar_live)
        elif nombre == "live_select_gemini":
            self.raiz.after(0, self._seleccionar_proveedor_live, "gemini")
        elif nombre == "live_select_openai":
            self.raiz.after(0, self._seleccionar_proveedor_live, "openai")
        elif nombre.startswith("live_voice_"):
            voces = {
                "live_voice_gemini_kore": ("gemini", "Kore"),
                "live_voice_gemini_puck": ("gemini", "Puck"),
                "live_voice_gemini_aoede": ("gemini", "Aoede"),
                "live_voice_openai_marin": ("openai", "marin"),
                "live_voice_openai_cedar": ("openai", "cedar"),
            }
            seleccion = voces.get(nombre)
            if seleccion:
                self.raiz.after(0, self._seleccionar_voz_live, *seleccion)
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
                probar_ia=self._probar_ia_hud,
                adjuntar_archivos=self.adjuntar_desde_hud,
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
        hud_activo = self.hud_servidor and self.hud_servidor.hud_activo()
        if self.bus.hay_hud() or hud_activo:
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
                if config.INICIO_AUTOMATICO:
                    self._hud_visto = False
                    self._hud_ausente = None
                    self._hud_lanzado_en = None
                    if self.live_activo:
                        self.finalizar_live()
                    with self._modo_lock:
                        if self.asistente_activo:
                            self.modo = "espera"
                    self._publicar_info()
                else:
                    self._hud_visto = False
                    self._hud_ausente = None
                    self.cerrar()
                    return
        self.raiz.after(2000, self._hud_vigilar)

    def cerrar(self):
        manager = self._live_manager
        self._live_manager = None
        if manager is not None:
            manager.stop()
        self.live_activo = False
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


def _solicitar_nombre_usuario(raiz):
    ruta = config.CARPETA_DATOS / "preferencias_usuario.json"
    try:
        nombre = preferencias_usuario.cargar_nombre_usuario(ruta)
    except (OSError, ValueError) as error:
        messagebox.showerror(
            config.NOMBRE,
            f"No pude leer el nombre guardado: {error}",
            parent=raiz,
        )
        nombre = ""
    if nombre:
        return nombre

    while True:
        nombre = simpledialog.askstring(
            "Bienvenido a Jarvis",
            "¿Cómo querés que te llame?",
            parent=raiz,
        )
        if nombre is None:
            return ""
        nombre = nombre.strip()
        if not nombre:
            messagebox.showwarning(
                config.NOMBRE,
                "Escribí un nombre o elegí Cancelar para continuar sin guardarlo.",
                parent=raiz,
            )
            continue
        if len(nombre) > 60:
            messagebox.showwarning(
                config.NOMBRE,
                "El nombre puede tener como máximo 60 caracteres.",
                parent=raiz,
            )
            continue
        try:
            return preferencias_usuario.guardar_nombre_usuario(ruta, nombre)
        except OSError as error:
            messagebox.showerror(
                config.NOMBRE,
                f"No pude guardar tu nombre: {error}",
                parent=raiz,
            )
            return ""


def main():
    parser = argparse.ArgumentParser(description="Jarvis para Windows")
    parser.add_argument(
        "--background",
        action="store_true",
        help="inicia oculto y espera la palabra de activación si está habilitada",
    )
    parser.add_argument(
        "--automation-service",
        action="store_true",
        help="ejecuta el servicio independiente de automatizaciones persistentes",
    )
    args = parser.parse_args()
    if args.automation_service:
        import automation_service

        automation_service.ejecutar_servicio()
        return
    raiz = tk.Tk()
    raiz.withdraw()  # no mostrar esta ventana: la interfaz principal es el HUD
    raiz.iconbitmap(str(Path(__file__).resolve().parent / "jarvis.ico"))
    candado = _instancia_unica()
    if candado is None:
        messagebox.showinfo(
            config.NOMBRE,
            f"{config.NOMBRE} ya está en ejecución.\n\nSi no ves su ventana, cerralo desde el "
            "Administrador de tareas (procesos «Python») y volvé a abrir run.vbs.",
        )
        raiz.destroy()
        return
    config.NOMBRE_USUARIO = _solicitar_nombre_usuario(raiz)
    lanzador = Lanzador(raiz)
    lanzador._candado = candado  # se mantiene abierto mientras viva el programa
    if config.INICIO_AUTOMATICO:
        if not args.background:
            raiz.after(300, lanzador.abrir_hud_inicial)
        raiz.after(500, lanzador.iniciar_por_activacion)
    else:
        raiz.after(300, lanzador.abrir_hud_inicial)
    raiz.mainloop()


if __name__ == "__main__":
    main()
