"""
Tests unitarios para el handler principal del Lambda Trigger.

Verifica:
- Parsing de eventos S3
- Extracción de componentes del path (userId, documentId, fileName)
- Flujo completo del handler con validaciones
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import sys
import os
from pathlib import Path

# Agregar el directorio del trigger al path para que las imports internas funcionen
_trigger_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "lambdas" / "trigger")
if _trigger_dir not in sys.path:
    sys.path.insert(0, _trigger_dir)

# Importar con importlib y registrar bajo un nombre único para evitar conflictos
import importlib.util


def _load_trigger_module(module_name: str, filename: str):
    """Carga un módulo del trigger con nombre único en sys.modules."""
    filepath = os.path.join(_trigger_dir, filename)
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Cargar los módulos del trigger con nombres únicos
_validator_mod = _load_trigger_module("trigger_validator", "validator.py")
_handler_mod = _load_trigger_module("trigger_handler", "handler.py")

# Importar las funciones que testeamos
S3EventRecord = _handler_mod.S3EventRecord
_build_response = _handler_mod._build_response
_extract_path_components = _handler_mod._extract_path_components
_parse_s3_record = _handler_mod._parse_s3_record
lambda_handler = _handler_mod.lambda_handler
ValidationResult = _validator_mod.ValidationResult


class TestExtractPathComponents:
    """Tests para extracción de componentes del path S3."""

    def test_valid_path_simple_filename(self) -> None:
        """Path con estructura plana y nombre simple."""
        user_id, doc_id, file_name = _extract_path_components(
            "originales/user123/documento.pdf"
        )
        assert user_id == "user123"
        assert doc_id == "documento.pdf"
        assert file_name == "documento.pdf"

    def test_valid_path_complex_filename(self) -> None:
        """Path con nombre de archivo que contiene espacios (URL encoded)."""
        user_id, doc_id, file_name = _extract_path_components(
            "originales/user-abc/mi archivo.pdf"
        )
        assert user_id == "user-abc"
        assert doc_id == "mi archivo.pdf"
        assert file_name == "mi archivo.pdf"

    def test_valid_path_uuid_user(self) -> None:
        """Path con userId tipo UUID."""
        user_id, doc_id, file_name = _extract_path_components(
            "originales/550e8400-e29b-41d4-a716-446655440000/report.pdf"
        )
        assert user_id == "550e8400-e29b-41d4-a716-446655440000"
        assert doc_id == "report.pdf"
        assert file_name == "report.pdf"

    def test_invalid_path_wrong_prefix(self) -> None:
        """Path sin prefijo 'originales/' lanza ValueError."""
        with pytest.raises(ValueError, match="prefijo esperado"):
            _extract_path_components("ofuscados/user/file.pdf")

    def test_invalid_path_too_few_parts(self) -> None:
        """Path con menos de 2 componentes después del prefijo lanza ValueError."""
        with pytest.raises(ValueError, match="patrón"):
            _extract_path_components("originales/file.pdf")

    def test_invalid_path_only_prefix(self) -> None:
        """Path solo con prefijo sin componentes lanza ValueError."""
        with pytest.raises(ValueError, match="patrón"):
            _extract_path_components("originales/")

    def test_invalid_path_empty_user(self) -> None:
        """Path con userId vacío lanza ValueError."""
        with pytest.raises(ValueError, match="userId vacío"):
            _extract_path_components("originales//file.pdf")


class TestParseS3Record:
    """Tests para parsing de registros de eventos S3."""

    def test_valid_record(self) -> None:
        """Record válido se parsea correctamente."""
        record = {
            "s3": {
                "bucket": {"name": "my-bucket"},
                "object": {
                    "key": "originales/user1/test.pdf",
                    "size": 1024,
                },
            }
        }
        result = _parse_s3_record(record)
        assert result.bucket_name == "my-bucket"
        assert result.object_key == "originales/user1/test.pdf"
        assert result.object_size == 1024
        assert result.user_id == "user1"
        assert result.document_id == "test.pdf"
        assert result.file_name == "test.pdf"

    def test_url_encoded_key(self) -> None:
        """Keys URL-encoded se decodifican correctamente."""
        record = {
            "s3": {
                "bucket": {"name": "bucket"},
                "object": {
                    "key": "originales/user1/mi+archivo.pdf",
                    "size": 512,
                },
            }
        }
        result = _parse_s3_record(record)
        assert result.file_name == "mi archivo.pdf"

    def test_missing_bucket_name(self) -> None:
        """Record sin nombre de bucket lanza ValueError."""
        record = {
            "s3": {
                "bucket": {},
                "object": {"key": "originales/u/f.pdf", "size": 100},
            }
        }
        with pytest.raises(ValueError, match="bucket"):
            _parse_s3_record(record)

    def test_missing_object_key(self) -> None:
        """Record sin key de objeto lanza ValueError."""
        record = {
            "s3": {
                "bucket": {"name": "bucket"},
                "object": {"size": 100},
            }
        }
        with pytest.raises(ValueError, match="Key"):
            _parse_s3_record(record)


class TestLambdaHandler:
    """Tests para el handler principal."""

    @patch("trigger_handler.validate_pdf")
    @patch("trigger_handler.register_processing_state")
    @patch("trigger_handler.boto3")
    def test_valid_pdf_registers_processing(
        self, mock_boto3, mock_register, mock_validate
    ) -> None:
        """PDF válido registra estado PROCESSING y retorna 200."""
        mock_validate.return_value = ValidationResult(
            is_valid=True, page_count=5
        )
        mock_register.return_value = True

        event = _build_s3_event(
            "my-bucket", "originales/user1/file.pdf", 1024
        )

        with patch.dict(os.environ, {"DOCUMENTS_TABLE": "test-table"}):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["documentId"] == "doc1"
        assert body["userId"] == "user1"
        assert body["pageCount"] == 5

    @patch("trigger_handler.validate_pdf")
    @patch("trigger_handler._move_to_errors")
    @patch("trigger_handler.update_failed_state")
    @patch("trigger_handler.boto3")
    def test_invalid_pdf_moves_to_errors(
        self, mock_boto3, mock_update_failed, mock_move, mock_validate
    ) -> None:
        """PDF inválido se mueve a errores/ y retorna 400."""
        mock_validate.return_value = ValidationResult(
            is_valid=False,
            error_reason="Archivo no es un PDF válido: no contiene firma %PDF",
        )

        event = _build_s3_event(
            "my-bucket", "originales/user1/fake.pdf", 512
        )

        with patch.dict(os.environ, {"DOCUMENTS_TABLE": "test-table"}):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        mock_move.assert_called_once()

    def test_empty_records_returns_400(self) -> None:
        """Evento sin records retorna 400."""
        event = {"Records": []}
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_invalid_key_format_returns_400(self) -> None:
        """Key con formato inválido retorna 400."""
        event = _build_s3_event("bucket", "invalid/path.pdf", 100)

        with patch("trigger_handler.boto3"):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400


class TestBuildResponse:
    """Tests para la construcción de respuestas."""

    def test_simple_response(self) -> None:
        """Respuesta simple con solo mensaje."""
        result = _build_response(200, "OK")
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "OK"

    def test_response_with_extra(self) -> None:
        """Respuesta con datos adicionales."""
        result = _build_response(200, "OK", extra={"docId": "abc"})
        body = json.loads(result["body"])
        assert body["message"] == "OK"
        assert body["docId"] == "abc"


def _build_s3_event(
    bucket: str, key: str, size: int
) -> dict:
    """Helper para construir un evento S3 de prueba."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key, "size": size},
                }
            }
        ]
    }
