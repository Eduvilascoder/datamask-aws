"""
Tests unitarios para lambdas/redaction/output_generator.py.

Valida:
- generate_output_filename: generación correcta de nombres de archivo
- generate_markdown_report: formato del informe con título, metadatos y páginas
- upload_results_to_s3: subida de archivos a las rutas correctas en S3
- update_document_completed: actualización de DynamoDB con status COMPLETED
"""

from unittest.mock import MagicMock, patch

import pytest

from lambdas.redaction.output_generator import (
    generate_markdown_report,
    generate_output_filename,
    update_document_completed,
    upload_results_to_s3,
)


# ------------------------------------------------------------------
# Tests para generate_output_filename
# ------------------------------------------------------------------


class TestGenerateOutputFilename:
    """Tests para la función generate_output_filename."""

    def test_pdf_output(self) -> None:
        """Genera nombre correcto para PDF ofuscado."""
        result = generate_output_filename("documento.pdf", "pdf")
        assert result == "documento_ofuscado.pdf"

    def test_markdown_output(self) -> None:
        """Genera nombre correcto para informe Markdown."""
        result = generate_output_filename("documento.pdf", "markdown")
        assert result == "documento_informe.md"

    def test_filename_with_spaces(self) -> None:
        """Maneja nombres con espacios correctamente."""
        result = generate_output_filename("mi documento.pdf", "pdf")
        assert result == "mi documento_ofuscado.pdf"

    def test_filename_with_multiple_dots(self) -> None:
        """Usa solo el stem (hasta la última extensión)."""
        result = generate_output_filename("v2.1.final.pdf", "pdf")
        assert result == "v2.1.final_ofuscado.pdf"

    def test_filename_with_special_chars(self) -> None:
        """Maneja caracteres especiales en el nombre."""
        result = generate_output_filename("año_2024-informe.pdf", "markdown")
        assert result == "año_2024-informe_informe.md"

    def test_invalid_output_type_raises(self) -> None:
        """Lanza ValueError para tipo de salida inválido."""
        with pytest.raises(ValueError, match="output_type debe ser"):
            generate_output_filename("test.pdf", "invalid")

    def test_filename_without_extension(self) -> None:
        """Maneja nombre sin extensión (stem es el nombre completo)."""
        result = generate_output_filename("documento", "pdf")
        assert result == "documento_ofuscado.pdf"


# ------------------------------------------------------------------
# Tests para generate_markdown_report
# ------------------------------------------------------------------


class TestGenerateMarkdownReport:
    """Tests para la función generate_markdown_report."""

    def test_basic_report_structure(self) -> None:
        """Verifica estructura básica: título, metadato, separador, páginas."""
        entities: list[dict] = []
        page_texts = [
            {"page_number": 1, "text": "Texto de la página 1"},
        ]

        result = generate_markdown_report("test.pdf", entities, page_texts)

        assert "# Informe de Ofuscación: test.pdf" in result
        assert "**Archivo original:** test.pdf" in result
        assert "---" in result
        assert "## Página 1" in result
        assert "Texto de la página 1" in result

    def test_multiple_pages(self) -> None:
        """Genera secciones para múltiples páginas."""
        page_texts = [
            {"page_number": 1, "text": "Texto página 1"},
            {"page_number": 2, "text": "Texto página 2"},
            {"page_number": 3, "text": "Texto página 3"},
        ]

        result = generate_markdown_report("doc.pdf", [], page_texts)

        assert "## Página 1" in result
        assert "## Página 2" in result
        assert "## Página 3" in result
        assert "Texto página 1" in result
        assert "Texto página 2" in result
        assert "Texto página 3" in result

    def test_entities_replaced_by_labels(self) -> None:
        """Las entidades PII se reemplazan por [TIPO] en el texto."""
        entities = [
            {
                "type": "NOMBRE",
                "text": "Juan Pérez",
                "page": 1,
                "startOffset": 0,
                "endOffset": 10,
            },
        ]
        page_texts = [
            {"page_number": 1, "text": "Juan Pérez trabaja aquí."},
        ]

        result = generate_markdown_report("doc.pdf", entities, page_texts)

        assert "[NOMBRE]" in result
        assert "Juan Pérez" not in result

    def test_multiple_entities_same_page(self) -> None:
        """Múltiples entidades en la misma página se reemplazan."""
        text = "Juan Pérez, DNI 35.123.456, email juan@mail.com"
        entities = [
            {
                "type": "NOMBRE",
                "text": "Juan Pérez",
                "page": 1,
                "startOffset": 0,
                "endOffset": 10,
            },
            {
                "type": "DNI",
                "text": "35.123.456",
                "page": 1,
                "startOffset": 16,
                "endOffset": 26,
            },
            {
                "type": "EMAIL",
                "text": "juan@mail.com",
                "page": 1,
                "startOffset": 34,
                "endOffset": 47,
            },
        ]
        page_texts = [{"page_number": 1, "text": text}]

        result = generate_markdown_report("doc.pdf", entities, page_texts)

        assert "[NOMBRE]" in result
        assert "[DNI]" in result
        assert "[EMAIL]" in result

    def test_pages_sorted_by_number(self) -> None:
        """Las páginas se ordenan por número ascendente."""
        page_texts = [
            {"page_number": 3, "text": "Tercera"},
            {"page_number": 1, "text": "Primera"},
            {"page_number": 2, "text": "Segunda"},
        ]

        result = generate_markdown_report("doc.pdf", [], page_texts)

        # Verificar orden
        idx1 = result.index("## Página 1")
        idx2 = result.index("## Página 2")
        idx3 = result.index("## Página 3")
        assert idx1 < idx2 < idx3

    def test_empty_page_texts(self) -> None:
        """Con lista vacía de páginas, genera solo encabezado."""
        result = generate_markdown_report("doc.pdf", [], [])

        assert "# Informe de Ofuscación: doc.pdf" in result
        assert "**Archivo original:** doc.pdf" in result
        assert "---" in result
        assert "## Página" not in result

    def test_entity_on_different_page_not_applied(self) -> None:
        """Entidades de otra página no afectan la página actual."""
        entities = [
            {
                "type": "NOMBRE",
                "text": "Juan",
                "page": 2,
                "startOffset": 0,
                "endOffset": 4,
            },
        ]
        page_texts = [
            {"page_number": 1, "text": "Juan está en página 1"},
        ]

        result = generate_markdown_report("doc.pdf", entities, page_texts)

        # La entidad es de página 2, así que página 1 no se modifica
        assert "Juan está en página 1" in result


# ------------------------------------------------------------------
# Tests para upload_results_to_s3
# ------------------------------------------------------------------


class TestUploadResultsToS3:
    """Tests para la función upload_results_to_s3."""

    def test_uploads_both_files(self) -> None:
        """Sube tanto el PDF como el Markdown a S3."""
        mock_s3 = MagicMock()

        result = upload_results_to_s3(
            bucket="test-bucket",
            user_id="user1",
            doc_id="doc123",
            redacted_pdf_bytes=b"%PDF-fake-content",
            markdown_content="# Informe\n\nContenido",
            original_filename="test.pdf",
            s3_client=mock_s3,
        )

        assert mock_s3.put_object.call_count == 2

    def test_correct_s3_keys(self) -> None:
        """Los archivos se suben a las rutas correctas."""
        mock_s3 = MagicMock()

        result = upload_results_to_s3(
            bucket="my-bucket",
            user_id="user1",
            doc_id="doc123",
            redacted_pdf_bytes=b"pdf-bytes",
            markdown_content="# Markdown",
            original_filename="informe_2024.pdf",
            s3_client=mock_s3,
        )

        assert result["s3_key_redacted"] == (
            "ofuscados/user1/doc123/informe_2024_ofuscado.pdf"
        )
        assert result["s3_key_markdown"] == (
            "ofuscados/user1/doc123/informe_2024_informe.md"
        )

    def test_pdf_content_type(self) -> None:
        """El PDF se sube con content-type correcto."""
        mock_s3 = MagicMock()

        upload_results_to_s3(
            bucket="bucket",
            user_id="u1",
            doc_id="d1",
            redacted_pdf_bytes=b"pdf",
            markdown_content="md",
            original_filename="test.pdf",
            s3_client=mock_s3,
        )

        # Primera llamada: PDF
        first_call = mock_s3.put_object.call_args_list[0]
        assert first_call.kwargs["ContentType"] == "application/pdf"

    def test_markdown_content_type(self) -> None:
        """El Markdown se sube con content-type correcto."""
        mock_s3 = MagicMock()

        upload_results_to_s3(
            bucket="bucket",
            user_id="u1",
            doc_id="d1",
            redacted_pdf_bytes=b"pdf",
            markdown_content="md",
            original_filename="test.pdf",
            s3_client=mock_s3,
        )

        # Segunda llamada: Markdown
        second_call = mock_s3.put_object.call_args_list[1]
        assert second_call.kwargs["ContentType"] == (
            "text/markdown; charset=utf-8"
        )

    def test_s3_error_raises_runtime_error(self) -> None:
        """Error de S3 se propaga como RuntimeError."""
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 unavailable")

        with pytest.raises(RuntimeError, match="Error subiendo resultados"):
            upload_results_to_s3(
                bucket="bucket",
                user_id="u1",
                doc_id="d1",
                redacted_pdf_bytes=b"pdf",
                markdown_content="md",
                original_filename="test.pdf",
                s3_client=mock_s3,
            )


# ------------------------------------------------------------------
# Tests para update_document_completed
# ------------------------------------------------------------------


class TestUpdateDocumentCompleted:
    """Tests para la función update_document_completed."""

    def test_successful_update(self) -> None:
        """Actualización exitosa retorna True."""
        mock_table = MagicMock()
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        stats = {
            "s3_key_redacted": "ofuscados/u1/d1/test_ofuscado.pdf",
            "s3_key_markdown": "ofuscados/u1/d1/test_informe.md",
            "entities_found": 5,
            "entities_by_type": {"NOMBRE": 2, "DNI": 3},
            "processing_time_ms": 1500,
        }

        result = update_document_completed(
            table_name="test-table",
            user_id="user1",
            doc_id="doc123",
            stats=stats,
            dynamodb_resource=mock_resource,
        )

        assert result is True
        mock_table.update_item.assert_called_once()

    def test_correct_dynamodb_key(self) -> None:
        """Usa las claves PK/SK correctas."""
        mock_table = MagicMock()
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        update_document_completed(
            table_name="table",
            user_id="user42",
            doc_id="doc99",
            stats={},
            dynamodb_resource=mock_resource,
        )

        call_kwargs = mock_table.update_item.call_args.kwargs
        assert call_kwargs["Key"] == {
            "PK": "USER#user42",
            "SK": "DOC#doc99",
        }

    def test_sets_completed_status(self) -> None:
        """Establece status=COMPLETED en DynamoDB."""
        mock_table = MagicMock()
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        update_document_completed(
            table_name="table",
            user_id="u1",
            doc_id="d1",
            stats={"entities_found": 3, "entities_by_type": {}},
            dynamodb_resource=mock_resource,
        )

        call_kwargs = mock_table.update_item.call_args.kwargs
        assert ":status" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":status"] == (
            "COMPLETED"
        )

    def test_dynamodb_error_returns_false(self) -> None:
        """Error de DynamoDB retorna False."""
        mock_table = MagicMock()
        mock_table.update_item.side_effect = Exception("DynamoDB error")
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        result = update_document_completed(
            table_name="table",
            user_id="u1",
            doc_id="d1",
            stats={},
            dynamodb_resource=mock_resource,
        )

        assert result is False

    def test_stats_fields_in_update(self) -> None:
        """Las estadísticas se incluyen en la actualización."""
        mock_table = MagicMock()
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        stats = {
            "s3_key_redacted": "ofuscados/u1/d1/test_ofuscado.pdf",
            "s3_key_markdown": "ofuscados/u1/d1/test_informe.md",
            "entities_found": 10,
            "entities_by_type": {"NOMBRE": 5, "EMAIL": 3, "DNI": 2},
            "processing_time_ms": 2500,
        }

        update_document_completed(
            table_name="table",
            user_id="u1",
            doc_id="d1",
            stats=stats,
            dynamodb_resource=mock_resource,
        )

        call_kwargs = mock_table.update_item.call_args.kwargs
        values = call_kwargs["ExpressionAttributeValues"]
        assert values[":entities_found"] == 10
        assert values[":entities_by_type"] == {
            "NOMBRE": 5, "EMAIL": 3, "DNI": 2
        }
        assert values[":processing_time_ms"] == 2500
        assert values[":s3_redacted"] == (
            "ofuscados/u1/d1/test_ofuscado.pdf"
        )
        assert values[":s3_markdown"] == (
            "ofuscados/u1/d1/test_informe.md"
        )
