"""
Módulo de interacción con DynamoDB para el estado de documentos.

Responsabilidades:
- Registrar estado PROCESSING en DynamoDB.
- Actualizar estado a FAILED con detalles del error.

Schema DynamoDB (Single Table Design):
  PK: USER#{userId}
  SK: DOC#{documentId}
  GSI1PK: USER#{userId}
  GSI1SK: STATUS#{status}#TIMESTAMP#{uploadedAt}
"""

import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def register_processing_state(
    table_name: str,
    user_id: str,
    document_id: str,
    file_name: str,
    file_size: int,
    s3_key_original: str,
    dynamodb_resource: Any = None,
) -> bool:
    """
    Registra el estado PROCESSING de un documento en DynamoDB.

    Crea o actualiza el ítem del documento con estado PROCESSING,
    timestamp actual y metadatos del archivo.

    Args:
        table_name: Nombre de la tabla DynamoDB.
        user_id: Identificador del usuario (extraído de la key S3).
        document_id: Identificador del documento (UUID del path S3).
        file_name: Nombre original del archivo PDF.
        file_size: Tamaño del archivo en bytes.
        s3_key_original: Key completa del objeto en S3.
        dynamodb_resource: Recurso DynamoDB (opcional, se crea uno si no se provee).

    Returns:
        True si la operación fue exitosa, False en caso de error.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")

    table = dynamodb_resource.Table(table_name)
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "PK": f"USER#{user_id}",
        "SK": f"DOC#{document_id}",
        "GSI1PK": f"USER#{user_id}",
        "GSI1SK": f"STATUS#PROCESSING#TIMESTAMP#{now}",
        "documentId": document_id,
        "userId": user_id,
        "fileName": file_name,
        "fileSize": file_size,
        "s3KeyOriginal": s3_key_original,
        "s3KeyRedacted": None,
        "s3KeyMarkdown": None,
        "status": "PROCESSING",
        "errorMessage": None,
        "errorStep": None,
        "entitiesFound": None,
        "entitiesByType": None,
        "processingTimeMs": None,
        "uploadedAt": now,
        "completedAt": None,
    }

    try:
        table.put_item(Item=item)
        logger.info(
            "Estado PROCESSING registrado para documento %s del usuario %s",
            document_id,
            user_id,
        )
        return True
    except ClientError as e:
        logger.error(
            "Error registrando estado PROCESSING en DynamoDB: %s",
            e.response["Error"]["Message"],
        )
        return False


def update_failed_state(
    table_name: str,
    user_id: str,
    document_id: str,
    error_step: str,
    error_message: str,
    dynamodb_resource: Any = None,
) -> bool:
    """
    Actualiza el estado de un documento a FAILED en DynamoDB.

    Args:
        table_name: Nombre de la tabla DynamoDB.
        user_id: Identificador del usuario.
        document_id: Identificador del documento.
        error_step: Paso del pipeline donde ocurrió el fallo.
        error_message: Descripción del error.
        dynamodb_resource: Recurso DynamoDB (opcional).

    Returns:
        True si la actualización fue exitosa, False en caso de error.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")

    table = dynamodb_resource.Table(table_name)
    now = datetime.now(timezone.utc).isoformat()

    try:
        table.update_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{document_id}",
            },
            UpdateExpression=(
                "SET #status = :status, "
                "errorStep = :error_step, "
                "errorMessage = :error_message, "
                "GSI1SK = :gsi1sk, "
                "completedAt = :completed_at"
            ),
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":error_step": error_step,
                ":error_message": error_message,
                ":gsi1sk": f"STATUS#FAILED#TIMESTAMP#{now}",
                ":completed_at": now,
            },
        )
        logger.info(
            "Estado FAILED registrado para documento %s (paso: %s)",
            document_id,
            error_step,
        )
        return True
    except ClientError as e:
        logger.error(
            "Error actualizando estado FAILED en DynamoDB: %s",
            e.response["Error"]["Message"],
        )
        return False
