"""
Respuestas HTTP estandarizadas para el Lambda API Handler.

Req 11.6: Todas las respuestas 403 usan mensaje generico "Forbidden"
sin revelar la existencia de recursos.
"""

import json
from decimal import Decimal
from typing import Any


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder que convierte Decimal de DynamoDB a int/float."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)

# Mensaje genérico para todas las respuestas de acceso denegado.
# NUNCA revelar si el recurso existe o no (Req 11.6).
FORBIDDEN_MESSAGE = "Forbidden"

# Headers por defecto para CORS y content-type
DEFAULT_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,Cookie",
    "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
    "Access-Control-Allow-Credentials": "true",
}


def build_response(
    status_code: int,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    cookies: list[str] | None = None,
) -> dict[str, Any]:
    """
    Construye respuesta HTTP en formato Lambda Proxy Integration.

    Args:
        status_code: Código de estado HTTP.
        body: Cuerpo de la respuesta (se serializa a JSON).
        headers: Headers adicionales opcionales.
        cookies: Lista de cookies Set-Cookie opcionales.

    Returns:
        Diccionario con formato Lambda Proxy response.
    """
    response_headers = dict(DEFAULT_HEADERS)
    if headers:
        response_headers.update(headers)

    response: dict[str, Any] = {
        "statusCode": status_code,
        "headers": response_headers,
        "body": json.dumps(body, cls=DecimalEncoder),
    }

    if cookies:
        response["multiValueHeaders"] = {"Set-Cookie": cookies}

    return response


def forbidden() -> dict[str, Any]:
    """
    Respuesta HTTP 403 genérica.

    Req 11.6: Retorna siempre el mismo mensaje sin revelar si el recurso existe.
    Usado tanto para acceso no autorizado como para rutas no encontradas.

    Returns:
        Respuesta 403 con mensaje genérico.
    """
    return build_response(403, {"message": FORBIDDEN_MESSAGE})


def unauthorized_redirect(login_url: str) -> dict[str, Any]:
    """
    Respuesta HTTP 401 que indica al cliente redirigir al login.

    Req 1.1: Redirigir a IAM Identity Center cuando no hay sesión activa.

    Args:
        login_url: URL de login para el redirect.

    Returns:
        Respuesta 401 con URL de login.
    """
    return build_response(
        401,
        {"message": "Authentication required", "login_url": login_url},
    )


def success(body: dict[str, Any]) -> dict[str, Any]:
    """
    Respuesta HTTP 200 exitosa.

    Args:
        body: Cuerpo de la respuesta.

    Returns:
        Respuesta 200 con el body.
    """
    return build_response(200, body)


def created(body: dict[str, Any]) -> dict[str, Any]:
    """
    Respuesta HTTP 201 recurso creado.

    Args:
        body: Cuerpo de la respuesta.

    Returns:
        Respuesta 201 con el body.
    """
    return build_response(201, body)


def bad_request(message: str) -> dict[str, Any]:
    """
    Respuesta HTTP 400 por request inválido.

    Args:
        message: Descripción del error de validación.

    Returns:
        Respuesta 400 con mensaje descriptivo.
    """
    return build_response(400, {"message": message})


def internal_error() -> dict[str, Any]:
    """
    Respuesta HTTP 500 por error interno.

    No revela detalles del error al cliente.

    Returns:
        Respuesta 500 con mensaje genérico.
    """
    return build_response(500, {"message": "Internal server error"})
