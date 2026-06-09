"""
Tests unitarios para el módulo de interacción con DynamoDB.

Verifica:
- Registro de estado PROCESSING
- Actualización a estado FAILED
"""

from unittest.mock import MagicMock, patch

import pytest

import sys
import os

# Agregar el directorio del módulo al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../lambdas/trigger"))

from dynamodb_client import register_processing_state, update_failed_state


class TestRegisterProcessingState:
    """Tests para registro de estado PROCESSING."""

    def test_successful_registration(self) -> None:
        """Registro exitoso retorna True."""
        mock_table = MagicMock()
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        result = register_processing_state(
            table_name="test-table",
            user_id="user-123",
            document_id="doc-456",
            file_name="report.pdf",
            file_size=1024,
            s3_key_original="originales/user-123/doc-456/report.pdf",
            dynamodb_resource=mock_dynamodb,
        )

        assert result is True
        mock_table.put_item.assert_called_once()

        # Verificar contenido del item
        call_args = mock_table.put_item.call_args
        item = call_args[1]["Item"] if "Item" in call_args[1] else call_args[0][0]
        if isinstance(call_args, tuple) and call_args[1]:
            item = call_args[1]["Item"]
        else:
            item = call_args.kwargs["Item"]

        assert item["PK"] == "USER#user-123"
        assert item["SK"] == "DOC#doc-456"
        assert item["status"] == "PROCESSING"
        assert item["fileName"] == "report.pdf"
        assert item["fileSize"] == 1024

    def test_dynamodb_error_returns_false(self) -> None:
        """Error de DynamoDB retorna False."""
        from botocore.exceptions import ClientError

        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Internal error"}},
            "PutItem",
        )
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        result = register_processing_state(
            table_name="test-table",
            user_id="user-123",
            document_id="doc-456",
            file_name="report.pdf",
            file_size=1024,
            s3_key_original="originales/user-123/doc-456/report.pdf",
            dynamodb_resource=mock_dynamodb,
        )

        assert result is False


class TestUpdateFailedState:
    """Tests para actualización de estado FAILED."""

    def test_successful_update(self) -> None:
        """Actualización exitosa retorna True."""
        mock_table = MagicMock()
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        result = update_failed_state(
            table_name="test-table",
            user_id="user-123",
            document_id="doc-456",
            error_step="VALIDATION",
            error_message="No contiene firma %PDF",
            dynamodb_resource=mock_dynamodb,
        )

        assert result is True
        mock_table.update_item.assert_called_once()

        # Verificar que se actualiza con los valores correctos
        call_kwargs = mock_table.update_item.call_args.kwargs
        assert call_kwargs["Key"]["PK"] == "USER#user-123"
        assert call_kwargs["Key"]["SK"] == "DOC#doc-456"
        assert call_kwargs["ExpressionAttributeValues"][":status"] == "FAILED"
        assert call_kwargs["ExpressionAttributeValues"][":error_step"] == "VALIDATION"

    def test_dynamodb_error_returns_false(self) -> None:
        """Error de DynamoDB retorna False."""
        from botocore.exceptions import ClientError

        mock_table = MagicMock()
        mock_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Internal error"}},
            "UpdateItem",
        )
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        result = update_failed_state(
            table_name="test-table",
            user_id="user-123",
            document_id="doc-456",
            error_step="VALIDATION",
            error_message="Error de prueba",
            dynamodb_resource=mock_dynamodb,
        )

        assert result is False
