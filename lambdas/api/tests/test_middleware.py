"""Tests para el módulo middleware.py (autorización Cognito User Pools)."""

import json
import sys
from pathlib import Path

# Agregar el directorio padre al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AppConfig
from middleware import authenticate_request, get_user_id, is_public_route


def _make_config() -> AppConfig:
    """Crea configuración de test."""
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
) -> dict:
    """Crea evento API Gateway de test con claims del authorizer Cognito."""
    request_context: dict = {}
    if claims is not None:
        request_context["authorizer"] = {"claims": claims}
    return {
        "httpMethod": method,
        "path": path,
        "headers": {},
        "requestContext": request_context,
    }


class TestIsPublicRoute:
    """Tests para is_public_route (solo OPTIONS es público con Cognito)."""

    def test_options_is_public(self) -> None:
        """OPTIONS (CORS preflight) es público."""
        assert is_public_route("OPTIONS", "/documents") is True

    def test_documents_is_protected(self) -> None:
        """GET /documents requiere autenticación."""
        assert is_public_route("GET", "/documents") is False

    def test_config_detection_is_protected(self) -> None:
        """GET /config/detection requiere autenticación."""
        assert is_public_route("GET", "/config/detection") is False

    def test_upload_is_protected(self) -> None:
        """POST /upload/presign requiere autenticación."""
        assert is_public_route("POST", "/upload/presign") is False


class TestAuthenticateRequest:
    """Tests para authenticate_request con authorizer Cognito."""

    def test_options_skips_auth(self) -> None:
        """Preflight CORS (OPTIONS) no pasa por autenticación."""
        event = _make_event(method="OPTIONS", path="/documents")
        result = authenticate_request(event, _make_config())
        assert result is None

    def test_no_claims_returns_403(self) -> None:
        """Sin claims del JWT en el contexto retorna 403 genérico."""
        event = _make_event(method="GET", path="/documents")
        result = authenticate_request(event, _make_config())
        assert result is not None
        assert result["statusCode"] == 403
        assert json.loads(result["body"])["message"] == "Forbidden"

    def test_empty_claims_returns_403(self) -> None:
        """Claims vacíos (sin email/sub/username) retorna 403."""
        event = _make_event(method="GET", path="/documents", claims={})
        result = authenticate_request(event, _make_config())
        assert result is not None
        assert result["statusCode"] == 403

    def test_valid_email_claim_derives_username(self) -> None:
        """El userId se deriva de la parte local del email."""
        event = _make_event(
            method="GET",
            path="/documents",
            claims={"email": "eduvilas@org.com", "sub": "abc-123"},
        )
        result = authenticate_request(event, _make_config())
        assert result is None
        authorizer = event["requestContext"]["authorizer"]
        assert authorizer["userId"] == "eduvilas"
        assert authorizer["email"] == "eduvilas@org.com"

    def test_username_stable_for_same_email(self) -> None:
        """El userId derivado es estable para el mismo email."""
        event1 = _make_event(claims={"email": "alice@org.com", "sub": "s1"})
        event2 = _make_event(claims={"email": "alice@org.com", "sub": "s1"})
        authenticate_request(event1, _make_config())
        authenticate_request(event2, _make_config())
        assert (
            event1["requestContext"]["authorizer"]["userId"]
            == event2["requestContext"]["authorizer"]["userId"]
        )

    def test_email_subaddressing_normalized(self) -> None:
        """El subaddressing (+etiqueta) se normaliza al mismo userId."""
        event_fed = _make_event(
            claims={"email": "eduvilas+quicksuite@amazon.com.ar", "sub": "s1"}
        )
        event_native = _make_event(
            claims={"email": "eduvilas@amazon.com", "sub": "s2"}
        )
        authenticate_request(event_fed, _make_config())
        authenticate_request(event_native, _make_config())
        assert event_fed["requestContext"]["authorizer"]["userId"] == "eduvilas"
        assert (
            event_native["requestContext"]["authorizer"]["userId"] == "eduvilas"
        )

    def test_falls_back_to_cognito_username(self) -> None:
        """Sin email, usa cognito:username (quitando prefijo del IdP)."""
        event = _make_event(
            method="GET",
            path="/documents",
            claims={"cognito:username": "IAMIdentityCenter_bob", "sub": "s2"},
        )
        result = authenticate_request(event, _make_config())
        assert result is None
        assert event["requestContext"]["authorizer"]["userId"] == "bob"

    def test_403_never_reveals_resource_existence(self) -> None:
        """Req 11.6: El 403 es idéntico para rutas que existen y no existen."""
        config = _make_config()
        result_existing = authenticate_request(
            _make_event(method="GET", path="/documents"), config
        )
        result_nonexisting = authenticate_request(
            _make_event(method="GET", path="/secret/admin"), config
        )
        assert result_existing["statusCode"] == result_nonexisting["statusCode"]
        assert json.loads(result_existing["body"]) == json.loads(
            result_nonexisting["body"]
        )


class TestGetUserId:
    """Tests para get_user_id."""

    def test_extracts_user_id(self) -> None:
        """Extrae userId del contexto autenticado."""
        event = {
            "requestContext": {
                "authorizer": {"userId": "user-456", "sessionId": "sess-789"}
            }
        }
        assert get_user_id(event) == "user-456"

    def test_missing_context_returns_empty(self) -> None:
        """Sin contexto de autorización retorna string vacío."""
        event = {"requestContext": {}}
        assert get_user_id(event) == ""
