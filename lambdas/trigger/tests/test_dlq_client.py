"""
Tests unitarios para dlq_client — envío de mensajes a Dead Letter Queue.

Verifica:
- Envío exitoso de mensajes a SQS DLQ
- Inclusión correcta de metadata (event, error, timestamp)
- Manejo de DLQ_URL vacía
- Manejo de errores de SQS
- Formato de message attributes
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dlq_client import send_to_dlq


@pytest.fixture
def mock_sqs_client() -> MagicMock:
    """Crea un mock de cliente SQS."""
    client = MagicMock()
    client.send_message.return_value = {"MessageId": "test-message-id-123"}
    return client


@pytest.fixture
def sample_event() -> dict:
    """Evento S3 de ejemplo."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {
                        "key": "originales/user1/doc1/file.pdf",
                        "size": 1024,
                    },
                }
            }
        ]
    }


class TestSendToDlq:
    """Tests para send_to_dlq."""

    def test_sends_message_successfully(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """Verifica envío exitoso con todos los campos."""
        result = send_to_dlq(
            dlq_url="https://sqs.us-east-1.amazonaws.com/123/test-dlq",
            original_event=sample_event,
            error_message="Error de prueba",
            error_step="TEXTRACT",
            document_id="doc-123",
            user_id="user-456",
            sqs_client=mock_sqs_client,
        )

        assert result is True
        mock_sqs_client.send_message.assert_called_once()

        call_kwargs = mock_sqs_client.send_message.call_args[1]
        assert call_kwargs["QueueUrl"] == (
            "https://sqs.us-east-1.amazonaws.com/123/test-dlq"
        )

        # Verificar body del mensaje
        body = json.loads(call_kwargs["MessageBody"])
        assert body["originalEvent"] == sample_event
        assert body["error"]["message"] == "Error de prueba"
        assert body["error"]["step"] == "TEXTRACT"
        assert "timestamp" in body["error"]
        assert body["metadata"]["documentId"] == "doc-123"
        assert body["metadata"]["userId"] == "user-456"
        assert body["metadata"]["source"] == "lambda-trigger"

    def test_includes_message_attributes(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """Verifica que se incluyen message attributes correctos."""
        send_to_dlq(
            dlq_url="https://sqs.us-east-1.amazonaws.com/123/test-dlq",
            original_event=sample_event,
            error_message="Error",
            error_step="VALIDATION",
            document_id="doc-789",
            sqs_client=mock_sqs_client,
        )

        call_kwargs = mock_sqs_client.send_message.call_args[1]
        attrs = call_kwargs["MessageAttributes"]

        assert attrs["ErrorStep"]["StringValue"] == "VALIDATION"
        assert "ErrorTimestamp" in attrs
        assert attrs["DocumentId"]["StringValue"] == "doc-789"

    def test_omits_document_id_attribute_when_none(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """No incluye DocumentId en attributes si es None."""
        send_to_dlq(
            dlq_url="https://sqs.us-east-1.amazonaws.com/123/test-dlq",
            original_event=sample_event,
            error_message="Error",
            error_step="UNHANDLED",
            document_id=None,
            sqs_client=mock_sqs_client,
        )

        call_kwargs = mock_sqs_client.send_message.call_args[1]
        attrs = call_kwargs["MessageAttributes"]
        assert "DocumentId" not in attrs

    def test_returns_false_when_dlq_url_empty(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """Retorna False si DLQ URL está vacía."""
        result = send_to_dlq(
            dlq_url="",
            original_event=sample_event,
            error_message="Error",
            error_step="TEXTRACT",
            sqs_client=mock_sqs_client,
        )

        assert result is False
        mock_sqs_client.send_message.assert_not_called()

    def test_returns_false_on_client_error(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """Retorna False si SQS lanza ClientError."""
        mock_sqs_client.send_message.side_effect = ClientError(
            error_response={"Error": {"Code": "AWS.SimpleQueueService.NonExistentQueue", "Message": "Queue does not exist"}},
            operation_name="SendMessage",
        )

        result = send_to_dlq(
            dlq_url="https://sqs.us-east-1.amazonaws.com/123/bad-dlq",
            original_event=sample_event,
            error_message="Error",
            error_step="TEXTRACT",
            sqs_client=mock_sqs_client,
        )

        assert result is False

    def test_returns_false_on_unexpected_exception(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """Retorna False si ocurre un error inesperado."""
        mock_sqs_client.send_message.side_effect = RuntimeError("Connection reset")

        result = send_to_dlq(
            dlq_url="https://sqs.us-east-1.amazonaws.com/123/test-dlq",
            original_event=sample_event,
            error_message="Error",
            error_step="UNHANDLED",
            sqs_client=mock_sqs_client,
        )

        assert result is False

    def test_truncates_long_error_message(
        self, mock_sqs_client: MagicMock, sample_event: dict
    ) -> None:
        """Trunca mensajes de error mayores a 2048 caracteres."""
        long_message = "x" * 5000

        send_to_dlq(
            dlq_url="https://sqs.us-east-1.amazonaws.com/123/test-dlq",
            original_event=sample_event,
            error_message=long_message,
            error_step="UNHANDLED",
            sqs_client=mock_sqs_client,
        )

        call_kwargs = mock_sqs_client.send_message.call_args[1]
        body = json.loads(call_kwargs["MessageBody"])
        assert len(body["error"]["message"]) == 2048
