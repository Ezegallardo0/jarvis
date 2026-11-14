"""Estado verificable de las integraciones visibles en el HUD."""


def catalogo_conexiones(
    spotify_client_id="",
    github_token_guardado=False,
    ubicacion="",
):
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
            "estado": "pendiente",
            "estado_texto": "Pendiente de implementar OAuth oficial",
            "descripcion": "Calendar, Gmail y Tasks no están conectados todavía.",
            "detalle": "Gmail actualmente solo se abre en el navegador; no se lee ni modifica la bandeja.",
            "accion": "",
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
            "estado": "pendiente",
            "estado_texto": "Pendiente de implementar OAuth",
            "descripcion": "No hay acceso API configurado en esta versión.",
            "detalle": "Se mostrará como conectado solo después de implementar y probar OAuth.",
            "accion": "",
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
