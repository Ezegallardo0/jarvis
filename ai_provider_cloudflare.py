"""Adaptador de Cloudflare Workers AI a la interfaz de chat de OpenAI."""
import json
import uuid

import config


class _ObjetoSimple:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self, exclude_none=True):
        def convertir(valor):
            if isinstance(valor, _ObjetoSimple):
                return valor.model_dump(exclude_none=exclude_none)
            if isinstance(valor, list):
                return [convertir(v) for v in valor]
            if isinstance(valor, dict):
                return {k: convertir(v) for k, v in valor.items() if not (exclude_none and v is None)}
            return valor
        datos = convertir(self.__dict__)
        if exclude_none:
            return {k: v for k, v in datos.items() if v is not None}
        return datos


class _CloudflareCompletions:
    def __init__(self, account_id, api_token, timeout=30):
        self.account_id = account_id
        self.api_token = api_token
        self.timeout = timeout

    def create(self, **opciones):
        import httpx

        modelo = opciones.get("model") or config.MODELO_CLOUDFLARE
        mensajes = self._convertir_mensajes(opciones.get("messages", []))
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{modelo}"
        headers = {
            "Authorization": "Bearer " + self.api_token,
            "Content-Type": "application/json",
        }
        payload = {"messages": mensajes}
        for clave in ("max_tokens", "temperature", "stream"):
            if clave in opciones and opciones[clave] is not None:
                payload[clave] = opciones[clave]
        if opciones.get("tools") is not None:
            payload["tools"] = self._convertir_herramientas(opciones["tools"])

        respuesta = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
        try:
            datos = respuesta.json()
        except Exception:
            datos = {"errors": [{"message": respuesta.text}]}
        if respuesta.status_code >= 400 or not datos.get("success", True):
            mensaje = None
            errores = datos.get("errors") or []
            if errores and isinstance(errores[0], dict):
                mensaje = errores[0].get("message")
            mensaje = mensaje or datos.get("result") or respuesta.text or f"HTTP {respuesta.status_code}"
            error = RuntimeError(str(mensaje))
            error.status_code = respuesta.status_code
            error.body = datos
            raise error

        resultado = datos.get("result", {}) or {}
        contenido = (
            resultado.get("response")
            or resultado.get("result")
            or resultado.get("text")
            or ""
        )
        if isinstance(contenido, dict):
            contenido = json.dumps(contenido, ensure_ascii=False)
        llamadas = []
        for llamada in resultado.get("tool_calls") or []:
            nombre = llamada.get("name")
            if not nombre:
                continue
            argumentos = llamada.get("arguments") or {}
            if isinstance(argumentos, str):
                try:
                    argumentos = json.loads(argumentos)
                except json.JSONDecodeError:
                    pass
            if not isinstance(argumentos, str):
                argumentos = json.dumps(argumentos, ensure_ascii=False)
            funcion = _ObjetoSimple(name=nombre, arguments=argumentos)
            llamadas.append(
                _ObjetoSimple(
                    id=f"call_{uuid.uuid4().hex}",
                    type="function",
                    function=funcion,
                )
            )
        mensaje = _ObjetoSimple(
            role="assistant",
            content=str(contenido).strip(),
            tool_calls=llamadas or None,
        )
        eleccion = _ObjetoSimple(
            message=mensaje,
            finish_reason="tool_calls" if llamadas else "stop",
        )
        return _ObjetoSimple(choices=[eleccion])

    @staticmethod
    def _convertir_mensajes(mensajes):
        convertidos = []
        for mensaje in mensajes:
            llamadas = mensaje.get("tool_calls") or []
            if mensaje.get("role") == "assistant" and llamadas:
                for llamada in llamadas:
                    funcion = llamada.get("function", {})
                    argumentos = funcion.get("arguments") or {}
                    if isinstance(argumentos, str):
                        try:
                            argumentos = json.loads(argumentos)
                        except json.JSONDecodeError:
                            pass
                    convertidos.append(
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "name": funcion.get("name", ""),
                                    "arguments": argumentos,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
            elif mensaje.get("role") == "tool":
                convertidos.append(
                    {"role": "tool", "content": mensaje.get("content", "")}
                )
            else:
                convertidos.append(
                    {
                        "role": mensaje.get("role", "user"),
                        "content": mensaje.get("content", ""),
                    }
                )
        return convertidos

    @staticmethod
    def _convertir_herramientas(herramientas):
        convertidas = []
        for herramienta in herramientas:
            funcion = herramienta.get("function")
            if herramienta.get("type") == "function" and isinstance(funcion, dict):
                convertidas.append(funcion)
            else:
                convertidas.append(herramienta)
        return convertidas


class _CloudflareChat:
    def __init__(self, account_id, api_token, timeout=30):
        self.completions = _CloudflareCompletions(account_id, api_token, timeout=timeout)


class CloudflareClient:
    def __init__(self, account_id, api_token, timeout=30):
        self.chat = _CloudflareChat(account_id, api_token, timeout=timeout)
