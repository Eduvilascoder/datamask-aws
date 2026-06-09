"""
Tests unitarios para textract_client.py.

Valida:
- Selección síncrono/asíncrono según page_count (Req 4.1, 4.5)
- Reconstrucción de páginas y bloques (Req 4.2)
- Almacenamiento en S3 (Req 4.3)
- Manejo de errores TEXTRACT_ERROR (Req 4.4)
- Timeout TEXTRACT_TIMEOUT (Req 4.6)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Agregar el directorio padre al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from textract_client import (
    ASYNC_POLL_INTERVAL_SECONDS,
    ASYNC_TIMEOUT_SECONDS,
    PAGE_THRESHOLD,
    BoundingBox,
    LineBlock,
    PageResult,
    TextractResult,
    WordBlock,
    _reconstruct_pages_from_blocks,
    _textract_result_to_dict,
    extract_text,
    extract_text_async,
    extract_text_sync,
    store_textract_result,
)


class TestExtractTextRouting:
    """Tests para selección automática de modo síncrono/asíncrono."""

    def test_uses_sync_for_less_than_15_pages(self):
        """PDFs con <15 páginas usan DetectDocumentText síncrono."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        # Simular lectura del archivo
        s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"%PDF-fake"))
        }
        # Simular respuesta sync de Textract
        textract_client.detect_document_text.return_value = {"Blocks": []}

        result = extract_text(
            bucket="test-bucket",
            key="originales/user1/doc1/file.pdf",
            page_count=5,
            document_id="doc1",
            s3_client=s3_client,
            textract_client=textract_client,
        )

        textract_client.detect_document_text.assert_called_once()
        textract_client.start_document_text_detection.assert_not_called()

    def test_uses_async_for_15_or_more_pages(self):
        """PDFs con ≥15 páginas usan StartDocumentTextDetection asíncrono."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        # Simular respuesta async
        textract_client.start_document_text_detection.return_value = {
            "JobId": "job-123"
        }
        textract_client.get_document_text_detection.return_value = {
            "JobStatus": "SUCCEEDED",
            "Blocks": [],
        }

        with patch("textract_client.time.sleep"):
            result = extract_text(
                bucket="test-bucket",
                key="originales/user1/doc1/file.pdf",
                page_count=15,
                document_id="doc1",
                s3_client=s3_client,
                textract_client=textract_client,
            )

        textract_client.start_document_text_detection.assert_called_once()
        textract_client.detect_document_text.assert_not_called()

    def test_uses_async_for_exactly_15_pages(self):
        """Exactamente 15 páginas usa el modo asíncrono (≥15)."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        textract_client.start_document_text_detection.return_value = {
            "JobId": "job-abc"
        }
        textract_client.get_document_text_detection.return_value = {
            "JobStatus": "SUCCEEDED",
            "Blocks": [],
        }

        with patch("textract_client.time.sleep"):
            result = extract_text(
                bucket="test-bucket",
                key="originales/user1/doc1/file.pdf",
                page_count=15,
                document_id="doc1",
                s3_client=s3_client,
                textract_client=textract_client,
            )

        textract_client.start_document_text_detection.assert_called_once()

    def test_page_threshold_constant_is_15(self):
        """El umbral de páginas es 15."""
        assert PAGE_THRESHOLD == 15


class TestExtractTextSync:
    """Tests para la extracción síncrona (DetectDocumentText)."""

    def test_successful_extraction(self):
        """Extracción exitosa retorna páginas y bloques."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"%PDF-content"))
        }

        textract_client.detect_document_text.return_value = {
            "Blocks": [
                {
                    "Id": "line-1",
                    "BlockType": "LINE",
                    "Page": 1,
                    "Text": "Hola mundo",
                    "Geometry": {
                        "BoundingBox": {
                            "Left": 0.1,
                            "Top": 0.2,
                            "Width": 0.3,
                            "Height": 0.04,
                        }
                    },
                    "Relationships": [],
                }
            ]
        }

        result = extract_text_sync(
            bucket="test-bucket",
            key="file.pdf",
            document_id="doc1",
            s3_client=s3_client,
            textract_client=textract_client,
        )

        assert result.error is None
        assert result.total_pages == 1
        assert len(result.pages) == 1
        assert result.pages[0].page_number == 1
        assert result.pages[0].blocks[0].text == "Hola mundo"

    def test_s3_read_error(self):
        """Error leyendo de S3 retorna TEXTRACT_ERROR."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        s3_client.get_object.side_effect = Exception("Access Denied")

        result = extract_text_sync(
            bucket="test-bucket",
            key="file.pdf",
            document_id="doc1",
            s3_client=s3_client,
            textract_client=textract_client,
        )

        assert result.error_code == "TEXTRACT_ERROR"
        assert "S3" in result.error

    def test_unsupported_document_error(self):
        """Documento no soportado retorna TEXTRACT_ERROR."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"%PDF-content"))
        }

        # Simular excepción de Textract
        textract_client.exceptions.UnsupportedDocumentException = type(
            "UnsupportedDocumentException", (Exception,), {}
        )
        textract_client.exceptions.InvalidParameterException = type(
            "InvalidParameterException", (Exception,), {}
        )
        textract_client.exceptions.BadDocumentException = type(
            "BadDocumentException", (Exception,), {}
        )
        textract_client.detect_document_text.side_effect = (
            textract_client.exceptions.UnsupportedDocumentException("Encrypted PDF")
        )

        result = extract_text_sync(
            bucket="test-bucket",
            key="file.pdf",
            document_id="doc1",
            s3_client=s3_client,
            textract_client=textract_client,
        )

        assert result.error_code == "TEXTRACT_ERROR"
        assert "no soportado" in result.error

    def test_bad_document_error(self):
        """Documento corrupto retorna TEXTRACT_ERROR."""
        s3_client = MagicMock()
        textract_client = MagicMock()

        s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"%PDF-content"))
        }

        textract_client.exceptions.UnsupportedDocumentException = type(
            "UnsupportedDocumentException", (Exception,), {}
        )
        textract_client.exceptions.InvalidParameterException = type(
            "InvalidParameterException", (Exception,), {}
        )
        textract_client.exceptions.BadDocumentException = type(
            "BadDocumentException", (Exception,), {}
        )
        textract_client.detect_document_text.side_effect = (
            textract_client.exceptions.BadDocumentException("Corrupt file")
        )

        result = extract_text_sync(
            bucket="test-bucket",
            key="file.pdf",
            document_id="doc1",
            s3_client=s3_client,
            textract_client=textract_client,
        )

        assert result.error_code == "TEXTRACT_ERROR"
        assert "corrupto" in result.error.lower() or "corrupt" in result.error.lower()


class TestExtractTextAsync:
    """Tests para la extracción asíncrona (StartDocumentTextDetection)."""

    def test_successful_async_extraction(self):
        """Extracción asíncrona exitosa retorna páginas."""
        textract_client = MagicMock()

        textract_client.start_document_text_detection.return_value = {
            "JobId": "job-123"
        }
        textract_client.get_document_text_detection.return_value = {
            "JobStatus": "SUCCEEDED",
            "Blocks": [
                {
                    "Id": "line-1",
                    "BlockType": "LINE",
                    "Page": 1,
                    "Text": "Texto página 1",
                    "Geometry": {
                        "BoundingBox": {
                            "Left": 0.05,
                            "Top": 0.1,
                            "Width": 0.4,
                            "Height": 0.03,
                        }
                    },
                    "Relationships": [],
                }
            ],
        }

        with patch("textract_client.time.sleep"):
            result = extract_text_async(
                bucket="test-bucket",
                key="file.pdf",
                document_id="doc1",
                textract_client=textract_client,
            )

        assert result.error is None
        assert result.total_pages == 1
        assert result.pages[0].blocks[0].text == "Texto página 1"

    def test_async_timeout(self):
        """Timeout asíncrono retorna TEXTRACT_TIMEOUT."""
        textract_client = MagicMock()

        textract_client.start_document_text_detection.return_value = {
            "JobId": "job-timeout"
        }
        # Siempre retorna IN_PROGRESS
        textract_client.get_document_text_detection.return_value = {
            "JobStatus": "IN_PROGRESS",
            "Blocks": [],
        }

        with patch("textract_client.time.sleep"):
            result = extract_text_async(
                bucket="test-bucket",
                key="file.pdf",
                document_id="doc1",
                textract_client=textract_client,
            )

        assert result.error_code == "TEXTRACT_TIMEOUT"
        assert "240" in result.error

    def test_async_job_failed(self):
        """Job que falla retorna TEXTRACT_ERROR."""
        textract_client = MagicMock()

        textract_client.start_document_text_detection.return_value = {
            "JobId": "job-fail"
        }
        textract_client.get_document_text_detection.return_value = {
            "JobStatus": "FAILED",
            "StatusMessage": "Internal error processing document",
        }

        with patch("textract_client.time.sleep"):
            result = extract_text_async(
                bucket="test-bucket",
                key="file.pdf",
                document_id="doc1",
                textract_client=textract_client,
            )

        assert result.error_code == "TEXTRACT_ERROR"
        assert "Internal error" in result.error

    def test_async_polls_at_5_second_interval(self):
        """El polling usa intervalo de 5 segundos."""
        assert ASYNC_POLL_INTERVAL_SECONDS == 5

    def test_async_timeout_is_240_seconds(self):
        """El timeout asíncrono es de 240 segundos (< 300s de la Lambda)."""
        assert ASYNC_TIMEOUT_SECONDS == 240

    def test_async_pagination_with_next_token(self):
        """Obtiene bloques adicionales con NextToken."""
        textract_client = MagicMock()

        textract_client.start_document_text_detection.return_value = {
            "JobId": "job-pag"
        }

        # Primera respuesta con NextToken
        first_response = {
            "JobStatus": "SUCCEEDED",
            "Blocks": [
                {
                    "Id": "line-1",
                    "BlockType": "LINE",
                    "Page": 1,
                    "Text": "Página 1",
                    "Geometry": {
                        "BoundingBox": {
                            "Left": 0.1, "Top": 0.1,
                            "Width": 0.3, "Height": 0.02,
                        }
                    },
                    "Relationships": [],
                }
            ],
            "NextToken": "token-abc",
        }
        # Segunda respuesta sin NextToken
        second_response = {
            "Blocks": [
                {
                    "Id": "line-2",
                    "BlockType": "LINE",
                    "Page": 2,
                    "Text": "Página 2",
                    "Geometry": {
                        "BoundingBox": {
                            "Left": 0.1, "Top": 0.1,
                            "Width": 0.3, "Height": 0.02,
                        }
                    },
                    "Relationships": [],
                }
            ],
        }

        textract_client.get_document_text_detection.side_effect = [
            first_response, second_response
        ]

        with patch("textract_client.time.sleep"):
            result = extract_text_async(
                bucket="test-bucket",
                key="file.pdf",
                document_id="doc1",
                textract_client=textract_client,
            )

        assert result.error is None
        assert result.total_pages == 2
        assert result.pages[0].page_number == 1
        assert result.pages[1].page_number == 2

    def test_start_document_error(self):
        """Error al iniciar job asíncrono retorna TEXTRACT_ERROR."""
        textract_client = MagicMock()

        textract_client.exceptions.UnsupportedDocumentException = type(
            "UnsupportedDocumentException", (Exception,), {}
        )
        textract_client.exceptions.InvalidParameterException = type(
            "InvalidParameterException", (Exception,), {}
        )
        textract_client.exceptions.BadDocumentException = type(
            "BadDocumentException", (Exception,), {}
        )
        textract_client.start_document_text_detection.side_effect = (
            textract_client.exceptions.BadDocumentException("Bad doc")
        )

        result = extract_text_async(
            bucket="test-bucket",
            key="file.pdf",
            document_id="doc1",
            textract_client=textract_client,
        )

        assert result.error_code == "TEXTRACT_ERROR"


class TestReconstructPages:
    """Tests para reconstrucción de páginas y bloques."""

    def test_orders_pages_by_page_number(self):
        """Las páginas se ordenan por número ascendente."""
        blocks = [
            {
                "Id": "l2",
                "BlockType": "LINE",
                "Page": 2,
                "Text": "Página 2",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
            {
                "Id": "l1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Página 1",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
        ]

        pages = _reconstruct_pages_from_blocks(blocks)

        assert len(pages) == 2
        assert pages[0].page_number == 1
        assert pages[1].page_number == 2

    def test_orders_blocks_top_to_bottom(self):
        """Bloques dentro de una página se ordenan de arriba a abajo."""
        blocks = [
            {
                "Id": "l2",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Línea abajo",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.8, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
            {
                "Id": "l1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Línea arriba",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
        ]

        pages = _reconstruct_pages_from_blocks(blocks)

        assert pages[0].blocks[0].text == "Línea arriba"
        assert pages[0].blocks[1].text == "Línea abajo"

    def test_orders_blocks_left_to_right_same_top(self):
        """Bloques con mismo top se ordenan de izquierda a derecha."""
        blocks = [
            {
                "Id": "l2",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Derecha",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.6, "Top": 0.1, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
            {
                "Id": "l1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Izquierda",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
        ]

        pages = _reconstruct_pages_from_blocks(blocks)

        assert pages[0].blocks[0].text == "Izquierda"
        assert pages[0].blocks[1].text == "Derecha"

    def test_preserves_bounding_box_as_normalized_floats(self):
        """Las coordenadas se preservan como floats normalizados (0-1)."""
        blocks = [
            {
                "Id": "l1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Texto",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.123,
                        "Top": 0.456,
                        "Width": 0.789,
                        "Height": 0.012,
                    }
                },
                "Relationships": [],
            },
        ]

        pages = _reconstruct_pages_from_blocks(blocks)
        bb = pages[0].blocks[0].bounding_box

        assert bb.left == 0.123
        assert bb.top == 0.456
        assert bb.width == 0.789
        assert bb.height == 0.012

    def test_extracts_words_from_line(self):
        """Extrae palabras de un bloque LINE via relación CHILD."""
        blocks = [
            {
                "Id": "word-1",
                "BlockType": "WORD",
                "Page": 1,
                "Text": "Hola",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.2, "Width": 0.05, "Height": 0.02,
                    }
                },
            },
            {
                "Id": "word-2",
                "BlockType": "WORD",
                "Page": 1,
                "Text": "mundo",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.16, "Top": 0.2, "Width": 0.07, "Height": 0.02,
                    }
                },
            },
            {
                "Id": "line-1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Hola mundo",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.2, "Width": 0.13, "Height": 0.02,
                    }
                },
                "Relationships": [
                    {"Type": "CHILD", "Ids": ["word-1", "word-2"]}
                ],
            },
        ]

        pages = _reconstruct_pages_from_blocks(blocks)
        line = pages[0].blocks[0]

        assert len(line.words) == 2
        assert line.words[0].text == "Hola"
        assert line.words[1].text == "mundo"

    def test_ignores_non_line_blocks(self):
        """Solo incluye bloques de tipo LINE, ignora PAGE y otros."""
        blocks = [
            {
                "Id": "page-1",
                "BlockType": "PAGE",
                "Page": 1,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.0, "Top": 0.0, "Width": 1.0, "Height": 1.0,
                    }
                },
            },
            {
                "Id": "line-1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Texto",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02,
                    }
                },
                "Relationships": [],
            },
        ]

        pages = _reconstruct_pages_from_blocks(blocks)

        assert len(pages) == 1
        assert len(pages[0].blocks) == 1

    def test_empty_blocks_returns_empty_pages(self):
        """Lista vacía de bloques retorna lista vacía de páginas."""
        pages = _reconstruct_pages_from_blocks([])
        assert pages == []


class TestStoreTextractResult:
    """Tests para almacenamiento del resultado en S3."""

    def test_stores_at_correct_path(self):
        """Almacena en procesamiento/{userId}/{documentId}/textract_output.json."""
        s3_client = MagicMock()
        result = TextractResult(
            document_id="doc-123",
            pages=[],
            total_pages=0,
            extracted_at="2024-01-15T10:30:00+00:00",
        )

        store_textract_result(
            result=result,
            bucket="my-bucket",
            user_id="user-abc",
            document_id="doc-123",
            s3_client=s3_client,
        )

        call_args = s3_client.put_object.call_args
        assert call_args.kwargs["Bucket"] == "my-bucket"
        assert call_args.kwargs["Key"] == (
            "procesamiento/user-abc/doc-123/textract_output.json"
        )
        assert call_args.kwargs["ContentType"] == "application/json"

    def test_stores_valid_json(self):
        """El contenido almacenado es JSON válido."""
        s3_client = MagicMock()
        result = TextractResult(
            document_id="doc-123",
            pages=[
                PageResult(
                    page_number=1,
                    blocks=[
                        LineBlock(
                            type="LINE",
                            text="Test",
                            bounding_box=BoundingBox(
                                left=0.1, top=0.2, width=0.3, height=0.04
                            ),
                            words=[],
                        )
                    ],
                )
            ],
            total_pages=1,
            extracted_at="2024-01-15T10:30:00+00:00",
        )

        store_textract_result(
            result=result,
            bucket="my-bucket",
            user_id="user-abc",
            document_id="doc-123",
            s3_client=s3_client,
        )

        body_bytes = s3_client.put_object.call_args.kwargs["Body"]
        parsed = json.loads(body_bytes.decode("utf-8"))

        assert parsed["documentId"] == "doc-123"
        assert parsed["totalPages"] == 1
        assert len(parsed["pages"]) == 1
        assert parsed["pages"][0]["pageNumber"] == 1
        assert parsed["pages"][0]["blocks"][0]["text"] == "Test"

    def test_returns_true_on_success(self):
        """Retorna True cuando el almacenamiento es exitoso."""
        s3_client = MagicMock()
        result = TextractResult(
            document_id="doc-123",
            pages=[],
            total_pages=0,
            extracted_at="2024-01-15T10:30:00+00:00",
        )

        success = store_textract_result(
            result=result,
            bucket="bucket",
            user_id="user",
            document_id="doc-123",
            s3_client=s3_client,
        )

        assert success is True

    def test_returns_false_on_error(self):
        """Retorna False cuando hay error al almacenar."""
        s3_client = MagicMock()
        s3_client.put_object.side_effect = Exception("S3 error")

        result = TextractResult(
            document_id="doc-123",
            pages=[],
            total_pages=0,
            extracted_at="2024-01-15T10:30:00+00:00",
        )

        success = store_textract_result(
            result=result,
            bucket="bucket",
            user_id="user",
            document_id="doc-123",
            s3_client=s3_client,
        )

        assert success is False


class TestTextractResultToDict:
    """Tests para serialización del resultado a JSON."""

    def test_json_schema_matches_design_doc(self):
        """El JSON generado sigue el esquema del design doc."""
        result = TextractResult(
            document_id="uuid-test",
            pages=[
                PageResult(
                    page_number=1,
                    blocks=[
                        LineBlock(
                            type="LINE",
                            text="Juan Pérez García",
                            bounding_box=BoundingBox(
                                left=0.05, top=0.12, width=0.25, height=0.02
                            ),
                            words=[
                                WordBlock(
                                    text="Juan",
                                    bounding_box=BoundingBox(
                                        left=0.05, top=0.12,
                                        width=0.06, height=0.02,
                                    ),
                                ),
                                WordBlock(
                                    text="Pérez",
                                    bounding_box=BoundingBox(
                                        left=0.12, top=0.12,
                                        width=0.07, height=0.02,
                                    ),
                                ),
                            ],
                        )
                    ],
                )
            ],
            total_pages=1,
            extracted_at="2024-01-15T10:30:00Z",
        )

        output = _textract_result_to_dict(result)

        assert output["documentId"] == "uuid-test"
        assert output["totalPages"] == 1
        assert output["extractedAt"] == "2024-01-15T10:30:00Z"

        page = output["pages"][0]
        assert page["pageNumber"] == 1

        block = page["blocks"][0]
        assert block["type"] == "LINE"
        assert block["text"] == "Juan Pérez García"
        assert block["boundingBox"]["left"] == 0.05
        assert block["boundingBox"]["top"] == 0.12
        assert block["boundingBox"]["width"] == 0.25
        assert block["boundingBox"]["height"] == 0.02

        word = block["words"][0]
        assert word["text"] == "Juan"
        assert word["boundingBox"]["left"] == 0.05

    def test_includes_error_fields_when_present(self):
        """Incluye campos de error cuando hay error."""
        result = TextractResult(
            document_id="doc-err",
            error="Timeout reached",
            error_code="TEXTRACT_TIMEOUT",
        )

        output = _textract_result_to_dict(result)

        assert output["error"] == "Timeout reached"
        assert output["errorCode"] == "TEXTRACT_TIMEOUT"

    def test_no_error_fields_when_successful(self):
        """No incluye campos de error cuando no hay error."""
        result = TextractResult(
            document_id="doc-ok",
            pages=[],
            total_pages=0,
            extracted_at="2024-01-15T10:30:00Z",
        )

        output = _textract_result_to_dict(result)

        assert "error" not in output
        assert "errorCode" not in output
