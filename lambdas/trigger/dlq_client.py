"""
Módulo de envío de mensajes a Dead Letter Queue (SQS).

Responsabilidades:
- Enviar mensajes explícitos a la DLQ cuando ocurre un error no manejado.
- Incluir metadata del error, evento original y timestamp.

La DLQ permite capturar errores que de otra forma se perderían cuando
la excepción no propaga correctamente al mecanismo built-in de Lambda.

Req 3.5: Si el pipeline falla, enviar evento a Dead Letter Queue.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def send_to_dlq(
    dlq_url: str,
    original_event: dict[str, Any],
    error_message: str,
    error_step: str,
    document_id: str | None = None,
    user_id: str | None = None,
    sqs_client: Any = None,
) -> bool:
    """
    Envía un mensaje a la Dead Letter Queue con detalles del error.

    El mensaje incluye el evento S3 original, metadata del error y
    timestamp para análisis post-mortem.

    Args:
        dlq_url: URL de la cola SQS (DLQ).
        original_event: Evento S3 original que disparó el Lambda.
        error_message: Descripción del error no manejado.
        error_step: Paso del pipeline donde ocurrió el fallo.
        document_id: Identificador del documento (si disponible).
        user_id: Identificador del usuario (si disponible).
        sqs_client: Cliente boto3 SQS (opcional, se crea uno si no se provee).

    Returns:
        True si el mensaje se envió exitosamente, False en caso de error.
    """
    if not dlq_url:
        logger.warning("DLQ_URL no configurada, no se puede enviar mensaje")
        return False

    if sqs_client is None:
        sqs_client = boto3.client("sqs")

    now = datetime.now(timezone.utc).isoformat()

    message_body = {
        "originalEvent": original_event,
        "error": {
            "message": error_message[:2048],
            "step": error_step,
            "timestamp": now,
        },
        "metadata": {
            "documentId": document_id,
            "userId": user_id,
            "source": "lambda-trigger",
        },
    }

    message_attributes = {
        "ErrorStep": {
            "DataType": "String",
            "StringValue": error_step,
        },
        "ErrorTimestamp": {
            "DataType": "String",
            "StringValue": now,
        },
    }

    if document_id:
        message_attributes["DocumentId"] = {
            "DataType": "String",
            "StringValue": document_id,
        }

    try:
        response = sqs_client.send_message(
            QueueUrl=dlq_url,
            MessageBody=json.dumps(message_body, default=str),
            MessageAttributes=message_attributes,
        )
        message_id = response.get("MessageId", "unknown")
        logger.info(
            "Mensaje enviado a DLQ: MessageId=%s, step=%s, document=%s",
            message_id,
            error_step,
            document_id or "N/A",
        )
        return True
    except ClientError as e:
        logger.error(
            "Error enviando mensaje a DLQ (%s): %s",
            dlq_url,
            e.response["Error"]["Message"],
        )
        return False
    except Exception as e:
        logger.error(
            "Error inesperado enviando a DLQ: %s",
            str(e),
            exc_info=True,
        )
        return False
