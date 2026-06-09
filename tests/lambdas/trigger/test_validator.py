"""
Tests unitarios para el módulo de validación de PDF (validator.py).

Verifica:
- Validación de tamaño (< 500MB)
- Validación de firma mágica %PDF
- Validación de parseabilidad
"""

import io
from unittest.mock import MagicMock, patch

import pytest

import sys
import os

# Agregar el directorio del módulo al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../lambdas/trigger"))

from validator import (
    MAX_FILE_SIZE_BYTES,
    ValidationResult,
    validate_file_size,
    validate_pdf,
    validate_pdf_header,
    validate_pdf_parseable,
)


class TestValidateFileSize:
    """Tests para validación de tamaño de archivo."""

    def test_valid_size_under_limit(self) -> None:
        """Archivos menores a 500MB son válidos."""
        result = validate_file_size(100 * 1024 * 1024)  # 100MB
        assert result.is_valid is True
        assert result.error_reason is None

    def test_valid_size_zero(self) -> None:
        """Archivos de tamaño 0 pasan la validación de tamaño."""
        result = validate_file_size(0)
        assert result.is_valid is True

    def test_invalid_size_at_limit(self) -> None:
        """Archivos de exactamente 500MB son rechazados (>= 524288000)."""
        result = validate_file_size(MAX_FILE_SIZE_BYTES)
        assert result.is_valid is False
        assert result.error_reason is not None
        assert "500MB" in result.error_reason

    def test_invalid_size_over_limit(self) -> None:
        """Archivos mayores a 500MB son rechazados."""
        result = validate_file_size(MAX_FILE_SIZE_BYTES + 1)
        assert result.is_valid is False
        assert result.error_reason is not None

    def test_valid_size_just_under_limit(self) -> None:
        """Archivos de 1 byte bajo el límite son válidos."""
        result = validate_file_size(MAX_FILE_SIZE_BYTES - 1)
        assert result.is_valid is True


class TestValidatePdfHeader:
    """Tests para validación de firma mágica %PDF."""

    def test_valid_pdf_header(self) -> None:
        """Archivos con firma %PDF son válidos."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"%PDF-1.4 rest of header content"
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = validate_pdf_header("test-bucket", "test-key.pdf", mock_s3)
        assert result.is_valid is True

    def test_invalid_pdf_header_not_pdf(self) -> None:
        """Archivos sin firma %PDF son inválidos."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"PK\x03\x04 this is a zip file"
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = validate_pdf_header("test-bucket", "test-key.pdf", mock_s3)
        assert result.is_valid is False
        assert "%PDF" in result.error_reason

    def test_invalid_pdf_header_empty_file(self) -> None:
        """Archivos vacíos son inválidos."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b""
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = validate_pdf_header("test-bucket", "test-key.pdf", mock_s3)
        assert result.is_valid is False

    def test_s3_read_error(self) -> None:
        """Errores de lectura desde S3 se manejan correctamente."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("Access Denied")

        result = validate_pdf_header("test-bucket", "test-key.pdf", mock_s3)
        assert result.is_valid is False
        assert "Error leyendo" in result.error_reason


class TestValidatePdfParseable:
    """Tests para validación de parseabilidad del PDF."""

    def test_valid_pdf_parseable(self) -> None:
        """PDF válido con contenido parseable."""
        # Crear un PDF mínimo válido
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_content = pdf_buffer.getvalue()

        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = pdf_content
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = validate_pdf_parseable("test-bucket", "test.pdf", mock_s3)
        assert result.is_valid is True
        assert result.page_count == 1

    def test_invalid_pdf_not_parseable(self) -> None:
        """Archivo con encabezado PDF pero contenido corrupto."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"%PDF-1.4 corrupted content here"
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = validate_pdf_parseable("test-bucket", "test.pdf", mock_s3)
        assert result.is_valid is False
        assert result.error_reason is not None

    def test_s3_download_error(self) -> None:
        """Error de descarga se maneja correctamente."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        result = validate_pdf_parseable("test-bucket", "test.pdf", mock_s3)
        assert result.is_valid is False
        assert "Error descargando" in result.error_reason


class TestValidatePdf:
    """Tests de integración para validate_pdf (validación completa)."""

    def test_rejects_oversized_file_without_s3_calls(self) -> None:
        """Archivos > 500MB se rechazan sin descargar desde S3."""
        mock_s3 = MagicMock()

        result = validate_pdf(
            bucket="test-bucket",
            key="test.pdf",
            object_size=MAX_FILE_SIZE_BYTES + 1000,
            s3_client=mock_s3,
        )

        assert result.is_valid is False
        assert "500MB" in result.error_reason
        # No debería hacer llamadas a S3 si el tamaño ya falla
        mock_s3.get_object.assert_not_called()

    def test_rejects_non_pdf_after_header_check(self) -> None:
        """Archivos sin firma %PDF se rechazan en la verificación de header."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"NOT A PDF FILE CONTENT"
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = validate_pdf(
            bucket="test-bucket",
            key="test.pdf",
            object_size=1024,
            s3_client=mock_s3,
        )

        assert result.is_valid is False
        assert "%PDF" in result.error_reason

    def test_accepts_valid_pdf(self) -> None:
        """PDF válido pasa todas las validaciones."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_content = pdf_buffer.getvalue()

        mock_s3 = MagicMock()

        # Primera llamada: header check (range read)
        mock_header_body = MagicMock()
        mock_header_body.read.return_value = pdf_content[:1024]

        # Segunda llamada: full download para parseo
        mock_full_body = MagicMock()
        mock_full_body.read.return_value = pdf_content

        mock_s3.get_object.side_effect = [
            {"Body": mock_header_body},
            {"Body": mock_full_body},
        ]

        result = validate_pdf(
            bucket="test-bucket",
            key="test.pdf",
            object_size=len(pdf_content),
            s3_client=mock_s3,
        )

        assert result.is_valid is True
        assert result.page_count == 2
