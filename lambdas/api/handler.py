"""
Lambda API Handler para DataMask AWS.

Runtime: Python 3.12
Integración: API Gateway REST (Lambda Proxy Integration)

La autenticación se resuelve en el frontend contra IAM Identity Center
(OIDC Device Authorization Flow). El API Gateway usa autorización AWS_IAM:
cada request llega firmado con SigV4 usando las credenciales temporales (STS)
del usuario, y la identidad se deriva en `middleware.authenticate_request`.

Endpoints:
  POST /upload/presign   — Generar presigned URLs para S3
  POST /documents        — Registrar documento subido
  GET  /documents        — Listar documentos del usuario
  GET  /documents/{id}   — Detalle de un documento
  GET  /documents/{id}/download/{type} — Presigned URL descarga
  GET  /config           — Obtener configuración PII
  PUT  /config           — Actualizar configuración PII
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from config import AppConfig, load_config
from middleware import authenticate_request, get_user_id
import detection_config
import macie_findings
import macie_jobs
import notifications
from responses import bad_request, build_response, forbidden, internal_error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Presigned URL expirations (en segundos)
UPLOAD_URL_EXPIRATION_SECONDS = 300  # Req 2.3: 5 minutos
DOWNLOAD_URL_EXPIRATION_SECONDS = 300  # Req 9.3: 5 minutos

# Máximo archivos por operación de upload (Req 2.2)
MAX_FILES_PER_UPLOAD = 30

# Tamaño máximo de archivo para upload (50 MB) en bytes (Req 2.2)
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

# Paginación por defecto
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Cargar configuración una vez (reutilizada entre invocaciones)
_config: AppConfig | None = None

# Clientes boto3 (singleton por instancia Lambda)
_s3_client = None
_dynamodb_resource = None


def get_config() -> AppConfig:
    """Obtiene la configuración cargada (singleton por instancia Lambda)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_s3_client():
    """Obtiene el cliente S3 con Signature V4 (requerido para SSE-KMS)."""
    global _s3_client
    if _s3_client is None:
        from botocore.config import Config

        _s3_client = boto3.client(
            "s3",
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


def get_dynamodb_table():
    """Obtiene el recurso DynamoDB Table (singleton por instancia Lambda)."""
    global _dynamodb_resource
    if _dynamodb_resource is None:
        dynamodb = boto3.resource("dynamodb")
        config = get_config()
        _dynamodb_resource = dynamodb.Table(config.documents_table)
    return _dynamodb_resource


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Punto de entrada de la Lambda API Handler.

    Recibe eventos de API Gateway (Lambda Proxy Integration) y rutea
    la solicitud al handler apropiado según método HTTP y path.

    Flujo:
    1. Parsear método y path del evento
    2. Aplicar middleware de autenticación (excepto rutas públicas)
    3. Rutear al handler correspondiente
    4. Retornar respuesta estandarizada

    Args:
        event: Evento de API Gateway (Lambda Proxy format).
        context: Contexto de ejecución Lambda.

    Returns:
        Respuesta HTTP en formato Lambda Proxy.
    """
    http_method = event.get("httpMethod", "")
    path = event.get("path", "")
    config = get_config()

    # No logear datos sensibles — solo método y path
    logger.info("Request: %s %s", http_method, path)

    try:
        # Aplicar middleware de autenticación
        auth_response = authenticate_request(event, config)
        if auth_response is not None:
            return auth_response

        # Rutear la solicitud
        response = route_request(http_method, path, event)
    except Exception:
        logger.exception("Error procesando request %s %s", http_method, path)
        response = internal_error()

    return response


def route_request(
    method: str, path: str, event: dict[str, Any]
) -> dict[str, Any]:
    """
    Rutea la solicitud al handler correspondiente según método y path.

    Req 11.6: Rutas no reconocidas retornan 403 genérico (no 404)
    para no revelar la existencia de recursos.

    Args:
        method: Método HTTP (GET, POST, PUT, etc.).
        path: Path de la solicitud.
        event: Evento completo de API Gateway.

    Returns:
        Respuesta HTTP en formato Lambda Proxy.
    """
    # La autenticación se resuelve con Amazon Cognito (federado con IAM
    # Identity Center). API Gateway valida el JWT con el authorizer Cognito
    # antes de invocar este Lambda; el middleware deriva el usuario del token.

    if path == "/upload/presign" and method == "POST":
        return handle_upload_presign(event)
    if path == "/documents/s3/list" and method == "GET":
        return handle_s3_list(event)
    if path == "/documents/s3/download" and method == "GET":
        return handle_s3_download(event)
    if path == "/documents/s3/object" and method == "DELETE":
        return handle_s3_delete(event)
    if path == "/documents/process" and method == "POST":
        return handle_process_documents(event)
    if path == "/documents/purge" and method == "POST":
        return handle_purge_processed(event)
    if path == "/documents" and method == "POST":
        return handle_create_document(event)
    if path == "/documents" and method == "GET":
        return handle_list_documents(event)
    if path == "/documents" and method == "DELETE":
        return handle_purge_audit(event)
    if (
        path.startswith("/documents/")
        and "/download/" in path
        and method == "GET"
    ):
        return handle_download_document(event)
    if path.startswith("/documents/") and method == "GET":
        return handle_get_document(event)
    if path.startswith("/documents/") and method == "DELETE":
        return handle_delete_document(event)
    if path == "/config/detection" and method == "GET":
        return handle_get_detection_config(event)
    if path == "/config/detection" and method == "PUT":
        return handle_update_detection_config(event)
    if path == "/config/models" and method == "GET":
        return handle_get_models(event)
    if path == "/macie/findings" and method == "GET":
        return handle_get_macie_findings(event)
    if path == "/macie/jobs" and method == "POST":
        return handle_create_macie_jobs(event)

    # Req 11.6: HTTP 403 genérico sin revelar existencia del recurso
    return forbidden()


# =============================================================================
# =============================================================================
# Upload, documentos y configuración
# =============================================================================

def handle_upload_presign(event: dict[str, Any]) -> dict[str, Any]:
    """
    Genera presigned URLs para subida directa a S3.

    Acepta un array de archivos [{fileName, fileSize}] y genera
    presigned PUT URLs para cada uno en el prefijo originales/.

    Req 2.3: Presigned URLs con expiración de 300 segundos.
    Req 2.4: Si la generación falla, retorna error sin iniciar transferencia.
    Req 11.2: Acceso solo via presigned URLs.

    Args:
        event: Evento de API Gateway con body conteniendo `files` array.

    Returns:
        Respuesta 200 con array de presigned URLs, o error.
    """
    config = get_config()
    user_id = get_user_id(event)

    if not user_id:
        return forbidden()

    body = _parse_body(event)
    files = body.get("files", [])

    # Validar que se envió al menos un archivo
    if not files:
        return bad_request("Se requiere al menos un archivo")

    # Validar máximo de archivos por operación (Req 2.2)
    if len(files) > MAX_FILES_PER_UPLOAD:
        return bad_request(
            f"Máximo {MAX_FILES_PER_UPLOAD} archivos por operación"
        )

    # Validar cada archivo
    for file_info in files:
        file_name = file_info.get("fileName", "")
        file_size = file_info.get("fileSize", 0)

        if not file_name:
            return bad_request("Cada archivo debe tener un fileName")

        if not file_name.lower().endswith(".pdf"):
            return bad_request(
                f"Solo se aceptan archivos PDF: {file_name}"
            )

        if file_size > MAX_FILE_SIZE_BYTES:
            return bad_request(
                f"El archivo {file_name} excede el tamaño máximo de 50 MB"
            )

    # Generar presigned URLs (Req 2.3: expiración 300s)
    s3_client = get_s3_client()
    presigned_urls = []

    try:
        for file_info in files:
            file_name = file_info.get("fileName", "")
            # Estructura plana: originales/{userId}/{fileName}. El documentId se
            # deriva del nombre del archivo (mismo archivo = mismo documento),
            # evitando un segmento UUID intermedio en originales/.
            s3_key = f"originales/{user_id}/{file_name}"
            document_id = file_name

            presigned_url = s3_client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": config.documents_bucket,
                    "Key": s3_key,
                    "ContentType": "application/pdf",
                    "ServerSideEncryption": "aws:kms",
                },
                ExpiresIn=UPLOAD_URL_EXPIRATION_SECONDS,
            )

            presigned_urls.append({
                "fileName": file_name,
                "documentId": document_id,
                "uploadUrl": presigned_url,
                "s3Key": s3_key,
            })
    except ClientError:
        # Req 2.4: Si falla, retornar error sin iniciar transferencia
        logger.exception("Error generando presigned URLs")
        return build_response(500, {
            "message": "No se pudo preparar la subida. Intente nuevamente."
        })

    return build_response(200, {"uploads": presigned_urls})


def handle_create_document(event: dict[str, Any]) -> dict[str, Any]:
    """
    Registra un documento subido en DynamoDB con status=UPLOADED.

    El frontend llama a este endpoint después de completar la subida
    directa a S3 para registrar los metadatos del documento.

    Args:
        event: Evento de API Gateway con body conteniendo metadatos del documento.

    Returns:
        Respuesta 201 con el registro del documento creado.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    body = _parse_body(event)

    # Campos obligatorios
    document_id = body.get("documentId", "")
    file_name = body.get("fileName", "")
    file_size = body.get("fileSize", 0)
    s3_key = body.get("s3Key", "")

    if not document_id or not file_name or not s3_key:
        return bad_request(
            "Se requieren documentId, fileName y s3Key"
        )

    now = datetime.now(timezone.utc).isoformat()
    table = get_dynamodb_table()

    item = {
        "PK": f"USER#{user_id}",
        "SK": f"DOC#{document_id}",
        "GSI1PK": f"USER#{user_id}",
        "GSI1SK": f"STATUS#UPLOADED#TIMESTAMP#{now}",
        "documentId": document_id,
        "userId": user_id,
        "fileName": file_name,
        "fileSize": file_size,
        "s3KeyOriginal": s3_key,
        "s3KeyRedacted": None,
        "s3KeyMarkdown": None,
        "status": "UPLOADED",
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
    except ClientError:
        logger.exception("Error registrando documento en DynamoDB")
        return internal_error()

    # Retornar el documento creado (sin claves internas PK/SK/GSI)
    document_response = {
        "documentId": document_id,
        "fileName": file_name,
        "fileSize": file_size,
        "status": "UPLOADED",
        "uploadedAt": now,
    }

    return build_response(201, document_response)


def handle_list_documents(event: dict[str, Any]) -> dict[str, Any]:
    """
    Lista documentos del usuario paginados, ordenados por fecha descendente.

    Usa GSI1 para consultar por usuario. Los documentos se ordenan por
    GSI1SK (STATUS#status#TIMESTAMP#uploadedAt) en orden descendente.

    Req 9.1: Ordenados por fecha de subida descendente, mostrando status.

    Args:
        event: Evento de API Gateway con query parameters opcionales:
               - limit: cantidad de resultados (default 20, max 100)
               - nextToken: token de paginación (base64 encoded)

    Returns:
        Respuesta 200 con lista paginada de documentos.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    # Parsear parámetros de paginación
    query_params = event.get("queryStringParameters") or {}
    limit = min(
        int(query_params.get("limit", str(DEFAULT_PAGE_SIZE))),
        MAX_PAGE_SIZE,
    )

    table = get_dynamodb_table()

    # Construir query kwargs
    query_kwargs: dict[str, Any] = {
        "IndexName": "GSI1",
        "KeyConditionExpression": "GSI1PK = :pk",
        "ExpressionAttributeValues": {":pk": f"USER#{user_id}"},
        "ScanIndexForward": False,  # Descendente por fecha
        "Limit": limit,
    }

    # Token de paginación (si se proporciona)
    next_token = query_params.get("nextToken")
    if next_token:
        try:
            exclusive_start_key = json.loads(
                base64.b64decode(next_token).decode("utf-8")
            )
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key
        except (ValueError, json.JSONDecodeError):
            return bad_request("Token de paginación inválido")

    try:
        response = table.query(**query_kwargs)
    except ClientError:
        logger.exception("Error consultando documentos en DynamoDB")
        return internal_error()

    # Mapear items a respuesta (sin claves internas)
    documents = []
    for item in response.get("Items", []):
        doc = {
            "documentId": item.get("documentId"),
            "fileName": item.get("fileName"),
            "fileSize": item.get("fileSize"),
            "status": item.get("status"),
            "uploadedAt": item.get("uploadedAt"),
            "completedAt": item.get("completedAt"),
            "entitiesFound": item.get("entitiesFound"),
            "entitiesByType": item.get("entitiesByType"),
            "processingTimeMs": item.get("processingTimeMs"),
            "errorMessage": item.get("errorMessage"),
            "errorStep": item.get("errorStep"),
            "engine": item.get("engine"),
            "s3KeyRedacted": item.get("s3KeyRedacted"),
        }
        documents.append(doc)

    # Generar token de paginación para la siguiente página
    result: dict[str, Any] = {"documents": documents}

    last_key = response.get("LastEvaluatedKey")
    if last_key:
        result["nextToken"] = base64.b64encode(
            json.dumps(last_key).encode("utf-8")
        ).decode("utf-8")

    return build_response(200, result)


def handle_get_document(event: dict[str, Any]) -> dict[str, Any]:
    """
    Obtiene detalle de un documento por ID.

    Extrae el documentId del path y consulta DynamoDB.
    Retorna 403 genérico si no existe (Req 11.6: no revelar existencia).

    Args:
        event: Evento de API Gateway con path /documents/{id}.

    Returns:
        Respuesta 200 con detalle del documento, o 403 si no existe.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    # Extraer documentId del path
    path = event.get("path", "")
    path_parts = path.strip("/").split("/")
    if len(path_parts) < 2:
        return forbidden()

    document_id = path_parts[1]  # /documents/{id}

    table = get_dynamodb_table()

    try:
        response = table.get_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{document_id}",
            }
        )
    except ClientError:
        logger.exception("Error obteniendo documento de DynamoDB")
        return internal_error()

    item = response.get("Item")
    if not item:
        # Req 11.6: No revelar si el recurso existe
        return forbidden()

    # Construir respuesta con estadísticas (Req 9.5)
    document = {
        "documentId": item.get("documentId"),
        "fileName": item.get("fileName"),
        "fileSize": item.get("fileSize"),
        "status": item.get("status"),
        "s3KeyOriginal": item.get("s3KeyOriginal"),
        "s3KeyRedacted": item.get("s3KeyRedacted"),
        "s3KeyMarkdown": item.get("s3KeyMarkdown"),
        "uploadedAt": item.get("uploadedAt"),
        "completedAt": item.get("completedAt"),
        "entitiesFound": item.get("entitiesFound"),
        "entitiesByType": item.get("entitiesByType"),
        "processingTimeMs": item.get("processingTimeMs"),
        "errorMessage": item.get("errorMessage"),
        "errorStep": item.get("errorStep"),
        "engine": item.get("engine"),
    }

    return build_response(200, document)


def handle_delete_document(event: dict[str, Any]) -> dict[str, Any]:
    """
    Elimina un documento del registro de auditoría.

    Borra el item de DynamoDB y los objetos S3 asociados (original,
    PDF ofuscado e informe Markdown), si existen. Solo permite borrar
    documentos del propio usuario.

    Args:
        event: Evento de API Gateway con path /documents/{id}.

    Returns:
        Respuesta 200 al borrar, o 403 si no existe / no pertenece al usuario.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    # Extraer documentId del path
    path = event.get("path", "")
    path_parts = path.strip("/").split("/")
    if len(path_parts) < 2:
        return forbidden()

    document_id = path_parts[1]  # /documents/{id}

    table = get_dynamodb_table()

    try:
        response = table.get_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{document_id}",
            }
        )
    except ClientError:
        logger.exception("Error obteniendo documento para eliminar")
        return internal_error()

    item = response.get("Item")
    if not item:
        # Req 11.6: No revelar si el recurso existe
        return forbidden()

    # Borrar objetos S3 asociados (best-effort; no bloquea el borrado del item)
    config = get_config()
    s3_client = get_s3_client()
    s3_keys = [
        item.get("s3KeyOriginal"),
        item.get("s3KeyRedacted"),
        item.get("s3KeyMarkdown"),
    ]
    for s3_key in s3_keys:
        if not s3_key:
            continue
        try:
            s3_client.delete_object(Bucket=config.documents_bucket, Key=s3_key)
        except ClientError:
            logger.warning("No se pudo borrar objeto S3: %s", s3_key)

    # Borrar item de DynamoDB
    try:
        table.delete_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{document_id}",
            }
        )
    except ClientError:
        logger.exception("Error eliminando documento de DynamoDB")
        return internal_error()

    return build_response(200, {"message": "Documento eliminado", "documentId": document_id})


def handle_download_document(event: dict[str, Any]) -> dict[str, Any]:
    """
    Genera presigned URL para descarga de documento procesado.

    Soporta tipo 'pdf' (documento ofuscado) y 'markdown' (informe).
    Solo permite descarga si el documento tiene status COMPLETED.

    Req 9.3: Presigned URL con expiración de 5 minutos.
    Req 9.4: Si URL expirada o archivo no existe, responder con error.
    Req 11.2: Acceso solo via presigned URLs.

    Args:
        event: Evento de API Gateway con path /documents/{id}/download/{type}.

    Returns:
        Respuesta 200 con downloadUrl, o error.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    # Extraer documentId y tipo de descarga del path
    path = event.get("path", "")
    path_parts = path.strip("/").split("/")
    # Expected: ["documents", "{id}", "download", "{type}"]
    if len(path_parts) < 4:
        return forbidden()

    document_id = path_parts[1]
    download_type = path_parts[3]

    # Validar tipo de descarga (Req 9.2: PDF + markdown)
    if download_type not in ("pdf", "markdown"):
        return bad_request(
            "Tipo de descarga inválido. Usar 'pdf' o 'markdown'."
        )

    # Obtener documento de DynamoDB
    table = get_dynamodb_table()

    try:
        response = table.get_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{document_id}",
            }
        )
    except ClientError:
        logger.exception("Error obteniendo documento de DynamoDB")
        return internal_error()

    item = response.get("Item")
    if not item:
        # Req 11.6: No revelar existencia
        return forbidden()

    # Solo permitir descarga de documentos COMPLETED
    status = item.get("status")
    if status != "COMPLETED":
        return bad_request(
            "El documento no está disponible para descarga"
        )

    # Determinar la S3 key según el tipo de descarga
    if download_type == "pdf":
        s3_key = item.get("s3KeyRedacted")
    else:  # markdown
        s3_key = item.get("s3KeyMarkdown")

    if not s3_key:
        # Req 9.4: Archivo no disponible
        return build_response(404, {
            "message": "El recurso solicitado no está disponible"
        })

    # Verificar que el archivo existe en S3 antes de generar URL
    config = get_config()
    s3_client = get_s3_client()

    try:
        s3_client.head_object(
            Bucket=config.documents_bucket,
            Key=s3_key,
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            # Req 9.4: Archivo no existe
            return build_response(404, {
                "message": "El recurso solicitado no está disponible"
            })
        logger.exception("Error verificando archivo en S3")
        return internal_error()

    # Generar presigned URL de descarga (Req 9.3: 5 min)
    try:
        download_url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": config.documents_bucket,
                "Key": s3_key,
            },
            ExpiresIn=DOWNLOAD_URL_EXPIRATION_SECONDS,
        )
    except ClientError:
        logger.exception("Error generando presigned URL de descarga")
        return build_response(500, {
            "message": "No se pudo generar la URL de descarga"
        })

    return build_response(200, {
        "downloadUrl": download_url,
        "fileName": item.get("fileName"),
        "type": download_type,
        "expiresIn": DOWNLOAD_URL_EXPIRATION_SECONDS,
    })


def handle_get_detection_config(event: dict[str, Any]) -> dict[str, Any]:
    """Obtiene la configuración avanzada de detección del usuario."""
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    config = get_config()
    dynamodb = boto3.resource("dynamodb")
    detection = detection_config.get_detection_config(
        user_id=user_id,
        table_name=config.documents_table,
        dynamodb_resource=dynamodb,
    )
    return build_response(200, {
        "config": detection,
        "availableModels": detection_config.list_available_models(),
    })


def handle_get_macie_findings(event: dict[str, Any]) -> dict[str, Any]:
    """Devuelve los hallazgos de Amazon Macie del usuario actual.

    Para la pantalla "Monitoreo PII — Macie". Si Macie no está habilitado en
    la cuenta, responde con macieEnabled=false y un mensaje explicativo.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    config = get_config()
    try:
        result = macie_findings.get_macie_findings(
            user_id=user_id,
            bucket=config.documents_bucket,
        )
    except Exception:
        logger.exception("Error obteniendo hallazgos de Macie")
        return internal_error()

    return build_response(200, result)


def _account_id_from_bucket(bucket: str) -> str:
    """Deriva el account id del nombre del bucket (datamask-{env}-documents-{id})."""
    parts = bucket.rsplit("-", 1)
    return parts[1] if len(parts) == 2 and parts[1].isdigit() else ""


def handle_create_macie_jobs(event: dict[str, Any]) -> dict[str, Any]:
    """Crea los dos jobs de Macie (originales y ofuscados) del usuario.

    Para la sección Configuración. Si Macie no está habilitado, responde con
    macieEnabled=false y un mensaje explicativo (no es un error).
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    config = get_config()
    account_id = _account_id_from_bucket(config.documents_bucket)
    if not account_id:
        return internal_error()

    try:
        result = macie_jobs.create_scan_jobs(
            user_id=user_id,
            bucket=config.documents_bucket,
            account_id=account_id,
        )
    except Exception:
        logger.exception("Error creando jobs de Macie")
        return internal_error()

    # Notificar por SNS si el usuario tiene las alertas habilitadas.
    if result.get("macieEnabled"):
        dynamodb = boto3.resource("dynamodb")
        det = detection_config.get_detection_config(
            user_id=user_id,
            table_name=config.documents_table,
            dynamodb_resource=dynamodb,
        )
        created = [j for j in result.get("jobs", []) if j["status"] == "CREATED"]
        if created:
            notifications.publish_alert(
                subject=f"DataMask: {len(created)} job(s) de Macie iniciado(s)",
                message=(
                    f"El usuario {user_id} inició {len(created)} job(s) de "
                    "Amazon Macie para escanear PII:\n"
                    + "\n".join(
                        f"- [{j['scope']}] {j['prefix']} (jobId: {j['jobId']})"
                        for j in created
                    )
                ),
                enabled=bool(det.get("snsAlertsEnabled", True)),
            )

    return build_response(200, result)


def handle_update_detection_config(event: dict[str, Any]) -> dict[str, Any]:
    """Actualiza la configuración avanzada de detección del usuario."""
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    body = _parse_body(event)
    if not body:
        return bad_request("Se requiere un body con la configuración")

    # Tomar solo los campos esperados (ignorar el resto).
    defaults = detection_config.default_detection_config()
    new_config = {
        "detectionMethod": body.get(
            "detectionMethod", defaults["detectionMethod"]
        ),
        "macieVerification": body.get(
            "macieVerification", defaults["macieVerification"]
        ),
        "snsAlertsEnabled": body.get(
            "snsAlertsEnabled", defaults["snsAlertsEnabled"]
        ),
        "bedrockModelId": body.get("bedrockModelId", defaults["bedrockModelId"]),
        "bedrockTemperature": body.get(
            "bedrockTemperature", defaults["bedrockTemperature"]
        ),
        "bedrockPrompt": body.get("bedrockPrompt", defaults["bedrockPrompt"]),
        "regexRules": body.get("regexRules", defaults["regexRules"]),
        "ignoreEntities": body.get("ignoreEntities", defaults["ignoreEntities"]),
    }

    valid, message = detection_config.validate_detection_config(new_config)
    if not valid:
        return bad_request(message)

    config = get_config()
    dynamodb = boto3.resource("dynamodb")
    success = detection_config.save_detection_config(
        user_id=user_id,
        config=new_config,
        table_name=config.documents_table,
        dynamodb_resource=dynamodb,
    )
    if not success:
        return build_response(500, {
            "message": "La configuración no pudo ser guardada"
        })

    return build_response(200, {
        "message": "Configuración de detección actualizada",
        "config": new_config,
    })


def handle_get_models(event: dict[str, Any]) -> dict[str, Any]:
    """Devuelve los modelos de Bedrock disponibles para selección."""
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()
    return build_response(200, {
        "models": detection_config.list_available_models(),
    })


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """
    Parsea el body JSON del evento API Gateway.

    Maneja bodies nulos, strings vacíos, y JSON inválido.

    Args:
        event: Evento de API Gateway.

    Returns:
        Diccionario parseado del body, o dict vacío si falla.
    """
    body = event.get("body")
    if not body:
        return {}

    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {}


_USER_SCOPED_ROOTS = ("originales/", "ofuscados/", "procesamiento/", "errores/")


def _scope_prefix_to_user(prefix: str, user_id: str) -> str | None:
    """Restringe un prefijo S3 a la carpeta del usuario actual.

    - "" o "originales/" -> "originales/{userId}/"
    - "originales/{userId}/..." -> se permite tal cual
    - "originales/{otro}/..." -> None (acceso denegado)

    Returns:
        El prefijo acotado al usuario, o None si intenta acceder a otro usuario.
    """
    safe_prefix = prefix.lstrip("/")

    # Prefijo vacío: por defecto, los originales del usuario.
    if not safe_prefix:
        return f"originales/{user_id}/"

    for root in _USER_SCOPED_ROOTS:
        if safe_prefix == root:
            return f"{root}{user_id}/"
        if safe_prefix.startswith(root):
            remainder = safe_prefix[len(root):]
            owner = remainder.split("/", 1)[0]
            if owner == user_id:
                return safe_prefix
            return None

    # Prefijo desconocido: acotar a los originales del usuario por seguridad.
    return f"originales/{user_id}/"


def handle_s3_list(event: dict[str, Any]) -> dict[str, Any]:
    """
    Lista objetos en el bucket S3 de documentos.

    Permite navegar las carpetas del bucket (originales/, ofuscados/,
    procesamiento/, errores/) y ver los archivos dentro de cada una.

    Query params:
        prefix: Prefijo S3 para listar (default: 'originales/')

    Returns:
        Respuesta 200 con bucket name, prefix, objetos y carpetas.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    # Obtener prefix del query string
    params = event.get("queryStringParameters") or {}
    prefix = params.get("prefix", "originales/")

    # Sanitizar prefix — no permitir navegar fuera del bucket
    if ".." in prefix:
        return bad_request("Prefijo inválido")

    # Aislamiento por usuario: forzar que el listado quede dentro de la
    # carpeta del usuario actual (originales/{userId}/, ofuscados/{userId}/,
    # etc.). Si el prefijo es una raíz conocida, se le anexa el userId; si ya
    # incluye un userId distinto, se rechaza.
    prefix = _scope_prefix_to_user(prefix, user_id)
    if prefix is None:
        return forbidden()

    config = get_config()
    s3_client = get_s3_client()

    try:
        # Listado recursivo (sin Delimiter) para mostrar todos los archivos
        # bajo el prefijo, sin importar el anidamiento por usuario/documento.
        paginator = s3_client.get_paginator("list_objects_v2")
        page_iterator = paginator.paginate(
            Bucket=config.documents_bucket,
            Prefix=prefix,
            PaginationConfig={"MaxItems": 500},
        )

        objects: list[dict[str, Any]] = []
        for page in page_iterator:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Omitir "carpetas" vacías (keys que terminan en /)
                if key.endswith("/"):
                    continue
                objects.append({
                    "key": key,
                    "name": key.split("/")[-1],
                    "size": obj["Size"],
                    "lastModified": obj["LastModified"].isoformat(),
                    "isFolder": False,
                })

        # Ordenar por fecha de modificación descendente (más reciente primero)
        objects.sort(key=lambda o: o["lastModified"], reverse=True)

        return build_response(200, {
            "bucket": config.documents_bucket,
            "prefix": prefix,
            "objects": objects,
            "folders": [],
        })

    except ClientError as e:
        logger.error("Error listando S3: %s", str(e))
        return internal_error()


def handle_process_documents(event: dict[str, Any]) -> dict[str, Any]:
    """
    Inicia el procesamiento (ofuscación) de uno o más documentos.

    Recibe una lista de keys S3 (bajo originales/) y, por cada una,
    invoca la Lambda Trigger con un evento S3 sintético para arrancar
    el pipeline de detección y redacción.

    Body:
        keys: lista de keys S3 a procesar
              (ej: ["originales/{userId}/{docId}/archivo.pdf"])

    Returns:
        Respuesta 200 con la cantidad de documentos encolados.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    body = _parse_body(event)
    keys = body.get("keys") or []

    if not isinstance(keys, list) or len(keys) == 0:
        return bad_request("Se requiere una lista de 'keys' a procesar")

    config = get_config()
    trigger_function_name = os.environ.get("TRIGGER_FUNCTION_NAME", "")
    if not trigger_function_name:
        logger.error("TRIGGER_FUNCTION_NAME no configurado")
        return internal_error()

    lambda_client = boto3.client("lambda")
    s3_client = get_s3_client()

    queued: list[str] = []
    errors: list[dict[str, str]] = []

    for key in keys:
        # Sanitizar: solo permitir keys bajo originales/ del propio usuario
        if not isinstance(key, str) or not key.startswith("originales/"):
            errors.append({
                "key": str(key),
                "reason": "Solo se pueden procesar documentos de la carpeta 'originales/'.",
            })
            continue
        if f"originales/{user_id}/" not in key:
            # El usuario solo puede procesar sus propios documentos
            errors.append({
                "key": key,
                "reason": "El documento no pertenece al usuario actual.",
            })
            continue

        try:
            # Obtener tamaño real del objeto para el evento sintético
            head = s3_client.head_object(
                Bucket=config.documents_bucket, Key=key
            )
            object_size = head.get("ContentLength", 0)

            # Construir evento S3 sintético compatible con la Lambda Trigger
            synthetic_event = {
                "Records": [
                    {
                        "eventSource": "aws:s3",
                        "eventName": "ObjectCreated:Put",
                        "s3": {
                            "bucket": {"name": config.documents_bucket},
                            "object": {
                                "key": key,
                                "size": object_size,
                            },
                        },
                    }
                ]
            }

            lambda_client.invoke(
                FunctionName=trigger_function_name,
                InvocationType="Event",  # Asíncrono
                Payload=json.dumps(synthetic_event).encode("utf-8"),
            )
            queued.append(key)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                reason = "El archivo ya no existe en el bucket."
            else:
                reason = "No se pudo iniciar el procesamiento del documento."
            logger.error("Error encolando %s: %s", key, str(e))
            errors.append({"key": key, "reason": reason})

    return build_response(200, {
        "message": f"{len(queued)} documento(s) encolado(s) para procesamiento",
        "queued": queued,
        "errors": errors,
    })


def handle_s3_download(event: dict[str, Any]) -> dict[str, Any]:
    """
    Genera una presigned URL de descarga para una key S3 específica.

    Permite descargar cualquier objeto del bucket de documentos siempre
    que pertenezca al usuario autenticado (la key debe contener su userId).

    Query params:
        key: Key S3 del objeto a descargar.

    Returns:
        Respuesta 200 con la URL de descarga (expiración 5 min).
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    params = event.get("queryStringParameters") or {}
    s3_key = params.get("key", "")

    if not s3_key or ".." in s3_key:
        return bad_request("Key inválida")

    # El usuario solo puede descargar sus propios objetos
    if user_id not in s3_key:
        return forbidden()

    config = get_config()
    s3_client = get_s3_client()

    try:
        # Verificar que el objeto existe
        s3_client.head_object(Bucket=config.documents_bucket, Key=s3_key)
    except ClientError:
        return build_response(404, {
            "message": "El recurso solicitado no está disponible"
        })

    try:
        download_url = s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": config.documents_bucket,
                "Key": s3_key,
            },
            ExpiresIn=DOWNLOAD_URL_EXPIRATION_SECONDS,
        )
    except ClientError:
        logger.exception("Error generando presigned URL de descarga")
        return internal_error()

    return build_response(200, {
        "url": download_url,
        "key": s3_key,
    })


def handle_s3_delete(event: dict[str, Any]) -> dict[str, Any]:
    """
    Elimina un objeto del bucket S3 de documentos.

    Usado para borrar archivos ofuscados (PDF o Markdown) desde la
    sección "Archivos ofuscados". El usuario solo puede borrar objetos
    que le pertenecen (la key debe contener su userId).

    Query params:
        key: Key S3 del objeto a eliminar.

    Returns:
        Respuesta 200 al eliminar, o error.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    params = event.get("queryStringParameters") or {}
    s3_key = params.get("key", "")

    if not s3_key or ".." in s3_key:
        return bad_request("Key inválida")

    # El usuario solo puede borrar sus propios objetos
    if user_id not in s3_key:
        return forbidden()

    config = get_config()
    s3_client = get_s3_client()

    try:
        s3_client.delete_object(Bucket=config.documents_bucket, Key=s3_key)
    except ClientError:
        logger.exception("Error eliminando objeto S3")
        return internal_error()

    return build_response(200, {"message": "Archivo eliminado", "key": s3_key})


def _delete_prefix(s3_client: Any, bucket: str, prefix: str) -> int:
    """Borra todos los objetos bajo un prefijo. Devuelve la cantidad borrada."""
    deleted = 0
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if not keys:
            continue
        # delete_objects acepta hasta 1000 keys por llamada.
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            s3_client.delete_objects(
                Bucket=bucket, Delete={"Objects": batch, "Quiet": True}
            )
            deleted += len(batch)
    return deleted


def handle_purge_processed(event: dict[str, Any]) -> dict[str, Any]:
    """
    Borra archivos del usuario según el alcance solicitado.

    Body opcional:
        scope: "obfuscated" (ofuscados + procesamiento),
               "originals"  (solo originales),
               "all"        (todo lo anterior). Default: "obfuscated".

    NO toca el registro de auditoría (DynamoDB). Acción destructiva
    confirmada en el frontend.

    Returns:
        Respuesta 200 con la cantidad de objetos eliminados por carpeta.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    body = _parse_body(event)
    scope = body.get("scope", "obfuscated")
    if scope not in ("obfuscated", "originals", "all"):
        return bad_request("scope inválido")

    config = get_config()
    s3_client = get_s3_client()
    bucket = config.documents_bucket

    result: dict[str, Any] = {"message": "Archivos eliminados"}
    try:
        if scope in ("obfuscated", "all"):
            result["deletedProcessing"] = _delete_prefix(
                s3_client, bucket, f"procesamiento/{user_id}/"
            )
            result["deletedObfuscated"] = _delete_prefix(
                s3_client, bucket, f"ofuscados/{user_id}/"
            )
        if scope in ("originals", "all"):
            result["deletedOriginals"] = _delete_prefix(
                s3_client, bucket, f"originales/{user_id}/"
            )
    except ClientError:
        logger.exception("Error purgando archivos (scope=%s)", scope)
        return internal_error()

    return build_response(200, result)


def handle_purge_audit(event: dict[str, Any]) -> dict[str, Any]:
    """
    Borra todo el registro de auditoría del usuario (items DOC# en DynamoDB).

    Elimina los registros de documentos del usuario en DynamoDB. NO borra
    objetos S3 (originales/ofuscados quedan) ni la configuración de detección.
    Acción destructiva confirmada en el frontend.

    Returns:
        Respuesta 200 con la cantidad de registros eliminados.
    """
    user_id = get_user_id(event)
    if not user_id:
        return forbidden()

    table = get_dynamodb_table()

    try:
        # Consultar todos los documentos del usuario (GSI1).
        deleted = 0
        query_kwargs: dict[str, Any] = {
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :pk",
            "ExpressionAttributeValues": {":pk": f"USER#{user_id}"},
        }
        while True:
            response = table.query(**query_kwargs)
            items = response.get("Items", [])
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(
                        Key={"PK": item["PK"], "SK": item["SK"]}
                    )
                    deleted += 1
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key
    except ClientError:
        logger.exception("Error purgando registro de auditoría")
        return internal_error()

    return build_response(200, {
        "message": "Registro de auditoría eliminado",
        "deletedRecords": deleted,
    })
