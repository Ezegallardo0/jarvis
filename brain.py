"""Cerebro: conversa con Ollama o modelos cloud compatibles con OpenAI.

Cuando hay 2 o más API keys configuradas se activa el modo Ensemble:
las IAs trabajan en paralelo (o en cadena) para ofrecer mejores respuestas.
"""
import concurrent.futures
import datetime
import json
import os
import sys

import ai_providers
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

# Prompt de síntesis para el modo ensemble "mejor"
PROMPT_SINTESIS = """Recibiste {n} respuestas de distintas IAs al mismo mensaje del usuario.
Tu tarea es sintetizar la mejor respuesta posible:
- Tomá lo más preciso, útil y completo de cada respuesta.
- Si hay contradicciones, elegí la versión más confiable o mencionala brevemente.
- Mantené el tono conversacional en español rioplatense, sin markdown ni emojis.
- Sé conciso; no expliques que sos una síntesis ni menciones las fuentes.

Respuestas recibidas:
{respuestas}"""


def _sistema_base():
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


class Cerebro:
    """Cerebro único: un proveedor principal con fallback opcional.

    Se usa cuando solo hay 1 API key o el modo ensemble está desactivado.
    """

    def __init__(
        self,
        provider=None,
        api_key=None,
        proveedor_alternativo=None,
        clave_alternativa=None,
    ):
        self.proveedor = None
        self.api_key = None
        self.modelo = None
        self.cliente = None
        self.proveedor_alternativo = None
        self.clave_alternativa = None
        self.historial = []
        self.configurar(
            provider or config.PROVEEDOR_IA,
            api_key,
            proveedor_alternativo,
            clave_alternativa,
        )

    def configurar(
        self,
        provider,
        api_key=None,
        proveedor_alternativo=None,
        clave_alternativa=None,
    ):
        proveedor = (provider or "").lower()
        modelo = config.MODELO if proveedor == "ollama" else ai_providers.obtener_modelo(proveedor)
        if self.proveedor is not None and self.proveedor != proveedor:
            self._normalizar_historial_cambio_proveedor()
        if proveedor == "ollama":
            if (
                self.proveedor != proveedor
                or self.cliente is None
                or getattr(self, "ollama_host", None) != config.OLLAMA_HOST
            ):
                try:
                    import ollama
                except ModuleNotFoundError as error:
                    if error.name != "ollama":
                        raise
                    raise RuntimeError(
                        "Falta Ollama. Cerrá Jarvis y ejecutá "
                        f'"{sys.executable}" -m pip install -r requirements.txt.'
                    ) from error
                self.cliente = ollama.Client(host=config.OLLAMA_HOST)
                self.ollama_host = config.OLLAMA_HOST
        else:
            if not api_key:
                variable = ai_providers.PROVEEDORES[proveedor].variable_entorno
                api_key = os.environ.get(variable, "")
            if not api_key:
                try:
                    import keyring
                    if sys.platform == "win32":
                        from keyring.backends.Windows import WinVaultKeyring
                        keyring.set_keyring(WinVaultKeyring())
                    api_key = keyring.get_password("Jarvis", proveedor) or ""
                except Exception:
                    pass
            if not api_key:
                nombre = ai_providers.PROVEEDORES[proveedor].nombre
                raise ValueError(f"Falta la API key de {nombre}. Ingresala desde Configuración.")
            if self.proveedor != proveedor or self.api_key != api_key or self.cliente is None:
                try:
                    self.cliente = ai_providers.crear_cliente(proveedor, api_key)
                except ModuleNotFoundError as error:
                    if error.name != "openai":
                        raise
                    raise RuntimeError(
                        "Falta la dependencia del proveedor cloud. Cerrá Jarvis y ejecutá "
                        f'"{sys.executable}" -m pip install -r requirements.txt; '
                        "después volvé a abrir run.vbs."
                    ) from error
        self.proveedor = proveedor
        self.api_key = api_key
        self.modelo = modelo
        self.proveedor_alternativo = proveedor_alternativo or None
        self.clave_alternativa = clave_alternativa or None

    def _normalizar_historial_cambio_proveedor(self):
        historial = []
        for entrada in self.historial:
            rol = entrada.get("role")
            if rol == "user":
                historial.append({"role": "user", "content": entrada.get("content", "")})
            elif rol == "assistant" and not entrada.get("tool_calls"):
                historial.append({"role": "assistant", "content": entrada.get("content", "")})
        self.historial = historial

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
        return _sistema_base()

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
        herramientas_ejecutadas = False
        for _ in range(6):  # máx. rondas de herramientas por pedido
            if tools.detencion_solicitada():
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
            try:
                resp = self.cliente.chat(
                    model=config.MODELO,
                    messages=[self._sistema()] + self.historial,
                    tools=tools.esquemas(),
                )
            except Exception as error:
                if not herramientas_ejecutadas and self._activar_fallback():
                    nombre = ai_providers.PROVEEDORES[self.proveedor].nombre
                    try:
                        respuesta = self._responder_api(texto, usuario_agregado=True)
                    except Exception as error_alternativo:
                        raise RuntimeError(
                            f"Ollama no respondió y el proveedor alternativo también falló: "
                            f"{error_alternativo}"
                        ) from error
                    return f"Ollama no respondió; usé {nombre}. {respuesta}"
                raise
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
                herramientas_ejecutadas = True
            if cancelada:
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
        return "Me trabé con ese pedido, ¿podés reformularlo?"

    def _responder_api(self, texto, usuario_agregado=False):
        if not usuario_agregado:
            self.historial.append({"role": "user", "content": texto})
        self._recortar()
        herramientas_ejecutadas = False
        fallback_usado = False
        error_proveedor_principal = None
        for _ in range(6):
            if tools.detencion_solicitada():
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
            try:
                opciones = ai_providers.crear_opciones_solicitud(
                    self.proveedor,
                    self.modelo,
                    [self._sistema()] + self.historial,
                    tools.esquemas(),
                )
                respuesta = self.cliente.chat.completions.create(**opciones)
            except Exception as error:
                codigo = getattr(error, "status_code", None)
                tipo = type(error).__name__.casefold()
                reintentable = (
                    codigo in {400, 401, 403, 404, 408, 409, 429}
                    or (isinstance(codigo, int) and codigo >= 500)
                    or "connection" in tipo
                    or "timeout" in tipo
                )
                if (
                    reintentable
                    and not herramientas_ejecutadas
                    and not fallback_usado
                ):
                    error_proveedor_principal = ai_providers.error_publico(
                        self.proveedor, error, self.api_key
                    )
                    try:
                        fallback_activo = self._activar_fallback()
                    except Exception as error_fallback:
                        detalle_fallback = (
                            "No se pudo activar el proveedor alternativo; revisá su API key "
                            "y configuración."
                            if isinstance(error_fallback, ValueError)
                            else ai_providers.error_publico(
                                self.proveedor_alternativo,
                                error_fallback,
                                self.clave_alternativa,
                            )
                        )
                        raise RuntimeError(
                            f"{error_proveedor_principal} {detalle_fallback}"
                        ) from error
                    if fallback_activo:
                        fallback_usado = True
                        continue
                detalle = self._explicar_error_api(error)
                if fallback_usado and error_proveedor_principal:
                    detalle = (
                        f"{error_proveedor_principal} El proveedor alternativo también falló: "
                        f"{detalle}"
                    )
                raise RuntimeError(detalle) from error
            mensaje = respuesta.choices[0].message
            llamada = mensaje.model_dump(exclude_none=True)
            self.historial.append(llamada)
            if not mensaje.tool_calls:
                if tools.detencion_solicitada():
                    return "Detención recibida. No ejecutaré más acciones de esta solicitud."
                respuesta = (mensaje.content or "").strip() or "Listo."
                if fallback_usado:
                    nombre = ai_providers.PROVEEDORES[self.proveedor].nombre
                    return f"El proveedor principal no respondió; usé {nombre}. {respuesta}"
                return respuesta
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
                    "tool_name": nombre,
                    "content": resultado,
                })
                herramientas_ejecutadas = True
            if cancelada:
                return "Detención recibida. No ejecutaré más acciones de esta solicitud."
        return "Me trabé con ese pedido, ¿podés reformularlo?"

    def _explicar_error_api(self, error):
        return ai_providers.error_publico(self.proveedor, error, self.api_key)

    def _activar_fallback(self):
        proveedor = self.proveedor_alternativo
        if not proveedor or proveedor == self.proveedor:
            return False
        self.configurar(
            proveedor,
            self.clave_alternativa,
            proveedor_alternativo=None,
        )
        return True


# ---------------------------------------------------------------------------
# Motor ensemble: consulta múltiples IAs en paralelo y sintetiza
# ---------------------------------------------------------------------------

def _consultar_proveedor_simple(proveedor, api_key, mensajes, timeout=30):
    """Llama a un proveedor y devuelve (proveedor, texto_respuesta | None, error | None)."""
    try:
        cliente = ai_providers.crear_cliente(proveedor, api_key, timeout=timeout)
        modelo = ai_providers.obtener_modelo(proveedor)
        opciones = ai_providers.crear_opciones_solicitud(proveedor, modelo, mensajes)
        respuesta = cliente.chat.completions.create(**opciones)
        texto = (respuesta.choices[0].message.content or "").strip()
        return proveedor, texto, None
    except Exception as error:
        return proveedor, None, error


class CerebroEnsemble:
    """Cerebro multi-IA: coordina Gemini, OpenAI, Groq, Cloudflare y Cursor en conjunto.

    Estrategias disponibles (config.ESTRATEGIA_ENSEMBLE):
    - "mejor":    Consulta todos en paralelo y sintetiza la mejor respuesta
                  usando el proveedor principal. Máxima calidad.
    - "rapido":   Devuelve la primera respuesta que llegue. Máxima velocidad.
    - "verificar": El proveedor principal responde; los demás verifican y
                  el principal decide si ajustar. Balance calidad/costo.
    - "votar":    Cuando hay 3+ proveedores, elige la respuesta que más se
                  repite (similitud). Con 2 proveedores actúa como "mejor".

    Las herramientas (tool_calls) siempre las ejecuta solo el proveedor principal
    para evitar efectos duplicados.
    """

    def __init__(self, proveedores_claves: dict[str, str], proveedor_principal: str):
        """
        Args:
            proveedores_claves: {codigo_proveedor: api_key}
            proveedor_principal: código del proveedor que actúa como orquestador
        """
        if not proveedores_claves:
            raise ValueError(
                "Necesitás al menos una API key configurada para iniciar Jarvis. "
                "Ingresala desde Configuración → IA."
            )
        self.proveedores_claves = proveedores_claves
        self.proveedor_principal = proveedor_principal
        self.historial: list[dict] = []
        # Instancia del cerebro simple para manejar herramientas y el flujo principal
        api_key_principal = proveedores_claves.get(proveedor_principal, "")
        self._cerebro_principal = Cerebro(
            provider=proveedor_principal,
            api_key=api_key_principal,
        )
        # Clientes extra para los proveedores secundarios (sin herramientas)
        self._clientes_extra: dict[str, object] = {}
        for codigo, clave in proveedores_claves.items():
            if codigo == proveedor_principal:
                continue
            try:
                self._clientes_extra[codigo] = ai_providers.crear_cliente(codigo, clave)
            except Exception as err:
                print(f"  [ensemble] No se pudo iniciar {codigo}: {err}")

    @property
    def _estrategia(self):
        return getattr(config, "ESTRATEGIA_ENSEMBLE", "mejor").lower()

    def verificar(self):
        return self._cerebro_principal.verificar()

    def configurar(
        self,
        provider,
        api_key=None,
        proveedor_alternativo=None,
        clave_alternativa=None,
    ):
        """Redirige al cerebro principal para mantener compatibilidad con el launcher."""
        self._cerebro_principal.configurar(
            provider, api_key, proveedor_alternativo, clave_alternativa
        )
        self.proveedor_principal = self._cerebro_principal.proveedor

    def responder(self, texto):
        if tools.detencion_solicitada():
            return "Detención solicitada. No voy a iniciar nuevas acciones."

        proveedores_secundarios = list(self._clientes_extra.keys())

        # Si no hay IAs secundarias disponibles, delegar al cerebro principal
        if not proveedores_secundarios:
            return self._cerebro_principal.responder(texto)

        estrategia = self._estrategia

        if estrategia == "rapido":
            return self._responder_rapido(texto)
        elif estrategia == "verificar":
            return self._responder_verificar(texto)
        elif estrategia == "votar":
            return self._responder_votar(texto)
        else:  # "mejor" (default)
            return self._responder_mejor(texto)

    def _mensajes_actuales(self, texto, incluir_nuevo=True):
        """Construye la lista de mensajes (sistema + historial + nueva pregunta)."""
        # Usar historial del cerebro principal (ya tiene el historial sincronizado)
        historial = list(self._cerebro_principal.historial)
        # Filtrar solo mensajes de texto para los proveedores secundarios
        historial_limpio = [
            m for m in historial
            if m.get("role") in ("user", "assistant")
            and not m.get("tool_calls")
            and m.get("content", "").strip()
        ]
        mensajes = [_sistema_base()] + historial_limpio
        if incluir_nuevo:
            mensajes.append({"role": "user", "content": texto})
        return mensajes

    def _responder_mejor(self, texto):
        """Consulta todos los proveedores en paralelo y sintetiza la mejor respuesta."""
        print(f"  [ensemble:mejor] Consultando {1 + len(self._clientes_extra)} IAs en paralelo...")

        mensajes = self._mensajes_actuales(texto)
        tareas = {}

        # El proveedor principal puede usar herramientas (ruta completa)
        # Los secundarios responden en modo texto simple
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(self._clientes_extra) + 1
        ) as executor:
            # Secundarios: solo texto, sin herramientas
            for codigo, clave in self.proveedores_claves.items():
                if codigo == self.proveedor_principal:
                    continue
                tareas[executor.submit(
                    _consultar_proveedor_simple, codigo, clave, mensajes, 25
                )] = codigo

            # Esperar resultados secundarios
            respuestas_extra = {}
            for futuro in concurrent.futures.as_completed(tareas, timeout=30):
                proveedor, texto_resp, error = futuro.result()
                if texto_resp:
                    respuestas_extra[proveedor] = texto_resp
                    print(f"  [ensemble:mejor] {proveedor} respondió ({len(texto_resp)} chars)")
                elif error:
                    print(f"  [ensemble:mejor] {proveedor} falló: {error}")

        # El principal siempre responde (con soporte de herramientas)
        respuesta_principal = self._cerebro_principal.responder(texto)

        if not respuestas_extra:
            # Solo el principal respondió, sin síntesis necesaria
            return respuesta_principal

        # Sintetizar usando el principal como árbitro
        return self._sintetizar(texto, respuesta_principal, respuestas_extra)

    def _responder_rapido(self, texto):
        """Devuelve la primera respuesta que llegue de cualquier proveedor."""
        print(f"  [ensemble:rapido] Carrera entre {1 + len(self._clientes_extra)} IAs...")

        mensajes = self._mensajes_actuales(texto)

        # Lanzar todos los secundarios en paralelo
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(self._clientes_extra)
        ) as executor:
            futuros = {
                executor.submit(_consultar_proveedor_simple, codigo, clave, mensajes, 20): codigo
                for codigo, clave in self.proveedores_claves.items()
                if codigo != self.proveedor_principal
            }

            # Tomar la primera respuesta válida
            for futuro in concurrent.futures.as_completed(futuros, timeout=25):
                proveedor, texto_resp, error = futuro.result()
                if texto_resp:
                    nombre = ai_providers.PROVEEDORES[proveedor].nombre
                    print(f"  [ensemble:rapido] Ganó {proveedor}")
                    # Agregar al historial del cerebro principal para mantener coherencia
                    self._cerebro_principal.historial.append(
                        {"role": "user", "content": texto}
                    )
                    self._cerebro_principal.historial.append(
                        {"role": "assistant", "content": texto_resp}
                    )
                    self._cerebro_principal._recortar()
                    return texto_resp

        # Si todos fallaron, usar el principal
        return self._cerebro_principal.responder(texto)

    def _responder_verificar(self, texto):
        """Principal responde, secundarios verifican, principal decide si ajustar."""
        print(f"  [ensemble:verificar] Principal responde, {len(self._clientes_extra)} verifican...")

        # El principal responde primero (con herramientas si las necesita)
        respuesta_principal = self._cerebro_principal.responder(texto)

        if tools.detencion_solicitada() or not self._clientes_extra:
            return respuesta_principal

        # Los secundarios evalúan la respuesta
        mensajes_verificacion = [
            _sistema_base(),
            {"role": "user", "content": texto},
            {"role": "assistant", "content": respuesta_principal},
            {
                "role": "user",
                "content": (
                    "¿La respuesta anterior es correcta, completa y precisa? "
                    "Si tiene errores importantes, corrígela brevemente. "
                    "Si está bien, responde solo: OK"
                ),
            },
        ]

        correcciones = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(self._clientes_extra)
        ) as executor:
            futuros = {
                executor.submit(
                    _consultar_proveedor_simple, codigo, clave, mensajes_verificacion, 20
                ): codigo
                for codigo, clave in self.proveedores_claves.items()
                if codigo != self.proveedor_principal
            }
            for futuro in concurrent.futures.as_completed(futuros, timeout=25):
                proveedor, texto_resp, error = futuro.result()
                if texto_resp and texto_resp.strip().upper() != "OK":
                    correcciones.append((proveedor, texto_resp))
                    print(f"  [ensemble:verificar] {proveedor} sugirió corrección")

        if not correcciones:
            return respuesta_principal

        # Si hay correcciones, el principal las integra
        detalle_correcciones = "\n".join(
            f"- {ai_providers.PROVEEDORES.get(p, type('', (), {'nombre': p})()).nombre}: {r}"
            for p, r in correcciones
        )
        mensajes_ajuste = self._mensajes_actuales(
            f"Revisá tu respuesta anterior considerando estas observaciones:\n{detalle_correcciones}\n"
            f"Mejorala si corresponde o confirmá que es correcta. No expliques el proceso.",
            incluir_nuevo=True,
        )

        try:
            cliente_p = self._cerebro_principal.cliente
            modelo_p = self._cerebro_principal.modelo
            proveedor_p = self._cerebro_principal.proveedor
            opciones = ai_providers.crear_opciones_solicitud(proveedor_p, modelo_p, mensajes_ajuste)
            resp_ajustada = cliente_p.chat.completions.create(**opciones)
            ajustada = (resp_ajustada.choices[0].message.content or "").strip()
            if ajustada:
                # Actualizar historial
                self._cerebro_principal.historial.append(
                    {"role": "assistant", "content": ajustada}
                )
                self._cerebro_principal._recortar()
                return ajustada
        except Exception as err:
            print(f"  [ensemble:verificar] Error al ajustar: {err}")

        return respuesta_principal

    def _responder_votar(self, texto):
        """Con 3+ IAs elige por similitud; con 2 actúa como 'mejor'."""
        total = 1 + len(self._clientes_extra)
        if total < 3:
            return self._responder_mejor(texto)

        print(f"  [ensemble:votar] Votación entre {total} IAs...")

        mensajes = self._mensajes_actuales(texto)
        todas_respuestas: list[tuple[str, str]] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=total) as executor:
            futuros = {
                executor.submit(_consultar_proveedor_simple, codigo, clave, mensajes, 25): codigo
                for codigo, clave in self.proveedores_claves.items()
                if codigo != self.proveedor_principal
            }
            # Lanzar también el principal en modo texto simple para la votación
            futuro_principal = executor.submit(
                _consultar_proveedor_simple,
                self.proveedor_principal,
                self.proveedores_claves[self.proveedor_principal],
                mensajes,
                25,
            )
            futuros[futuro_principal] = self.proveedor_principal

            for futuro in concurrent.futures.as_completed(futuros, timeout=30):
                proveedor, texto_resp, error = futuro.result()
                if texto_resp:
                    todas_respuestas.append((proveedor, texto_resp))

        if not todas_respuestas:
            return self._cerebro_principal.responder(texto)

        # Elegir la respuesta con mayor similaridad con las demás (voto por promedio)
        ganadora = self._elegir_por_voto(todas_respuestas)
        proveedor_ganador, texto_ganador = ganadora

        # Sincronizar historial del cerebro principal
        self._cerebro_principal.historial.append({"role": "user", "content": texto})
        self._cerebro_principal.historial.append({"role": "assistant", "content": texto_ganador})
        self._cerebro_principal._recortar()

        return texto_ganador

    @staticmethod
    def _elegir_por_voto(respuestas: list[tuple[str, str]]) -> tuple[str, str]:
        """Elige la respuesta con mayor similitud promedio con las demás (Jaccard sobre tokens)."""
        if len(respuestas) == 1:
            return respuestas[0]

        def jaccard(a: str, b: str) -> float:
            ta = set(a.lower().split())
            tb = set(b.lower().split())
            inter = len(ta & tb)
            union = len(ta | tb)
            return inter / union if union else 0.0

        mejor_score = -1.0
        mejor = respuestas[0]
        for i, (p, r) in enumerate(respuestas):
            score = sum(
                jaccard(r, r2)
                for j, (_, r2) in enumerate(respuestas)
                if j != i
            ) / max(len(respuestas) - 1, 1)
            if score > mejor_score:
                mejor_score = score
                mejor = (p, r)
        return mejor

    def _sintetizar(
        self,
        texto_original: str,
        respuesta_principal: str,
        respuestas_extra: dict[str, str],
    ) -> str:
        """Usa el proveedor principal para sintetizar las respuestas de todas las IAs."""
        todas = {self.proveedor_principal: respuesta_principal, **respuestas_extra}
        partes = []
        for codigo, resp in todas.items():
            nombre = ai_providers.PROVEEDORES.get(
                codigo, type("P", (), {"nombre": codigo})()
            ).nombre
            partes.append(f"[{nombre}]: {resp}")

        bloque = "\n\n".join(partes)
        prompt_sintesis = PROMPT_SINTESIS.format(n=len(todas), respuestas=bloque)

        mensajes_sintesis = [
            {"role": "system", "content": prompt_sintesis},
            {"role": "user", "content": texto_original},
        ]

        try:
            cliente_p = self._cerebro_principal.cliente
            modelo_p = self._cerebro_principal.modelo
            proveedor_p = self._cerebro_principal.proveedor
            opciones = ai_providers.crear_opciones_solicitud(
                proveedor_p, modelo_p, mensajes_sintesis
            )
            resp = cliente_p.chat.completions.create(**opciones)
            sintetizada = (resp.choices[0].message.content or "").strip()
            if sintetizada:
                print(f"  [ensemble:mejor] Síntesis completada ({len(sintetizada)} chars)")
                return sintetizada
        except Exception as err:
            print(f"  [ensemble:mejor] Error en síntesis: {err}")

        # Si falla la síntesis, devolver la del principal
        return respuesta_principal


def crear_cerebro(obtener_clave_fn) -> "Cerebro | CerebroEnsemble":
    """Fábrica: devuelve un CerebroEnsemble si hay 2+ keys, o un Cerebro simple.

    Args:
        obtener_clave_fn: función(proveedor) -> str que lee la API key.

    Returns:
        Cerebro o CerebroEnsemble listo para usar.

    Raises:
        ValueError: si no hay ninguna API key configurada.
    """
    activo, proveedores = ai_providers.ensemble_activo(obtener_clave_fn)

    if not proveedores:
        # Verificar si hay al menos uno (para el error claro)
        principal = config.PROVEEDOR_IA
        if principal == "ollama":
            return Cerebro(provider="ollama")
        raise ValueError(
            "Necesitás al menos una API key configurada para usar Jarvis. "
            "Ingresala desde el HUD → Ajustes → IA."
        )

    principal = config.PROVEEDOR_IA if config.PROVEEDOR_IA in proveedores else proveedores[0]

    if not activo or len(proveedores) < 2:
        # Solo una key → cerebro simple
        clave = obtener_clave_fn(principal)
        return Cerebro(provider=principal, api_key=clave)

    # Ensemble
    claves = {p: obtener_clave_fn(p) for p in proveedores}
    print(
        f"  [ensemble] Modo activo con {len(claves)} IAs: "
        + ", ".join(ai_providers.PROVEEDORES[p].nombre for p in claves if p in ai_providers.PROVEEDORES)
        + f" (estrategia: {getattr(config, 'ESTRATEGIA_ENSEMBLE', 'mejor')})"
    )
    return CerebroEnsemble(claves, principal)
