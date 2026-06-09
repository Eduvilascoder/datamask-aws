"""
Middleware de autenticacion para el Lambda API Handler.

El API Gateway usa un authorizer de Cognito User Pools (COGNITO_USER_POOLS):
cada request protegido llega con un JWT (id token) en el header Authorization.
API Gateway valida la firma y expiracion del token ANTES de invocar al Lambda,
e inyecta los claims del usuario en `requestContext.authorizer.claims`.

Cognito actua como Service Provider SAML federado con IAM Identity Center (que
puede a su vez federar con Active Directory u otro IdP corporativo). El usuario
real se deriva del claim `email` (p.ej. eduvilas@org.com -> eduvilas).

Retorna HTTP 403 generico para solicitudes sin identidad
(Req 11.6: nunca revelar existencia de recursos).
"""

import hashlib
import logging
from typing import Any

from responses import forbidden

logger = logging.getLogger(__name__)


# Rutas publicas que no requieren autenticacion. El proxy SSO heredado se
# conserva por compatibilidad pero ya no se usa con Cognito.
SSO_PUBLIC_PREFIX = "/auth/sso/"


def is_public_route(method: str, path: str) -> bool:
    """
    Determina si una ruta es publica (no requiere autenticacion).

    Son publicas:
    - Los preflight CORS (OPTIONS).
    - El proxy SSO heredado (/auth/sso/*), conservado por compatibilidad.
    """
    if method.upper() == "OPTIONS":
        return True
    return path.startswith(SSO_PUBLIC_PREFIX)


def _username_from_email(email: str) -> str:
    """Deriva un username legible y estable a partir del email.

    Normaliza el subaddressing (parte tras '+') para que variantes del mismo
    buzón deriven el mismo usuario: p.ej. `eduvilas+quicksuite@dom.com` y
    `eduvilas@otro.com` derivan ambos en `eduvilas`.
    """
    local_part = email.split("@", 1)[0].strip().lower()
    # Quitar subaddressing "+etiqueta" (Gmail/AWS style).
    local_part = local_part.split("+", 1)[0]
    return local_part


def _derive_user_id(claims: dict[str, Any]) -> str:
    """
    Deriva un userId estable y legible a partir de los claims del JWT.

    Prioridad:
      1. parte local del email (p.ej. eduvilas@org.com -> eduvilas)
      2. cognito:username
      3. sub (identificador opaco de Cognito)
    """
    email = claims.get("email", "")
    if email:
        username = _username_from_email(email)
        if username:
            return username

    cognito_username = claims.get("cognito:username", "")
    if cognito_username:
        # Los usuarios federados llegan como "idp_xxx"; quedarse con el sufijo.
        return cognito_username.rsplit("_", 1)[-1] if "_" in cognito_username else cognito_username

    return claims.get("sub", "")


def authenticate_request(
    event: dict[str, Any], config: Any = None
) -> dict[str, Any] | None:
    """
    Verifica la autenticacion de una solicitud.

    Como API Gateway (Cognito User Pools authorizer) ya valido el JWT, aqui
    solo se extraen los claims desde `requestContext.authorizer.claims` y se
    deriva el usuario para el resto del handler.

    Args:
        event: Evento de API Gateway.
        config: Configuracion de la aplicacion (no usado actualmente).

    Returns:
        None si la autenticacion es valida,
        o respuesta 403 si no hay identidad.
    """
    method = event.get("httpMethod", "")
    path = event.get("path", "")

    if is_public_route(method, path):
        return None

    request_context = event.get("requestContext", {}) or {}
    authorizer = request_context.get("authorizer", {}) or {}
    # API Gateway anida los claims del JWT en authorizer.claims.
    claims = authorizer.get("claims", {}) or {}

    user_id = _derive_user_id(claims)
    if not user_id:
        logger.info("Request sin identidad valida a ruta protegida: %s %s", method, path)
        return forbidden()

    email = claims.get("email", "")
    sub = claims.get("sub", "")

    # Adjuntar datos de usuario al evento para el resto del handler.
    request_context["authorizer"] = {
        **authorizer,
        "userId": user_id,
        "sessionId": f"cognito-{hashlib.sha256(sub.encode()).hexdigest()[:8]}" if sub else "",
        "email": email,
        "sub": sub,
    }
    event["requestContext"] = request_context

    return None


def get_user_id(event: dict[str, Any]) -> str:
    """Extrae el userId del evento ya autenticado."""
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    return authorizer.get("userId", "")


def get_session_id(event: dict[str, Any]) -> str:
    """Extrae el sessionId del evento ya autenticado."""
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    return authorizer.get("sessionId", "")
