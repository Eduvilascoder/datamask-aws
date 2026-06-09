"""
Lambda Trigger — S3 Event Handler.

Activado por S3 Event Notification cuando un archivo se sube al prefijo originales/.
Responsabilidades:
1. Parsear evento S3 (bucket, key, size, metadata)
2. Extraer userId y documentId del path S3
3. Validar que el archivo es un PDF válido (firma %PDF y parseabilidad)
4. Validar que el tamaño es <500MB
5. Registrar estado PROCESSING en DynamoDB
6. Mover archivos inválidos/excedentes a errores/ con metadatos
7. Invocar Textract (síncrono <15 págs, asíncrono ≥15 págs)
8. Enviar a DLQ en fallo no manejado (tarea 4.3)
9. Publicar métricas custom en CloudWatch (tarea 4.3)
10. Top-level exception handler para errores no anticipados (tarea 4.3)

Runtime: Python 3.12
Timeout: 300s
Memoria: 512MB
"""

import json
import logging
import os
import traceback
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3

from dlq_client import send_to_dlq
from dynamodb_client import register_processing_state, update_failed_state
from metrics import publish_pipeline_error_metric, publish_service_error_metric
from textract_client import TextractResult, extract_text, store_textract_result
from validator import validate_pdf

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Variables de entorno
DOCUMENTS_BUCKET: str = os.environ.get("DOCUMENTS_BUCKET", "")
DOCUMENTS_TABLE: str = os.environ.get("DOCUMENTS_TABLE", "")
DLQ_URL: str = os.environ.get("DLQ_URL", "")
ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "dev")
DETECTION_FUNCTION_NAME: str = os.environ.get("DETECTION_FUNCTION_NAME", "")

# Patrón esperado de key S3: originales/{userId}/{documentId}/{fileName}.pdf
S3_KEY_PREFIX: str = "originales/"
S3_ERRORS_PREFIX: str = "errores/"


@dataclass
class S3EventRecord:
    """Datos extraídos de un registro de evento S3."""

    bucket_name: str
    object_key: str
    object_size: int
    user_id: str
    document_id: str
    file_name: str


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Handler principal para eventos S3 PutObject en originales/.

    Procesa un solo archivo por ejecución para garantizar aislamiento
    de errores (Req 3.2).

    Todo el código está envuelto en un try/except de nivel superior para
    capturar errores no manejados, enviarlos a la DLQ y registrar métricas
    en CloudWatch (Req 3.5).

    Args:
        event: Evento S3 con Records conteniendo información del objeto creado.
        context: Contexto de ejecución Lambda.

    Returns:
        Diccionario con statusCode y body indicando resultado del procesamiento.
    """
    logger.info("Evento recibido: %s", json.dumps(event, default=str))

    try:
        return _process_event(event, context)
    except Exception as e:
        # Top-level exception handler: captura TODO error no manejado
        error_message = f"Error no manejado: {type(e).__name__}: {str(e)}"
        error_traceback = traceback.format_exc()

        logger.error(
            "Error no manejado en Lambda Trigger: %s\n%s",
            error_message,
            error_traceback,
        )

        # Publicar métrica de error del pipeline en CloudWatch
        publish_pipeline_error_metric(
            environment=ENVIRONMENT,
            error_step="UNHANDLED",
        )

        # Enviar a DLQ para análisis post-mortem
        send_to_dlq(
            dlq_url=DLQ_URL,
            original_event=event,
            error_message=f"{error_message}\n{error_traceback}",
            error_step="UNHANDLED",
        )

        # Re-raise para que Lambda runtime registre el error en CloudWatch Logs
        # y active el mecanismo built-in de DLQ si está configurado
        raise


def _process_event(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lógica principal de procesamiento del evento S3.

    Separada del handler para permitir un try/except de nivel superior limpio.

    Args:
        event: Evento S3.
        context: Contexto Lambda.

    Returns:
        Respuesta del procesamiento.
    """

    records = event.get("Records", [])
    if not records:
        logger.warning("Evento sin records, ignorando")
        return _build_response(400, "Evento sin records")

    # Procesar un solo archivo por ejecución (Req 3.2)
    record = records[0]
    if len(records) > 1:
        logger.info(
            "Evento con %d records, procesando solo el primero", len(records)
        )

    try:
        s3_record = _parse_s3_record(record)
    except ValueError as e:
        logger.error("Error parseando evento S3: %s", str(e))
        return _build_response(400, f"Error parseando evento: {str(e)}")

    logger.info(
        "Procesando archivo: bucket=%s, key=%s, size=%d, "
        "user_id=%s, document_id=%s, file_name=%s",
        s3_record.bucket_name,
        s3_record.object_key,
        s3_record.object_size,
        s3_record.user_id,
        s3_record.document_id,
        s3_record.file_name,
    )

    # Crear clientes AWS
    s3_client = boto3.client("s3")
    dynamodb_resource = boto3.resource("dynamodb")

    # Validar PDF (tamaño, firma %PDF, parseabilidad)
    validation_result = validate_pdf(
        bucket=s3_record.bucket_name,
        key=s3_record.object_key,
        object_size=s3_record.object_size,
        s3_client=s3_client,
    )

    if not validation_result.is_valid:
        logger.error(
            "Validación fallida para %s: %s",
            s3_record.object_key,
            validation_result.error_reason,
        )

        # Publicar métrica de error del pipeline (tarea 4.3)
        publish_pipeline_error_metric(
            environment=ENVIRONMENT,
            error_step="VALIDATION",
        )

        _move_to_errors(
            s3_client=s3_client,
            bucket=s3_record.bucket_name,
            original_key=s3_record.object_key,
            user_id=s3_record.user_id,
            document_id=s3_record.document_id,
            file_name=s3_record.file_name,
            error_reason=validation_result.error_reason or "Validación fallida",
        )
        # Registrar FAILED en DynamoDB si hay tabla configurada
        if DOCUMENTS_TABLE:
            update_failed_state(
                table_name=DOCUMENTS_TABLE,
                user_id=s3_record.user_id,
                document_id=s3_record.document_id,
                error_step="VALIDATION",
                error_message=validation_result.error_reason or "Validación fallida",
                dynamodb_resource=dynamodb_resource,
            )

        # Enviar a DLQ (Req 3.5)
        send_to_dlq(
            dlq_url=DLQ_URL,
            original_event=event,
            error_message=validation_result.error_reason or "Validación fallida",
            error_step="VALIDATION",
            document_id=s3_record.document_id,
            user_id=s3_record.user_id,
        )

        return _build_response(
            400,
            f"Archivo inválido: {validation_result.error_reason}",
        )

    # Registrar estado PROCESSING en DynamoDB (Req 3.4)
    if DOCUMENTS_TABLE:
        success = register_processing_state(
            table_name=DOCUMENTS_TABLE,
            user_id=s3_record.user_id,
            document_id=s3_record.document_id,
            file_name=s3_record.file_name,
            file_size=s3_record.object_size,
            s3_key_original=s3_record.object_key,
            dynamodb_resource=dynamodb_resource,
        )
        if not success:
            logger.error(
                "No se pudo registrar estado PROCESSING para %s",
                s3_record.document_id,
            )
            return _build_response(500, "Error registrando estado en DynamoDB")

    logger.info(
        "Archivo validado y estado PROCESSING registrado. "
        "document_id=%s, pages=%s",
        s3_record.document_id,
        validation_result.page_count,
    )

    # Invocar Textract (Req 4.1, 4.2, 4.5)
    textract_client = boto3.client("textract")
    textract_result = extract_text(
        bucket=s3_record.bucket_name,
        key=s3_record.object_key,
        page_count=validation_result.page_count or 1,
        document_id=s3_record.document_id,
        s3_client=s3_client,
        textract_client=textract_client,
    )

    # Manejar errores de Textract (Req 4.4, 4.6)
    if textract_result.error:
        logger.error(
            "Error en Textract para %s: [%s] %s",
            s3_record.document_id,
            textract_result.error_code,
            textract_result.error,
        )

        # Publicar métrica de error de servicio (Req 3.5, tarea 4.3)
        publish_service_error_metric(
            environment=ENVIRONMENT,
            service_name="Textract",
            error_type=textract_result.error_code,
        )
        publish_pipeline_error_metric(
            environment=ENVIRONMENT,
            error_step="TEXTRACT",
        )

        if DOCUMENTS_TABLE:
            update_failed_state(
                table_name=DOCUMENTS_TABLE,
                user_id=s3_record.user_id,
                document_id=s3_record.document_id,
                error_step="TEXTRACT",
                error_message=(
                    f"{textract_result.error_code}: {textract_result.error}"
                ),
                dynamodb_resource=dynamodb_resource,
            )

        # Enviar a DLQ para análisis post-mortem (Req 3.5)
        send_to_dlq(
            dlq_url=DLQ_URL,
            original_event=event,
            error_message=(
                f"{textract_result.error_code}: {textract_result.error}"
            ),
            error_step="TEXTRACT",
            document_id=s3_record.document_id,
            user_id=s3_record.user_id,
        )

        return _build_response(
            500,
            f"Error en Textract: {textract_result.error_code}",
            extra={
                "documentId": s3_record.document_id,
                "errorCode": textract_result.error_code,
            },
        )

    # Almacenar resultado Textract en S3 (Req 4.3)
    stored = store_textract_result(
        result=textract_result,
        bucket=s3_record.bucket_name,
        user_id=s3_record.user_id,
        document_id=s3_record.document_id,
        s3_client=s3_client,
    )
    if not stored:
        logger.error(
            "No se pudo almacenar resultado Textract para %s",
            s3_record.document_id,
        )

    logger.info(
        "Textract completado para documento %s: %d páginas extraídas",
        s3_record.document_id,
        textract_result.total_pages,
    )

    # Invocar Lambda Detección
    if DETECTION_FUNCTION_NAME:
        _invoke_detection_lambda(
            function_name=DETECTION_FUNCTION_NAME,
            bucket=s3_record.bucket_name,
            user_id=s3_record.user_id,
            document_id=s3_record.document_id,
            file_name=s3_record.file_name,
            s3_key_original=s3_record.object_key,
            total_pages=textract_result.total_pages,
        )
    else:
        logger.warning(
            "DETECTION_FUNCTION_NAME no configurada, "
            "no se invocó Lambda Detección"
        )

    return _build_response(
        200,
        "Procesamiento iniciado correctamente",
        extra={
            "documentId": s3_record.document_id,
            "userId": s3_record.user_id,
            "pageCount": validation_result.page_count,
            "totalPagesExtracted": textract_result.total_pages,
        },
    )


def _parse_s3_record(record: dict[str, Any]) -> S3EventRecord:
    """
    Extrae información relevante de un registro de evento S3.

    Parsea bucket, key, size y extrae userId, documentId y fileName
    del patrón de key: originales/{userId}/{documentId}/{fileName}.pdf

    Args:
        record: Registro individual del evento S3.

    Returns:
        S3EventRecord con todos los campos extraídos.

    Raises:
        ValueError: Si el key no sigue el patrón esperado.
    """
    s3_event = record.get("s3", {})
    bucket_name = s3_event.get("bucket", {}).get("name", "")
    object_key = s3_event.get("object", {}).get("key", "")
    object_size = s3_event.get("object", {}).get("size", 0)

    # Decodificar URL-encoded key (S3 events encodean caracteres especiales)
    object_key = urllib.parse.unquote_plus(object_key)

    if not bucket_name:
        raise ValueError("Nombre del bucket no encontrado en el evento")
    if not object_key:
        raise ValueError("Key del objeto no encontrada en el evento")

    # Extraer componentes del path
    # Patrón: originales/{userId}/{documentId}/{fileName}.pdf
    user_id, document_id, file_name = _extract_path_components(object_key)

    return S3EventRecord(
        bucket_name=bucket_name,
        object_key=object_key,
        object_size=object_size,
        user_id=user_id,
        document_id=document_id,
        file_name=file_name,
    )


def _extract_path_components(object_key: str) -> tuple[str, str, str]:
    """
    Extrae userId, documentId y fileName del key S3.

    El patrón esperado es: originales/{userId}/{fileName}.pdf
    El documentId se deriva del fileName (estructura plana, sin UUID intermedio).

    Args:
        object_key: Key completa del objeto S3.

    Returns:
        Tupla (user_id, document_id, file_name).

    Raises:
        ValueError: Si el key no sigue el patrón esperado.
    """
    if not object_key.startswith(S3_KEY_PREFIX):
        raise ValueError(
            f"Key no comienza con prefijo esperado '{S3_KEY_PREFIX}': {object_key}"
        )

    # Remover prefijo "originales/"
    path_without_prefix = object_key[len(S3_KEY_PREFIX):]
    parts = path_without_prefix.split("/")

    if len(parts) < 2:
        raise ValueError(
            f"Key no sigue patrón originales/{{userId}}/{{fileName}}: "
            f"{object_key}"
        )

    user_id = parts[0]
    # El fileName puede contener "/" en teoría, pero tomamos el resto
    file_name = "/".join(parts[1:])
    # documentId determinista a partir del nombre del archivo.
    document_id = file_name

    if not user_id:
        raise ValueError(f"userId vacío en key: {object_key}")
    if not file_name:
        raise ValueError(f"fileName vacío en key: {object_key}")

    return user_id, document_id, file_name


def _invoke_detection_lambda(
    function_name: str,
    bucket: str,
    user_id: str,
    document_id: str,
    file_name: str,
    s3_key_original: str,
    total_pages: int,
) -> None:
    """
    Invoca la Lambda de Detección de PII de forma asíncrona.

    Envía el payload con la información del documento para que la Lambda
    de Detección procese el texto extraído por Textract.

    Args:
        function_name: Nombre de la función Lambda de Detección.
        bucket: Nombre del bucket S3.
        user_id: Identificador del usuario.
        document_id: Identificador del documento.
        file_name: Nombre del archivo original.
        s3_key_original: Key del PDF original en S3.
        total_pages: Total de páginas extraídas por Textract.
    """
    lambda_client = boto3.client("lambda")

    payload = {
        "bucket": bucket,
        "userId": user_id,
        "documentId": document_id,
        "fileName": file_name,
        "s3KeyOriginal": s3_key_original,
        "textractOutputKey": (
            f"procesamiento/{user_id}/{document_id}/textract_output.json"
        ),
        "totalPages": total_pages,
    }

    try:
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",  # Invocación asíncrona
            Payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            "Lambda Detección invocada para documento %s: %s",
            document_id,
            function_name,
        )
    except Exception as e:
        logger.error(
            "Error invocando Lambda Detección para %s: %s",
            document_id,
            str(e),
            exc_info=True,
        )


def _move_to_errors(
    s3_client: Any,
    bucket: str,
    original_key: str,
    user_id: str,
    document_id: str,
    file_name: str,
    error_reason: str,
) -> None:
    """
    Mueve un archivo inválido al prefijo errores/ con metadatos.

    Copia el archivo al prefijo errores/ añadiendo metadatos con el motivo
    del error, timestamp y nombre original. Luego elimina el archivo original.

    Args:
        s3_client: Cliente boto3 S3.
        bucket: Nombre del bucket.
        original_key: Key original del archivo.
        user_id: Identificador del usuario.
        document_id: Identificador del documento.
        file_name: Nombre original del archivo.
        error_reason: Motivo del error de validación.
    """
    error_key = f"{S3_ERRORS_PREFIX}{user_id}/{document_id}/{file_name}"
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Copiar archivo a errores/ con metadatos
        s3_client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": original_key},
            Key=error_key,
            Metadata={
                "error-reason": error_reason[:1024],  # Limitar tamaño de metadata
                "error-timestamp": now,
                "original-filename": file_name,
                "original-key": original_key,
                "user-id": user_id,
                "document-id": document_id,
            },
            MetadataDirective="REPLACE",
        )
        logger.info("Archivo copiado a errores/: %s", error_key)

        # Eliminar archivo original
        s3_client.delete_object(Bucket=bucket, Key=original_key)
        logger.info("Archivo original eliminado: %s", original_key)

    except Exception as e:
        logger.error(
            "Error moviendo archivo a errores/: %s", str(e), exc_info=True
        )


def _build_response(
    status_code: int,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Construye respuesta estándar del handler.

    Args:
        status_code: Código de estado HTTP.
        message: Mensaje descriptivo.
        extra: Datos adicionales opcionales para incluir en el body.

    Returns:
        Diccionario con statusCode y body JSON.
    """
    body: dict[str, Any] = {"message": message}
    if extra:
        body.update(extra)

    return {
        "statusCode": status_code,
        "body": json.dumps(body),
    }
