"""Cerebro: conversa con Ollama o modelos cloud compatibles con OpenAI."""
import datetime
import json
import os
import sys

import config
import tools

PROMPT = """Sos {nombre}, un asistente personal que corre en la PC Windows de tu usuario.
Respondé en español rioplatense, breve y directo (2 o 3 oraciones como máximo salvo que pidan más).
Tus respuestas se leen en voz alta: no uses markdown, listas, símbolos ni emojis.
Cuando el usuario pida una acción sobre la PC, usá las herramientas. Nunca inventes resultados:
si una herramienta falla, decilo. Si no hace falta ninguna herramienta, respondé directamente.
Para cerrar aplicaciones usa cerrar_aplicacion; para avisos relativos usa poner_recordatorio y para una hora exacta usa crear_alerta. Los enlaces se abren con abrir_web y las búsquedas con buscar_en_google.
Fecha y hora actual: {ahora}.
Datos que el usuario te pidió recordar:
{memoria}"""


class Cerebro:
    def __init__(self, provider=None, api_key=None):
        self.proveedor = (provider or config.PROVEEDOR_IA).lower()
        self.api_key = api_key
        self.cliente = None
        self.historial = []
        if self.proveedor == "ollama":
            import ollama

            self.cliente = ollama.Client(host=config.OLLAMA_HOST)
        elif self.proveedor in ("gemini", "openai"):
            try:
                from openai import OpenAI
            except ModuleNotFoundError as error:
                if error.name != "openai":
                    raise
                raise RuntimeError(
                    "Falta la dependencia del proveedor cloud. Cerrá Jarvis y ejecutá "
                    f'"{sys.executable}" -m pip install -r requirements.txt; '
                    "después volvé a abrir run.bat."
                ) from error

            clave = api_key or (
                os.environ.get("GEMINI_API_KEY")
                if self.proveedor == "gemini"
                else os.environ.get("OPENAI_API_KEY")
            )
            if not clave:
                nombre = "GEMINI_API_KEY" if self.proveedor == "gemini" else "OPENAI_API_KEY"
                raise ValueError(f"Falta la clave de API. Ingresala en Jarvis o configurá {nombre}.")
            opciones = {"api_key": clave}
            if self.proveedor == "gemini":
                opciones["base_url"] = "https://generativelanguage.googleapis.com/v1beta/openai/"
            self.cliente = OpenAI(**opciones)
        else:
            raise ValueError(f"Proveedor de IA no válido: {self.proveedor}")

    def verificar(self):
        """Devuelve None si todo está bien, o un mensaje de error útil."""
        if self.proveedor != "ollama":
            return None
        try:
            modelos = [m.model for m in self.cliente.list().models]
        except Exception:
            return "No pude conectar con Ollama. ¿Está instalado y corriendo? (https://ollama.com)"
        if ":" in config.MODELO:
            presente = config.MODELO in modelos
        else:
            presente = any(m.split(":")[0] == config.MODELO for m in modelos)
        if not presente:
            return f"Falta el modelo. Ejecutá en una terminal: ollama pull {config.MODELO}"
        return None

    def _sistema(self):
        memoria = "\n".join(f"- {d}" for d in tools.cargar_memoria()) or "(nada todavía)"
        ahora = datetime.datetime.now().strftime("%A %d/%m/%Y %H:%M")
        return {"role": "system", "content": PROMPT.format(nombre=config.NOMBRE, ahora=ahora, memoria=memoria)}

    def _recortar(self):
        if len(self.historial) <= 30:
            return
        resto = self.historial[-24:]
        while resto and resto[0]["role"] != "user":  # no cortar a mitad de una herramienta
            resto = resto[1:]
        self.historial = resto

    def responder(self, texto):
        if self.proveedor != "ollama":
            return self._responder_api(texto)
        self.historial.append({"role": "user", "content": texto})
        self._recortar()
        for _ in range(6):  # máx. rondas de herramientas por pedido
            resp = self.cliente.chat(
                model=config.MODELO,
                messages=[self._sistema()] + self.historial,
                tools=tools.esquemas(),
            )
            msg = resp.message
            entrada = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entrada["tool_calls"] = [
                    {"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
                    for c in msg.tool_calls
                ]
            self.historial.append(entrada)
            if not msg.tool_calls:
                return (msg.content or "").strip() or "Listo."
            for c in msg.tool_calls:
                nombre = c.function.name
                resultado = tools.ejecutar(nombre, dict(c.function.arguments))
                print(f"  [herramienta] {nombre} -> {resultado[:120]}")
                self.historial.append({"role": "tool", "tool_name": nombre, "content": resultado})
        return "Me trabé con ese pedido, ¿podés reformularlo?"

    def _responder_api(self, texto):
        self.historial.append({"role": "user", "content": texto})
        self._recortar()
        modelo = config.MODELO_GEMINI if self.proveedor == "gemini" else config.MODELO_OPENAI
        for _ in range(6):
            opciones = {
                "model": modelo,
                "messages": [self._sistema()] + self.historial,
                "tools": tools.esquemas(),
                "tool_choice": "auto",
            }
            if self.proveedor == "openai":
                opciones["reasoning_effort"] = "none"
                opciones["max_completion_tokens"] = 400
            else:
                opciones["max_tokens"] = 400
            try:
                respuesta = self.cliente.chat.completions.create(**opciones)
            except Exception as error:
                raise RuntimeError(self._explicar_error_api(error)) from error
            mensaje = respuesta.choices[0].message
            llamada = mensaje.model_dump(exclude_none=True)
            self.historial.append(llamada)
            if not mensaje.tool_calls:
                return (mensaje.content or "").strip() or "Listo."
            for llamada_herramienta in mensaje.tool_calls:
                try:
                    argumentos = json.loads(llamada_herramienta.function.arguments or "{}")
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"El modelo devolvió argumentos inválidos para "
                        f"{llamada_herramienta.function.name}: {error}"
                    ) from error
                nombre = llamada_herramienta.function.name
                resultado = tools.ejecutar(nombre, argumentos)
                print(f"  [herramienta] {nombre} -> {resultado[:120]}")
                self.historial.append({
                    "role": "tool",
                    "tool_call_id": llamada_herramienta.id,
                    "content": resultado,
                })
        return "Me trabé con ese pedido, ¿podés reformularlo?"

    def _explicar_error_api(self, error):
        codigo = getattr(error, "status_code", None)
        detalle = getattr(error, "message", None) or str(error)
        detalle = detalle.replace(self.api_key or "", "[API key oculta]") if self.api_key else detalle
        detalle = detalle[:500]
        if self.proveedor == "openai":
            if codigo == 401:
                return "OpenAI rechazó la API key (401). Verificá que sea una clave de API válida y activa."
            if codigo == 403:
                return "OpenAI denegó el acceso (403). Revisá permisos del proyecto y acceso al modelo."
            if codigo == 404:
                return (
                    f"OpenAI no encontró el modelo '{config.MODELO_OPENAI}' (404). "
                    "Revisá MODELO_OPENAI en config.py y el acceso de tu proyecto."
                )
            if codigo == 429:
                return (
                    "OpenAI rechazó la solicitud por cuota o límite (429). "
                    "Revisá facturación y límites de la API."
                )
        elif codigo == 401:
            return "Gemini rechazó la API key (401). Revisá que sea una clave válida de Google AI Studio."
        elif codigo == 403:
            return "Gemini denegó el acceso (403). Revisá permisos, región y habilitación de la API."
        elif codigo == 404:
            return (
                f"Gemini no encontró el modelo '{config.MODELO_GEMINI}' (404). "
                "Revisá MODELO_GEMINI en config.py."
            )
        elif codigo == 429:
            return "Gemini rechazó la solicitud por cuota o límite (429). Revisá la cuota de tu proyecto."
        if codigo is not None:
            return f"Error de API de {self.proveedor} (HTTP {codigo}): {detalle}"
        if "connection" in type(error).__name__.lower() or "timeout" in type(error).__name__.lower():
            return f"No pude conectar con {self.proveedor}. Revisá internet y volvé a intentar."
        return f"Error de {self.proveedor}: {detalle}"
