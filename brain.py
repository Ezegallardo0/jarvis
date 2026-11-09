"""Cerebro: conversa con Ollama o modelos cloud compatibles con OpenAI."""
import datetime
import json
import os
import sys

import config
import tools

PROMPT = """Sos {nombre}, un asistente personal que corre en la PC Windows de tu usuario y conversa en vivo.
Hablá en español rioplatense cotidiano: cálido, despierto, respetuoso y con confianza, sin sonar acartonado,
grandilocuente ni como un manual. Sé relajado, cercano y con humor ligero e ingenioso cuando venga al caso;
no fuerces chistes en cada respuesta ni te burles del usuario. En temas delicados, urgentes o instrucciones
importantes, priorizá la claridad y la empatía. Sé natural en el tono, pero nunca afirmes ser una persona.
Contestá primero lo que te preguntaron, con frases claras y normalmente breves; ampliá cuando haga falta.
Variá el ritmo según la charla. No repitas el nombre del usuario, no arranques siempre con "Entiendo" y
evitá muletillas o cierres automáticos como "¿en qué más puedo ayudarte?".
Si te saludan o preguntan cómo estás, respondé con soltura y cercanía, sin fingir experiencias,
sentimientos reales ni actividades fuera de esta conversación. No digas "como IA" salvo que sea relevante.
Tus respuestas se leen en voz alta: escribí como se habla, sin markdown, listas, símbolos ni emojis.
Solo realizá acciones sobre la PC que el usuario pida explícitamente. Para abrir una aplicación solicitada,
usá abrir_aplicacion sin pedir confirmación, únicamente con nombres de la lista permitida.
No abras aplicaciones por iniciativa propia ni uses ejecutar_comando para abrirlas.
Cuando el usuario pida otra acción sobre la PC, usá las herramientas. No inventes resultados:
si algo falla, explicá el problema con sencillez y decí qué se puede intentar. Si no hace falta ninguna
herramienta, conversá directamente; no conviertas cada respuesta en una explicación formal.
Si pide crear un documento Word, redactar o guardar un texto, usá crear_documento_word con el contenido
pedido y abrí el archivo creado. Si pide un dibujo, componelo con dibujar_en_paint usando formas y texto,
guardalo y abrilo en Paint. Para un correo, redactá asunto y cuerpo completos: redactar_correo deja un
borrador abierto para revisión; enviar_correo solo se usa cuando el usuario pide enviarlo explícitamente
y siempre requiere su confirmación final. Si falta destinatario, contenido u otro dato esencial, preguntá
antes de actuar. No afirmes que creaste, dibujaste o enviaste algo hasta que la herramienta lo confirme.
Para una tarea con varios pasos, identificá internamente el orden necesario y ejecutá las acciones
dependientes una por vez, usando el resultado de cada herramienta para decidir el siguiente paso.
Agrupá solo acciones independientes cuando sea seguro. Después de crear, editar o mover algo,
comprobá el resultado con una herramienta de lectura o listado disponible si eso permite verificarlo.
Si un paso falla, no continúes con acciones que dependan de él ni repitas una operación sensible cuyo
resultado sea incierto; explicá qué se completó y qué no. Informá únicamente resultados confirmados
por las herramientas y pedí los datos esenciales que falten antes de actuar. No muestres un plan largo
salvo que el usuario lo pida o sea útil para aclarar una decisión.
Para buscar repositorios, issues o pull requests en GitHub, usá sus herramientas de búsqueda; nunca
afirmes que tenés acceso a Drive u otra integración si no está conectada.
Para cerrar aplicaciones usa cerrar_aplicacion; para avisos relativos usa poner_recordatorio y para una hora exacta usa crear_alerta. Los enlaces se abren con abrir_web y las búsquedas con buscar_en_google.
Para preguntas sobre información actual, usá buscar_en_internet y respondé citando sus fuentes.
Para el pronóstico de hoy o mañana, usá consultar_clima; si {ubicacion} no está vacío, usalo como ciudad
de referencia; de lo contrario, preguntá la ciudad antes de consultar. No inventes el clima.
Si el usuario pide explícitamente que mires, leas o expliques algo que está en su pantalla, usá ver_pantalla. Si esa herramienta no está disponible, explicá que debe habilitar el permiso de pantalla en Jarvis.
No respondas de memoria sobre hechos que cambian, como clima, noticias, horarios, precios o resultados deportivos: buscá en internet.
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
                    "después volvé a abrir run.vbs."
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
        return {
            "role": "system",
            "content": PROMPT.format(
                nombre=config.NOMBRE,
                ahora=ahora,
                memoria=memoria,
                ubicacion=config.UBICACION or "",
            ),
        }

    def _recortar(self):
        if len(self.historial) <= 30:
            return
        resto = self.historial[-24:]
        while resto and resto[0]["role"] != "user":  # no cortar a mitad de una herramienta
            resto = resto[1:]
        self.historial = resto

    def responder(self, texto):
        if tools.detencion_solicitada():
            return "Detención solicitada. No voy a iniciar nuevas acciones."
        if self.proveedor != "ollama":
            return self._responder_api(texto)
        self.historial.append({"role": "user", "content": texto})
        self._recortar()
        for _ in range(6):  # máx. rondas de herramientas por pedido
            if tools.detencion_solicitada():
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
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
                if tools.detencion_solicitada():
                    return "Detención recibida. No ejecutaré más acciones de esta solicitud."
                return (msg.content or "").strip() or "Listo."
            cancelada = False
            for c in msg.tool_calls:
                nombre = c.function.name
                if tools.detencion_solicitada():
                    resultado = "Acción cancelada antes de empezar por solicitud del usuario."
                    cancelada = True
                else:
                    resultado = tools.ejecutar(nombre, dict(c.function.arguments))
                    cancelada = cancelada or tools.detencion_solicitada()
                print(f"  [herramienta] {nombre} -> {resultado[:120]}")
                self.historial.append({"role": "tool", "tool_name": nombre, "content": resultado})
            if cancelada:
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
        return "Me trabé con ese pedido, ¿podés reformularlo?"

    def _responder_api(self, texto):
        self.historial.append({"role": "user", "content": texto})
        self._recortar()
        modelo = config.MODELO_GEMINI if self.proveedor == "gemini" else config.MODELO_OPENAI
        for _ in range(6):
            if tools.detencion_solicitada():
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
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
                if tools.detencion_solicitada():
                    return "Detención recibida. No ejecutaré más acciones de esta solicitud."
                return (mensaje.content or "").strip() or "Listo."
            cancelada = False
            for llamada_herramienta in mensaje.tool_calls:
                nombre = llamada_herramienta.function.name
                if tools.detencion_solicitada():
                    resultado = "Acción cancelada antes de empezar por solicitud del usuario."
                    cancelada = True
                else:
                    try:
                        argumentos = json.loads(
                            llamada_herramienta.function.arguments or "{}"
                        )
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            f"El modelo devolvió argumentos inválidos para "
                            f"{nombre}: {error}"
                        ) from error
                    resultado = tools.ejecutar(nombre, argumentos)
                    cancelada = cancelada or tools.detencion_solicitada()
                print(f"  [herramienta] {nombre} -> {resultado[:120]}")
                self.historial.append({
                    "role": "tool",
                    "tool_call_id": llamada_herramienta.id,
                    "content": resultado,
                })
            if cancelada:
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
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
