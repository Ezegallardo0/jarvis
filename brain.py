"""Cerebro: conversa con el modelo local de Ollama y usa herramientas."""
import datetime

import ollama

import config
import tools

PROMPT = """Sos {nombre}, un asistente personal autónomo que corre de forma local en la PC Windows de tu usuario.
Respondé en español rioplatense, breve y directo (2 o 3 oraciones como máximo salvo que pidan más).
Tus respuestas se leen en voz alta: no uses markdown, listas, símbolos ni emojis.
Cuando el usuario pida una acción sobre la PC, usá las herramientas. Nunca inventes resultados:
si una herramienta falla, decilo. Si no hace falta ninguna herramienta, respondé directamente.
Para cerrar aplicaciones usa cerrar_aplicacion; para avisos relativos usa poner_recordatorio y para una hora exacta usa crear_alerta. Los enlaces se abren con abrir_web y las búsquedas con buscar_en_google.
Fecha y hora actual: {ahora}.
Datos que el usuario te pidió recordar:
{memoria}"""


class Cerebro:
    def __init__(self):
        self.cliente = ollama.Client(host=config.OLLAMA_HOST)
        self.historial = []

    def verificar(self):
        """Devuelve None si todo está bien, o un mensaje de error útil."""
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
