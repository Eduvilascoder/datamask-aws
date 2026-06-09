"""Tests para el módulo responses.py."""

import json
import sys
from pathlib import Path

# Agregar el directorio padre al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from responses import (
    bad_request,
    build_response,
    created,
    forbidden,
    internal_error,
    success,
)


class TestBuildResponse:
    """Tests para build_response."""

    def test_basic_response_structure(self) -> None:
        """Verifica estructura básica de respuesta Lambda Proxy."""
        response = build_response(200, {"key": "value"})

        assert response["statusCode"] == 200
        assert "headers" in response
        assert "body" in response
        assert json.loads(response["body"]) == {"key": "value"}

    def test_default_headers_present(self) -> None:
        """Verifica que los headers CORS por defecto estén presentes."""
        response = build_response(200, {})

        assert response["headers"]["Content-Type"] == "application/json"
        assert "Access-Control-Allow-Origin" in response["headers"]
        assert "Access-Control-Allow-Methods" in response["headers"]

    def test_custom_headers_merged(self) -> None:
        """Verifica que headers custom se añaden a los defaults."""
        response = build_response(
            200, {}, headers={"X-Custom": "test-value"}
        )

        assert response["headers"]["X-Custom"] == "test-value"
        assert response["headers"]["Content-Type"] == "application/json"

    def test_cookies_in_multi_value_headers(self) -> None:
        """Verifica que cookies se envían en multiValueHeaders."""
        cookies = ["session=abc; HttpOnly", "other=xyz"]
        response = build_response(200, {}, cookies=cookies)

        assert response["multiValueHeaders"]["Set-Cookie"] == cookies

    def test_no_cookies_no_multi_value_headers(self) -> None:
        """Verifica que sin cookies no hay multiValueHeaders."""
        response = build_response(200, {})

        assert "multiValueHeaders" not in response


class TestForbidden:
    """Tests para forbidden() — Req 11.6."""

    def test_returns_403(self) -> None:
        """Verifica que retorna status 403."""
        response = forbidden()
        assert response["statusCode"] == 403

    def test_generic_message(self) -> None:
        """Verifica mensaje genérico sin revelar info de recursos."""
        response = forbidden()
        body = json.loads(response["body"])
        assert body["message"] == "Forbidden"

    def test_no_resource_info_leaked(self) -> None:
        """Verifica que no se filtra información del recurso."""
        response = forbidden()
        body = json.loads(response["body"])
        # Solo debe contener "message" con "Forbidden"
        assert set(body.keys()) == {"message"}
        assert "not found" not in body["message"].lower()
        assert "exist" not in body["message"].lower()


class TestSuccess:
    """Tests para success()."""

    def test_returns_200(self) -> None:
        """Verifica que retorna status 200."""
        response = success({"data": "test"})
        assert response["statusCode"] == 200

    def test_body_preserved(self) -> None:
        """Verifica que el body se preserva."""
        response = success({"items": [1, 2, 3]})
        body = json.loads(response["body"])
        assert body == {"items": [1, 2, 3]}


class TestCreated:
    """Tests para created()."""

    def test_returns_201(self) -> None:
        """Verifica que retorna status 201."""
        response = created({"id": "abc-123"})
        assert response["statusCode"] == 201


class TestBadRequest:
    """Tests para bad_request()."""

    def test_returns_400(self) -> None:
        """Verifica que retorna status 400."""
        response = bad_request("Campo requerido faltante")
        assert response["statusCode"] == 400

    def test_message_included(self) -> None:
        """Verifica que el mensaje descriptivo se incluye."""
        response = bad_request("Archivo demasiado grande")
        body = json.loads(response["body"])
        assert body["message"] == "Archivo demasiado grande"


class TestInternalError:
    """Tests para internal_error()."""

    def test_returns_500(self) -> None:
        """Verifica que retorna status 500."""
        response = internal_error()
        assert response["statusCode"] == 500

    def test_generic_message(self) -> None:
        """Verifica que no revela detalles internos."""
        response = internal_error()
        body = json.loads(response["body"])
        assert body["message"] == "Internal server error"
