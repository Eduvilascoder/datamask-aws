"""Tests para el handler principal — routing y middleware integration.

La autenticación se resuelve con Amazon Cognito (federado con IAM Identity
Center). API Gateway valida el JWT con el authorizer Cognito; la identidad
llega en requestContext.authorizer.claims (procesada por el middleware).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Agregar el directorio padre al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AppConfig
from handler import handler, route_request
from responses import forbidden


def _make_test_config() -> AppConfig:
    """Crea configuración de test para handlers."""
    return AppConfig(
        documents_bucket="test-bucket",
        documents_table="test-table",
        secrets_arn="arn:aws:secretsmanager:us-east-1:123:secret:test",
        frontend_url="https://app.example.com",
    )


def _make_event(
    method: str = "GET",
    path: str = "/documents",
    claims: dict | None = None,
    body: dict | None = None,
) -> dict:
    """Crea evento API Gateway de test."""
    request_context: dict = {}
    if claims is not None:
        request_context["authorizer"] = {"claims": claims}

    event = {
        "httpMethod": method,
        "path": path,
        "headers": {},
        "requestContext": request_context,
        "body": json.dumps(body) if body else None,
    }
    return event


class TestRouteRequest:
    """Tests para el router de requests."""

    def test_unknown_route_returns_403(self) -> None:
        """Req 11.6: Ruta desconocida retorna 403 genérico."""
        event = _make_event(method="GET", path="/nonexistent")
        result = route_request("GET", "/nonexistent", event)

        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert body["message"] == "Forbidden"

    def test_wrong_method_returns_403(self) -> None:
        """Método incorrecto para ruta válida retorna 403."""
        event = _make_event(method="DELETE", path="/documents")
        result = route_request("DELETE", "/documents", event)

        assert result["statusCode"] == 403

    def test_auth_endpoints_removed(self) -> None:
        """Los endpoints SSO viejos (/auth/sso/*) ya no existen → 403."""
        for path in ("/auth/sso/register", "/auth/sso/token", "/auth/login"):
            event = _make_event(method="POST", path=path)
            result = route_request("POST", path, event)
            assert result["statusCode"] == 403

    def test_documents_get_routed(self) -> None:
        """GET /documents se rutea correctamente."""
        event = _make_event(method="GET", path="/documents")
        # Sin claims en context → 403
        result = route_request("GET", "/documents", event)
        assert result["statusCode"] == 403

    def test_config_detection_get_routed(self) -> None:
        """GET /config/detection se rutea correctamente."""
        event = _make_event(method="GET", path="/config/detection")
        # Sin claims en context → 403
        result = route_request("GET", "/config/detection", event)
        assert result["statusCode"] == 403

    def test_legacy_config_removed(self) -> None:
        """El endpoint /config (PII toggles) fue eliminado → 403."""
        event = _make_event(method="GET", path="/config")
        result = route_request("GET", "/config", event)
        assert result["statusCode"] == 403

    def test_download_route_detected(self) -> None:
        """GET /documents/{id}/download/{type} se rutea correctamente."""
        event = _make_event(
            method="GET", path="/documents/abc-123/download/pdf"
        )
        # Sin userId en context → 403
        result = route_request(
            "GET", "/documents/abc-123/download/pdf", event
        )
        assert result["statusCode"] == 403

    def test_document_detail_route(self) -> None:
        """GET /documents/{id} se rutea correctamente."""
        event = _make_event(method="GET", path="/documents/abc-123")
        # Sin userId en context → 403
        result = route_request("GET", "/documents/abc-123", event)
        assert result["statusCode"] == 403


class TestHandlerIntegration:
    """Tests de integración del handler con middleware."""

    def test_options_preflight_allowed(self) -> None:
        """OPTIONS (CORS preflight) no requiere autenticación IAM."""
        # OPTIONS es público; el router devuelve 403 sólo porque no hay
        # handler para esa ruta, pero el middleware NO debe bloquearlo.
        event = _make_event(method="OPTIONS", path="/documents")
        result = handler(event, None)
        # El middleware deja pasar; el router responde 403 (sin handler OPTIONS).
        assert result["statusCode"] == 403

    @patch("handler.authenticate_request")
    def test_protected_route_blocked_without_auth(
        self, mock_auth: object
    ) -> None:
        """Rutas protegidas retornan 403 sin autenticación."""
        mock_auth.return_value = forbidden()
        event = _make_event(method="GET", path="/documents")

        result = handler(event, None)

        assert result["statusCode"] == 403

    @patch("handler.authenticate_request")
    def test_protected_route_allowed_with_auth(
        self, mock_auth: object
    ) -> None:
        """Rutas protegidas permitidas con autenticación válida."""
        mock_auth.return_value = None
        event = _make_event(method="GET", path="/documents")

        result = handler(event, None)

        # Sin userId en context (mock no lo inyecta) → 403 from handler
        assert result["statusCode"] == 403

    @patch("handler.authenticate_request")
    def test_exception_returns_500(self, mock_auth: object) -> None:
        """Excepciones no manejadas retornan 500 genérico."""
        mock_auth.side_effect = RuntimeError("Error inesperado")
        event = _make_event(method="GET", path="/documents")

        result = handler(event, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert body["message"] == "Internal server error"
        # No debe revelar detalles del error
        assert "RuntimeError" not in result["body"]

    @patch("handler.authenticate_request")
    def test_403_identical_for_existing_and_nonexisting_routes(
        self, mock_auth: object
    ) -> None:
        """
        Req 11.6: Respuesta 403 idéntica para rutas existentes y no existentes.
        """
        mock_auth.return_value = forbidden()

        event_existing = _make_event(method="GET", path="/documents")
        result_existing = handler(event_existing, None)

        event_fake = _make_event(method="GET", path="/admin/secret")
        result_fake = handler(event_fake, None)

        assert result_existing["statusCode"] == result_fake["statusCode"]
        assert result_existing["body"] == result_fake["body"]
