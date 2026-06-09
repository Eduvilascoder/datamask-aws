"""
Lambda Redacción — PDF Redaction Engine.

Aplica redacciones al PDF original usando PyMuPDF (Lambda Layer).
Genera documento ofuscado con etiquetas [TIPO] y colores por tipo.
Genera informe Markdown con texto ofuscado por página.

Responsabilidades:
- Descargar PDF original desde S3
- Leer entidades detectadas desde S3
- Aplicar redacciones con PyMuPDF (preservar estructura, guardado incremental)
- Manejar PDFs protegidos con contraseña o corruptos
- Omitir generación si entities_found=0
- Retornar resultado con estadísticas o error

Runtime: Python 3.12
Timeout: 300s
Memoria: 1024MB
Layer: PyMuPDF compilado para Amazon Linux 2023
"""

import json
import logging
import os
import time
from typing import Any

import boto3

from output_generator import (
    generate_markdown_report,
    update_document_completed,
    update_document_failed,
    upload_results_to_s3,
)
from config_filter import (
    filter_entities_by_config,
    get_active_types,
)
from pdf_redactor import redact_pdf

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Variables de entorno
DOCUMENTS_BUCKET = os.environ.get("DOCUMENTS_BUCKET", "")
DOCUMENTS_TABLE = os.environ.get("DOCUMENTS_TABLE", "")
DLQ_URL = os.environ.get("DLQ_URL", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

# Clientes AWS
s3_client = boto3.client("s3")
dynamodb_resource = boto3.resource("dynamodb")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handler principal para redacción de PDF.

    Recibe el evento con referencia al PDF y entidades detectadas,
    aplica redacciones y retorna el resultado.

    Args:
        event: Evento con campos:
            - document_id: ID del documento.
            - user_id: ID del usuario propietario.
            - s3_key_original: Clave S3 del PDF original.
            - s3_key_entities: Clave S3 del JSON de entidades.
            - entities_found: Cantidad de entidades detectadas.
        context: Contexto de ejecución Lambda.

    Returns:
        Diccionario con resultado de la redacción.
    """
    logger.info(
        "Evento de redacción recibido: %s",
        json.dumps(event, default=str),
    )
    start_time = time.time()

    try:
        document_id = event.get("document_id", "")
        user_id = event.get("user_id", "")
        s3_key_original = event.get("s3_key_original", "")
        s3_key_entities = event.get("s3_key_entities", "")
        entities_found = event.get("entities_found", 0)
        original_filename = event.get("original_filename", "documento.pdf")
        engine = event.get("engine", "")

        logger.info(
            "Iniciando redacción: document_id=%s, user_id=%s, entities=%d",
            document_id,
            user_id,
            entities_found,
        )

        # Req 8.2: Si 0 entidades, omitir generación
        if entities_found == 0:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info("0 entidades detectadas — omitiendo generación")
            # Actualizar DynamoDB con 0 entidades
            update_document_completed(
                table_name=DOCUMENTS_TABLE,
                user_id=user_id,
                doc_id=document_id,
                stats={
                    "s3_key_redacted": "",
                    "s3_key_markdown": "",
                    "entities_found": 0,
                    "entities_by_type": {},
                    "processing_time_ms": elapsed_ms,
                    "engine": engine,
                },
                dynamodb_resource=dynamodb_resource,
            )
            return _build_response(
                document_id=document_id,
                success=True,
                message="Sin entidades detectadas — generación omitida",
                entities_redacted=0,
                entities_by_type={},
                processing_time_ms=elapsed_ms,
            )

        # Descargar PDF original desde S3
        pdf_bytes = _download_from_s3(s3_key_original)

        # Leer entidades desde S3
        entities = _load_entities(s3_key_entities)

        # Req 12.4: Filtrar entidades según configuración del usuario
        active_types = get_active_types(
            user_id=user_id,
            table_name=DOCUMENTS_TABLE,
        )
        entities = filter_entities_by_config(entities, active_types)

        logger.info(
            "Entidades tras filtrado por configuración: %d (tipos activos: %d)",
            len(entities),
            len(active_types),
        )

        # Si tras filtrar no quedan entidades, omitir generación
        if not entities:
            logger.info(
                "0 entidades tras filtrado por config — omitiendo generación"
            )
            return _build_response(
                document_id=document_id,
                success=True,
                message="Sin entidades activas en configuración — "
                "generación omitida",
                entities_redacted=0,
                entities_by_type={},
                processing_time_ms=int((time.time() - start_time) * 1000),
            )

        # Cargar la geometría de Textract (bounding boxes por palabra/línea).
        # Es la fuente de verdad para texto que vive en imágenes del PDF y NO
        # está en la capa de texto (p.ej. el nombre del encabezado de un CV).
        textract_geometry = _load_textract_geometry(s3_key_entities)

        # Aplicar redacciones
        result = redact_pdf(
            pdf_bytes=pdf_bytes,
            entities=entities,
            textract_pages=textract_geometry,
        )

        if not result.success:
            return _build_response(
                document_id=document_id,
                success=False,
                error_code=result.error_code,
                error_message=result.error_message,
                processing_time_ms=result.processing_time_ms,
            )

        # Si el redactor retornó sin bytes (0 entidades encontradas en PDF)
        if result.redacted_pdf_bytes is None:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return _build_response(
                document_id=document_id,
                success=True,
                message="Sin entidades para redactar en PDF",
                entities_redacted=0,
                entities_by_type={},
                processing_time_ms=elapsed_ms,
            )

        # --- Generación de salida y almacenamiento (Req 8.4-8.6, 8.9) ---

        # Obtener textos por página para el informe markdown
        page_texts = _extract_page_texts(s3_key_entities)

        # Generar informe Markdown
        markdown_content = generate_markdown_report(
            original_filename=original_filename,
            entities=entities,
            page_texts=page_texts,
        )

        # Subir PDF ofuscado e informe Markdown a S3
        s3_keys = upload_results_to_s3(
            bucket=DOCUMENTS_BUCKET,
            user_id=user_id,
            doc_id=document_id,
            redacted_pdf_bytes=result.redacted_pdf_bytes,
            markdown_content=markdown_content,
            original_filename=original_filename,
            s3_client=s3_client,
        )

        # Confirmar que el PDF ofuscado quedó efectivamente escrito en el
        # bucket destino ANTES de marcar el documento como COMPLETED. Así el
        # frontend no declara "terminado" hasta que el archivo está disponible.
        _confirm_object_exists(s3_keys["s3_key_redacted"])

        elapsed_ms = int((time.time() - start_time) * 1000)

        # Actualizar DynamoDB con status=COMPLETED y estadísticas
        update_document_completed(
            table_name=DOCUMENTS_TABLE,
            user_id=user_id,
            doc_id=document_id,
            stats={
                "s3_key_redacted": s3_keys["s3_key_redacted"],
                "s3_key_markdown": s3_keys["s3_key_markdown"],
                "entities_found": result.entities_redacted,
                "entities_by_type": result.entities_by_type,
                "processing_time_ms": elapsed_ms,
                "engine": engine,
            },
            dynamodb_resource=dynamodb_resource,
        )

        return _build_response(
            document_id=document_id,
            success=True,
            message="Redacción completada",
            entities_redacted=result.entities_redacted,
            entities_by_type=result.entities_by_type,
            processing_time_ms=elapsed_ms,
        )

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        error_msg = f"Error inesperado: {str(e)}"
        logger.error(error_msg, exc_info=True)
        # Marcar el documento como FAILED para que no quede "En Proceso".
        try:
            update_document_failed(
                table_name=DOCUMENTS_TABLE,
                user_id=event.get("user_id", ""),
                doc_id=event.get("document_id", ""),
                error_message=error_msg,
                processing_time_ms=elapsed_ms,
                dynamodb_resource=dynamodb_resource,
            )
        except Exception:
            logger.error("No se pudo marcar FAILED", exc_info=True)
        return _build_response(
            document_id=event.get("document_id", ""),
            success=False,
            error_message=error_msg,
            processing_time_ms=elapsed_ms,
        )


def _confirm_object_exists(s3_key: str, retries: int = 3) -> None:
    """Verifica que un objeto exista en S3 tras escribirlo.

    Evita marcar el documento como COMPLETED sin que el archivo ofuscado
    esté realmente disponible en el bucket destino. Reintenta brevemente
    para cubrir sobrescrituras.

    Args:
        s3_key: Clave del objeto a verificar.
        retries: Cantidad de intentos.

    Raises:
        RuntimeError: Si el objeto no aparece tras los reintentos.
    """
    for attempt in range(retries):
        try:
            s3_client.head_object(Bucket=DOCUMENTS_BUCKET, Key=s3_key)
            return
        except Exception:
            if attempt == retries - 1:
                raise RuntimeError(
                    f"El documento ofuscado no se encontró en S3: {s3_key}"
                )
            time.sleep(0.5)


def _download_from_s3(s3_key: str) -> bytes:
    """Descarga un archivo desde S3.

    Args:
        s3_key: Clave del objeto en S3.

    Returns:
        Bytes del archivo descargado.

    Raises:
        RuntimeError: Si la descarga falla.
    """
    try:
        response = s3_client.get_object(
            Bucket=DOCUMENTS_BUCKET,
            Key=s3_key,
        )
        return response["Body"].read()
    except Exception as e:
        raise RuntimeError(
            f"Error descargando desde S3 ({s3_key}): {e}"
        ) from e


def _load_entities(s3_key: str) -> list[dict]:
    """Carga la lista de entidades desde el JSON en S3.

    Args:
        s3_key: Clave S3 del archivo entities.json.

    Returns:
        Lista de entidades como dicts.

    Raises:
        RuntimeError: Si la carga falla.
    """
    try:
        response = s3_client.get_object(
            Bucket=DOCUMENTS_BUCKET,
            Key=s3_key,
        )
        data = json.loads(response["Body"].read().decode("utf-8"))
        return data.get("entities", [])
    except Exception as e:
        raise RuntimeError(
            f"Error cargando entidades desde S3 ({s3_key}): {e}"
        ) from e


def _load_textract_geometry(s3_key_entities: str) -> list[dict]:
    """Carga la geometría de Textract (bounding boxes) desde S3.

    Busca el textract_output.json en el mismo prefijo que el entities.json.
    Devuelve la lista de páginas con sus bloques (cada uno con boundingBox y
    words), que el redactor usa como fallback para localizar texto que está
    en imágenes y no en la capa de texto del PDF.

    Args:
        s3_key_entities: Clave S3 del archivo entities.json.

    Returns:
        Lista de páginas de Textract, o lista vacía si no se puede cargar.
    """
    prefix = "/".join(s3_key_entities.split("/")[:-1])
    textract_key = f"{prefix}/textract_output.json"
    try:
        response = s3_client.get_object(
            Bucket=DOCUMENTS_BUCKET, Key=textract_key
        )
        data = json.loads(response["Body"].read().decode("utf-8"))
        return data.get("pages", [])
    except Exception:
        logger.warning(
            "No se pudo cargar geometría de Textract desde %s", textract_key
        )
        return []


def _extract_page_texts(s3_key_entities: str) -> list[dict]:
    """Extrae los textos por página del JSON de entidades o Textract output.

    Busca el campo page_texts en el JSON de entidades. Si no existe,
    intenta construirlo desde el textract_output.json en el mismo prefijo.

    Args:
        s3_key_entities: Clave S3 del archivo entities.json.

    Returns:
        Lista de dicts con page_number y text por cada página.
    """
    try:
        response = s3_client.get_object(
            Bucket=DOCUMENTS_BUCKET,
            Key=s3_key_entities,
        )
        data = json.loads(response["Body"].read().decode("utf-8"))

        # Intentar obtener page_texts directamente del JSON
        page_texts = data.get("page_texts", [])
        if page_texts:
            return page_texts

        # Intentar cargar desde textract_output.json en el mismo directorio
        prefix = "/".join(s3_key_entities.split("/")[:-1])
        textract_key = f"{prefix}/textract_output.json"

        try:
            textract_response = s3_client.get_object(
                Bucket=DOCUMENTS_BUCKET,
                Key=textract_key,
            )
            textract_data = json.loads(
                textract_response["Body"].read().decode("utf-8")
            )
            pages = textract_data.get("pages", [])
            return [
                {
                    "page_number": page.get("pageNumber", i + 1),
                    "text": _reconstruct_page_text(page),
                }
                for i, page in enumerate(pages)
            ]
        except Exception:
            logger.warning(
                "No se pudo cargar textract_output.json desde %s",
                textract_key,
            )
            return []

    except Exception as e:
        logger.warning(
            "Error extrayendo textos por página: %s", str(e)
        )
        return []


def _reconstruct_page_text(page: dict) -> str:
    """Reconstruye el texto de una página desde los bloques de Textract.

    Args:
        page: Dict con campo 'blocks' (lista de bloques con 'text').

    Returns:
        Texto completo de la página.
    """
    blocks = page.get("blocks", [])
    lines = [
        block.get("text", "")
        for block in blocks
        if block.get("type") == "LINE"
    ]
    return "\n".join(lines)


def _build_response(
    document_id: str,
    success: bool,
    message: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    entities_redacted: int = 0,
    entities_by_type: dict[str, int] | None = None,
    processing_time_ms: int = 0,
) -> dict[str, Any]:
    """Construye la respuesta estandarizada del handler.

    Args:
        document_id: ID del documento procesado.
        success: Si la operación fue exitosa.
        message: Mensaje descriptivo (éxito).
        error_code: Código de error (PASSWORD_PROTECTED, CORRUPTED).
        error_message: Mensaje de error descriptivo.
        entities_redacted: Cantidad de entidades redactadas.
        entities_by_type: Desglose por tipo.
        processing_time_ms: Tiempo de procesamiento en ms.

    Returns:
        Diccionario con la respuesta.
    """
    body: dict[str, Any] = {
        "document_id": document_id,
        "success": success,
        "processing_time_ms": processing_time_ms,
    }

    if success:
        body["message"] = message or "OK"
        body["entities_redacted"] = entities_redacted
        body["entities_by_type"] = entities_by_type or {}
    else:
        if error_code:
            body["error_code"] = error_code
        if error_message:
            body["error_message"] = error_message

    return {
        "statusCode": 200 if success else 500,
        "body": body,
    }
