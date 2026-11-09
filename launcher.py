"""Interfaz básica para elegir dispositivos de audio y arrancar Jarvis."""
import json
import os
import queue
import sys
import threading
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
import voice


class Lanzador:
    def __init__(self, raiz):
        self.raiz = raiz
        raiz.title(f"{config.NOMBRE} - Inicio")
        raiz.resizable(True, True)
        raiz.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.cerrando = threading.Event()
        self.pedidos = queue.Queue()
        self.modo = None

        principal = ttk.Frame(raiz, padding=16)
        self.principal = principal
        principal.grid(sticky="nsew")
        ttk.Label(principal, text=f"{config.NOMBRE}", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        entradas, salidas = self._dispositivos()
        self.entradas = {"Predeterminado de Windows": ""}
        self.salidas = {"Predeterminada de Windows": None}
        self.entradas.update(entradas)
        self.salidas.update(salidas)
        microfono, salida, proveedor = self._leer_seleccion()
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
        self.api_key_var = tk.StringVar(value=self._clave_entorno(proveedor))

        ttk.Label(principal, text="Micrófono (entrada)").grid(row=1, column=0, sticky="w", pady=4)
        self.entrada_menu = ttk.Combobox(
            principal, textvariable=self.entrada_var, values=list(self.entradas), state="readonly", width=56
        )
        self.entrada_menu.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(principal, text="Parlante/auricular (salida)").grid(row=2, column=0, sticky="w", pady=4)
        self.salida_menu = ttk.Combobox(
            principal, textvariable=self.salida_var, values=list(self.salidas), state="readonly", width=56
        )
        self.salida_menu.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(principal, text="Modelo de IA").grid(row=3, column=0, sticky="w", pady=4)
        self.proveedor_menu = ttk.Combobox(
            principal,
            textvariable=self.proveedor_var,
            values=list(self.proveedores),
            state="readonly",
            width=56,
        )
        self.proveedor_menu.grid(row=3, column=1, sticky="ew", pady=4)
        self.proveedor_menu.bind("<<ComboboxSelected>>", self._cambiar_proveedor)

        ttk.Label(principal, text="API key (no se guarda)").grid(row=4, column=0, sticky="w", pady=4)
        self.api_key_entry = ttk.Entry(principal, textvariable=self.api_key_var, show="*", width=56)
        self.api_key_entry.grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Label(
            principal,
            text="Obtené la clave en Google AI Studio o en la plataforma de OpenAI. No se guarda.",
        ).grid(row=5, column=0, columnspan=2, sticky="w")
        self._cambiar_proveedor()

        acciones = ttk.Frame(principal)
        acciones.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(16, 8))
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
        ttk.Button(acciones, text="Iniciar con voz", command=lambda: self.iniciar(False)).grid(
            row=0, column=3, padx=6
        )
        ttk.Button(acciones, text="Iniciar en modo texto", command=lambda: self.iniciar(True)).grid(
            row=0, column=4, padx=(6, 0)
        )

        self.estado = tk.StringVar(value="Seleccioná los dispositivos y probalos antes de iniciar.")
        ttk.Label(principal, textvariable=self.estado, wraplength=650).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            principal,
            text="La prueba del micrófono dura 5 segundos: hablá durante la medición.",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

        if not entradas:
            self.estado.set("Windows no informa entradas de audio. Revisá permisos y conexión del micrófono.")
        raiz.columnconfigure(0, weight=1)
        raiz.rowconfigure(0, weight=1)

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
        return next((nombre for nombre, indice in opciones.items() if indice == valor), next(iter(opciones)))

    @staticmethod
    def _clave_entorno(proveedor):
        variable = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(proveedor, "")
        return os.environ.get(variable, "") if variable else ""

    def _cambiar_proveedor(self, evento=None):
        etiqueta = evento.widget.get() if evento else self.proveedor_var.get()
        proveedor = self.proveedores[etiqueta]
        self.api_key_var.set(self._clave_entorno(proveedor))
        if proveedor == "ollama":
            self.api_key_entry.state(["disabled"])
        else:
            self.api_key_entry.state(["!disabled"])

    @staticmethod
    def _leer_seleccion():
        ruta = config.CARPETA_DATOS / "dispositivos_audio.json"
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return config.MICROFONO, config.AUDIO_SALIDA, config.PROVEEDOR_IA
        except (OSError, ValueError) as error:
            messagebox.showerror("Configuración de audio", f"No pude leer la selección guardada:\n{error}")
            return config.MICROFONO, config.AUDIO_SALIDA, config.PROVEEDOR_IA
        return (
            datos.get("microfono", config.MICROFONO),
            datos.get("salida", config.AUDIO_SALIDA),
            datos.get("proveedor", config.PROVEEDOR_IA),
        )

    def _guardar(self):
        microfono = self.entradas[self.entrada_var.get()]
        salida = self.salidas[self.salida_var.get()]
        proveedor = self.proveedores[self.proveedor_var.get()]
        config.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
        ruta = config.CARPETA_DATOS / "dispositivos_audio.json"
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(
                {"microfono": microfono, "salida": salida, "proveedor": proveedor},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporal.replace(ruta)
        config.MICROFONO = microfono
        config.AUDIO_SALIDA = salida
        config.PROVEEDOR_IA = proveedor
        return microfono, salida, proveedor

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
        proveedor = self.proveedores[self.proveedor_var.get()]
        api_key = self.api_key_var.get().strip()
        if proveedor != "ollama" and not api_key:
            messagebox.showerror(
                "Falta la API key",
                "Pegá tu API key en el campo correspondiente. La clave se usa solo durante esta sesión y no se guarda en disco.",
                parent=self.raiz,
            )
            return
        try:
            self._guardar()
        except (OSError, KeyError) as error:
            messagebox.showerror("Configuración de audio", str(error))
            return
        self.modo = "texto" if texto else "voz"
        config.USAR_VOZ = not texto
        self.principal.grid_remove()
        self._crear_chat()
        proveedor_nombre = self.proveedor_var.get()
        self.estado_chat.set(f"Conectando con {proveedor_nombre}...")
        threading.Thread(
            target=self._iniciar_asistente,
            args=(proveedor, api_key),
            daemon=True,
        ).start()

    def _crear_chat(self):
        self.chat = ttk.Frame(self.raiz, padding=12)
        self.chat.grid(row=0, column=0, sticky="nsew")
        self.chat.columnconfigure(0, weight=1)
        self.chat.rowconfigure(1, weight=1)
        ttk.Label(
            self.chat,
            text=f"{config.NOMBRE} - modo {self.modo}",
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        conversacion = ttk.Frame(self.chat)
        conversacion.grid(row=1, column=0, sticky="nsew")
        conversacion.columnconfigure(0, weight=1)
        conversacion.rowconfigure(0, weight=1)
        self.historial_ui = tk.Text(conversacion, wrap="word", state="disabled", height=20)
        self.historial_ui.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(conversacion, command=self.historial_ui.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.historial_ui.configure(yscrollcommand=scroll.set)
        entrada = ttk.Frame(self.chat)
        entrada.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        entrada.columnconfigure(0, weight=1)
        self.mensaje = tk.StringVar()
        self.campo_mensaje = ttk.Entry(entrada, textvariable=self.mensaje)
        self.campo_mensaje.grid(row=0, column=0, sticky="ew")
        self.campo_mensaje.bind("<Return>", lambda _: self.enviar())
        ttk.Button(entrada, text="Enviar", command=self.enviar).grid(row=0, column=1, padx=(8, 0))
        self.estado_chat = tk.StringVar()
        ttk.Label(self.chat, textvariable=self.estado_chat, wraplength=680).grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )
        self.campo_mensaje.focus_set()

    def _agregar_mensaje(self, quien, texto):
        def agregar():
            if not self.cerrando.is_set():
                self.historial_ui.configure(state="normal")
                self.historial_ui.insert("end", f"{quien}: {texto}\n\n")
                self.historial_ui.configure(state="disabled")
                self.historial_ui.see("end")

        self.raiz.after(0, agregar)

    def _estado(self, texto):
        if not self.cerrando.is_set():
            self.raiz.after(0, self.estado_chat.set, texto)

    def enviar(self):
        texto = self.mensaje.get().strip()
        if not texto:
            return
        self.mensaje.set("")
        self._agregar_mensaje("Tú", texto)
        self.pedidos.put((texto, "texto"))

    def _confirmar(self, texto):
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

        tools.NOTIFICAR = self._notificar
        tools.CONFIRMAR = self._confirmar
        try:
            cerebro = Cerebro(provider=proveedor, api_key=api_key or None)
        except Exception as error:
            self._agregar_mensaje(config.NOMBRE, f"No pude iniciar el modelo: {error}")
            self._estado("Revisá el proveedor y la API key y volvé a iniciar.")
            return
        error = cerebro.verificar()
        if error:
            self._agregar_mensaje(config.NOMBRE, error)
            self._estado("No se pudo iniciar. Corregí el error y volvé a abrir Jarvis.")
            return

        oido = None
        if self.modo == "voz":
            self._estado("Iniciando micrófono y reconocimiento de voz...")
            try:
                oido = voice.Oido()
            except Exception as error:
                self._agregar_mensaje(config.NOMBRE, f"No pude iniciar la voz: {error}")
                self._estado("Podés seguir conversando por texto en esta misma ventana.")
                config.USAR_VOZ = False
        self.oido = oido

        self._agregar_mensaje(config.NOMBRE, "En línea. Escribime abajo.")
        self._estado(
            "Escuchando: decí «Jarvis» y luego tu pedido."
            if oido
            else "Modo texto listo."
        )
        if oido:
            threading.Thread(target=self._escuchar, args=(oido,), daemon=True).start()

        while not self.cerrando.is_set():
            try:
                pedido, origen = self.pedidos.get(timeout=0.2)
            except queue.Empty:
                continue
            if origen == "voz":
                self._agregar_mensaje("Tú (voz)", pedido)
            self._estado("Procesando...")
            try:
                respuesta = cerebro.responder(pedido)
            except Exception as error:
                respuesta = f"Tuve un problema: {error}"
            self._agregar_mensaje(config.NOMBRE, respuesta)
            if self.modo == "voz" and config.USAR_VOZ:
                self._hablar(respuesta)
            self._estado("Escuchando: decí «Jarvis» y luego tu pedido." if oido else "Listo.")

    def _notificar(self, texto):
        self._agregar_mensaje(config.NOMBRE, texto)
        if self.modo == "voz" and config.USAR_VOZ:
            self._hablar(texto)

    def _hablar(self, texto):
        oido = getattr(self, "oido", None)
        if oido:
            oido.pausar_escucha.set()
        try:
            voice.hablar(texto)
        finally:
            if oido:
                oido.pausar_escucha.clear()

    def _escuchar(self, oido):
        while not self.cerrando.is_set():
            try:
                pedido = oido.esperar_activacion(
                    lambda texto: self._estado(f"Reconocimiento: «{texto}»")
                )
                if not pedido:
                    self._hablar("Decime.")
                    pedido = oido.transcribir(oido.grabar_frase(espera_max=8))
                if pedido and not self.cerrando.is_set():
                    self.pedidos.put((pedido, "voz"))
            except Exception as error:
                self._estado(f"Se interrumpió el micrófono: {error}. Podés escribir abajo.")
                return

    def cerrar(self):
        self.cerrando.set()
        self.raiz.destroy()


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--texto", action="store_true")
    parser.add_argument("--probar-microfono", action="store_true")
    args = parser.parse_args()
    raiz = tk.Tk()
    lanzador = Lanzador(raiz)
    if args.texto:
        raiz.after(200, lanzador.iniciar, True)
    elif args.probar_microfono:
        raiz.after(200, lanzador.probar_microfono)
    raiz.mainloop()


if __name__ == "__main__":
    main()
