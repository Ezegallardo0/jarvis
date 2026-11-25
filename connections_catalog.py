"""Estado verificable de las integraciones visibles en el HUD."""


def catalogo_conexiones(
    spotify_client_id="",
    github_token_guardado=False,
    ubicacion="",
    google_estado=None,
):
    google_estado = google_estado or {}
    google_conectado = bool(google_estado.get("connected"))
    google_cliente = bool(google_estado.get("client_configured"))
    google_email = google_estado.get("email", "")
    return [
        {
            "id": "spotify",
            "nombre": "Spotify",
            "icono": "S",
            "estado": "configurado" if spotify_client_id else "pendiente",
            "estado_texto": (
                "Client ID configurado; la autorización se valida al usar el servicio"
                if spotify_client_id
                else "Requiere un Client ID para iniciar la autorización oficial"
            ),
            "descripcion": "Control de reproducción mediante la integración Spotify PKCE existente.",
            "detalle": "No se marca como conectado solo por guardar el Client ID.",
            "accion": "spotify",
        },
        {
            "id": "github",
            "nombre": "GitHub",
            "icono": "GH",
            "estado": "configurado" if github_token_guardado else "pendiente",
            "estado_texto": (
                "Token disponible; usá la prueba de conexión para verificarlo"
                if github_token_guardado
                else "Pendiente de un token con permisos de solo lectura"
            ),
            "descripcion": "Búsqueda de repositorios, issues, pull requests y código.",
            "detalle": "La integración existente es de solo lectura.",
            "accion": "github",
        },
        {
            "id": "clima",
            "nombre": "Clima",
            "icono": "°",
            "estado": "disponible",
            "estado_texto": (
                "Consulta disponible; ubicación configurada"
                if ubicacion
                else "Consulta disponible; falta definir una ubicación"
            ),
            "descripcion": "Pronóstico real mediante Open-Meteo, sin API key.",
            "detalle": "La ubicación puede ingresarse o detectarse aproximadamente por IP.",
            "accion": "ubicacion",
        },
        {
            "id": "google",
            "nombre": "Google Workspace",
            "icono": "G",
            "estado": "conectado" if google_conectado else ("configurado" if google_cliente else "pendiente"),
            "estado_texto": (
                f"Conectado como {google_email or 'cuenta de Google'}"
                if google_conectado
                else (
                    "Client ID configurado; falta autorizar con OAuth"
                    if google_cliente
                    else "Pendiente de configurar Client ID y autorizar OAuth 2.0"
                )
            ),
            "descripcion": "Integración con Google Calendar, Gmail y Google Drive mediante OAuth 2.0 PKCE.",
            "detalle": "Los tokens se guardan en el Administrador de credenciales de Windows.",
            "accion": "google",
        },
        {
            "id": "whatsapp",
            "nombre": "WhatsApp",
            "icono": "W",
            "estado": "limitado",
            "estado_texto": "La API oficial no permite acceder a cuentas personales",
            "descripcion": "La plataforma oficial está orientada a cuentas de WhatsApp Business.",
            "detalle": "No se usará automatización de WhatsApp Web ni clientes no oficiales.",
            "accion": "",
        },
        {
            "id": "drive",
            "nombre": "Google Drive",
            "icono": "D",
            "estado": "conectado" if google_conectado else "pendiente",
            "estado_texto": (
                f"Conectado mediante Google Workspace ({google_email or 'OAuth activo'})"
                if google_conectado
                else "Se habilita al conectar Google Workspace con OAuth 2.0"
            ),
            "descripcion": "Listado, búsqueda, lectura, descarga, subida y organización de archivos.",
            "detalle": "Usa el mismo flujo OAuth 2.0 PKCE seguro de Google Workspace.",
            "accion": "google",
        },
        {
            "id": "youtube",
            "nombre": "YouTube",
            "icono": "▶",
            "estado": "disponible",
            "estado_texto": "Apertura y búsqueda web disponibles",
            "descripcion": "JARVIS puede abrir YouTube y buscar videos mediante la búsqueda web.",
            "detalle": "No hay una conexión de cuenta ni acceso a datos privados de YouTube.",
            "accion": "",
        },
        {
            "id": "discord",
            "nombre": "Discord",
            "icono": "D",
            "estado": "pendiente",
            "estado_texto": "Pendiente de implementar",
            "descripcion": "Sin autenticación ni operaciones API disponibles.",
            "detalle": "No se solicita ni almacena ningún token.",
            "accion": "",
        },
        {
            "id": "telegram",
            "nombre": "Telegram",
            "icono": "T",
            "estado": "pendiente",
            "estado_texto": "Pendiente de implementar",
            "descripcion": "Sin autenticación ni operaciones API disponibles.",
            "detalle": "No se solicita ni almacena ningún token.",
            "accion": "",
        },
    ]
