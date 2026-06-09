"""
Generación de salida y almacenamiento para el pipeline de redacción.

Responsabilidades:
- Generar nombres de archivo de salida ({stem}_ofuscado.pdf, {stem}_informe.md)
- Generar informe Markdown con texto ofuscado por página
- Subir resultados (PDF + Markdown) a S3 en ofuscados/{userId}/{docId}/
- Actualizar DynamoDB con status=COMPLETED y estadísticas

Requirements: 8.4, 8.5, 8.6, 8.9
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_output_filename(
    original_filename: str,
    output_type: str,
) -> str:
    """Genera el nombre de archivo de salida según el tipo.

    El stem es el nombre original sin extensión. Se genera:
    - Para PDF: {stem}_ofuscado.pdf
    - Para Markdown: {stem}_informe.md

    Args:
        original_filename: Nombre del archivo original (ej. "documento.pdf").
        output_type: Tipo de salida ("pdf" o "markdown").

    Returns:
        Nombre de archivo generado.

    Raises:
        ValueError: Si output_type no es válido.
    """
    stem = Path(original_filename).stem

    if output_type == "pdf":
        return f"{stem}_ofuscado.pdf"
    elif output_type == "markdown":
        return f"{stem}_informe.md"
    else:
        raise ValueError(
            f"output_type debe ser 'pdf' o 'markdown', recibido: {output_type}"
        )


def generate_markdown_report(
    original_filename: str,
    entities: list[dict],
    page_texts: list[dict],
) -> str:
    """Genera el informe Markdown con texto ofuscado por página.

    El informe sigue el formato:
    - Título con nombre del archivo original
    - Metadato con nombre del archivo original
    - Separador visual
    - Texto ofuscado organizado por número de página

    El texto de cada página tiene las entidades PII reemplazadas
    por etiquetas [TIPO].

    Args:
        original_filename: Nombre del archivo original.
        entities: Lista de entidades detectadas con campos:
            text, type, page, startOffset, endOffset.
        page_texts: Lista de dicts con campos:
            page_number (int) y text (str) — texto extraído por página.

    Returns:
        Contenido del informe Markdown como string.
    """
    lines: list[str] = []

    # Título
    lines.append(f"# Informe de Ofuscación: {original_filename}")
    lines.append("")

    # Metadatos
    lines.append(f"**Archivo original:** {original_filename}")
    lines.append("")

    # Separador visual
    lines.append("---")
    lines.append("")

    # Ordenar page_texts por número de página
    sorted_pages = sorted(page_texts, key=lambda p: p.get("page_number", 0))

    for page_info in sorted_pages:
        page_number = page_info.get("page_number", 1)
        page_text = page_info.get("text", "")

        lines.append(f"## Página {page_number}")
        lines.append("")

        # Obtener entidades de esta página y aplicar reemplazo
        page_entities = [
            e for e in entities
            if e.get("page") == page_number
        ]

        redacted_text = _apply_redactions_to_text(page_text, page_entities)
        lines.append(redacted_text)
        lines.append("")

    return "\n".join(lines)


def _apply_redactions_to_text(
    text: str,
    entities: list[dict],
) -> str:
    """Aplica redacciones al texto reemplazando PII por [TIPO].

    Procesa las entidades de mayor offset a menor para no alterar
    las posiciones de las entidades anteriores.

    Args:
        text: Texto original de la página.
        entities: Entidades de esta página con startOffset, endOffset, type.

    Returns:
        Texto con PII reemplazado por etiquetas [TIPO].
    """
    if not entities:
        return text

    # Ordenar de mayor a menor offset para reemplazar sin afectar posiciones
    sorted_entities = sorted(
        entities,
        key=lambda e: e.get("startOffset", 0),
        reverse=True,
    )

    result = text
    for entity in sorted_entities:
        start = entity.get("startOffset", 0)
        end = entity.get("endOffset", 0)
        entity_type = entity.get("type", "PII")
        label = _entity_label(entity_type, entity.get("source", ""))

        if start < 0 or end <= start or end > len(result):
            # Posiciones inválidas — intentar búsqueda por texto
            entity_text = entity.get("text", "")
            if entity_text and entity_text in result:
                result = result.replace(entity_text, label, 1)
            continue

        result = result[:start] + label + result[end:]

    return result


# Sufijo de etiqueta según el motor que detectó (ganó) la entidad.
_SOURCE_SUFFIX_MAP: dict[str, str] = {
    "bedrock": "IA",
    "comprehend": "C",
    "regex": "R",
}


def _entity_label(entity_type: str, source: str) -> str:
    """Construye la etiqueta con el sufijo del motor (ej: [DNI-IA])."""
    suffix = _SOURCE_SUFFIX_MAP.get(source, "")
    return f"[{entity_type}-{suffix}]" if suffix else f"[{entity_type}]"


def upload_results_to_s3(
    bucket: str,
    user_id: str,
    doc_id: str,
    redacted_pdf_bytes: bytes,
    markdown_content: str,
    original_filename: str,
    s3_client: Any,
) -> dict[str, str]:
    """Sube el PDF ofuscado y el informe Markdown a S3.

    Los archivos se almacenan en:
    - ofuscados/{userId}/{docId}/{stem}_ofuscado.pdf
    - ofuscados/{userId}/{docId}/{stem}_informe.md

    Args:
        bucket: Nombre del bucket S3.
        user_id: ID del usuario propietario.
        doc_id: ID del documento.
        redacted_pdf_bytes: Bytes del PDF redactado.
        markdown_content: Contenido del informe Markdown.
        original_filename: Nombre del archivo original.
        s3_client: Cliente boto3 S3.

    Returns:
        Dict con s3_key_redacted y s3_key_markdown.

    Raises:
        RuntimeError: Si la subida falla.
    """
    pdf_filename = generate_output_filename(original_filename, "pdf")
    md_filename = generate_output_filename(original_filename, "markdown")

    s3_key_redacted = f"ofuscados/{user_id}/{doc_id}/{pdf_filename}"
    s3_key_markdown = f"ofuscados/{user_id}/{doc_id}/{md_filename}"

    try:
        # Subir PDF ofuscado
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key_redacted,
            Body=redacted_pdf_bytes,
            ContentType="application/pdf",
        )
        logger.info("PDF ofuscado subido a s3://%s/%s", bucket, s3_key_redacted)

        # Subir informe Markdown
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key_markdown,
            Body=markdown_content.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        logger.info(
            "Informe Markdown subido a s3://%s/%s", bucket, s3_key_markdown
        )

    except Exception as e:
        raise RuntimeError(
            f"Error subiendo resultados a S3: {e}"
        ) from e

    return {
        "s3_key_redacted": s3_key_redacted,
        "s3_key_markdown": s3_key_markdown,
    }


def update_document_completed(
    table_name: str,
    user_id: str,
    doc_id: str,
    stats: dict[str, Any],
    dynamodb_resource: Any,
) -> bool:
    """Actualiza el documento en DynamoDB con status=COMPLETED.

    Registra:
    - status = COMPLETED
    - s3KeyRedacted, s3KeyMarkdown
    - entitiesFound, entitiesByType, processingTimeMs
    - completedAt (ISO 8601)

    Args:
        table_name: Nombre de la tabla DynamoDB.
        user_id: ID del usuario propietario.
        doc_id: ID del documento.
        stats: Dict con campos:
            - s3_key_redacted (str)
            - s3_key_markdown (str)
            - entities_found (int)
            - entities_by_type (dict[str, int])
            - processing_time_ms (int)
        dynamodb_resource: Resource boto3 DynamoDB.

    Returns:
        True si la actualización fue exitosa, False en caso contrario.
    """
    try:
        table = dynamodb_resource.Table(table_name)
        completed_at = datetime.now(timezone.utc).isoformat()

        table.update_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{doc_id}",
            },
            UpdateExpression=(
                "SET #status = :status, "
                "s3KeyRedacted = :s3_redacted, "
                "s3KeyMarkdown = :s3_markdown, "
                "entitiesFound = :entities_found, "
                "entitiesByType = :entities_by_type, "
                "processingTimeMs = :processing_time_ms, "
                "#engine = :engine, "
                "completedAt = :completed_at"
            ),
            ExpressionAttributeNames={
                "#status": "status",
                "#engine": "engine",
            },
            ExpressionAttributeValues={
                ":status": "COMPLETED",
                ":s3_redacted": stats.get("s3_key_redacted", ""),
                ":s3_markdown": stats.get("s3_key_markdown", ""),
                ":entities_found": stats.get("entities_found", 0),
                ":entities_by_type": stats.get("entities_by_type", {}),
                ":processing_time_ms": stats.get("processing_time_ms", 0),
                ":engine": stats.get("engine", ""),
                ":completed_at": completed_at,
            },
        )

        logger.info(
            "DynamoDB actualizado: document=%s, status=COMPLETED, "
            "entities=%d, time=%dms",
            doc_id,
            stats.get("entities_found", 0),
            stats.get("processing_time_ms", 0),
        )
        return True

    except Exception as e:
        logger.error(
            "Error actualizando DynamoDB para document=%s: %s",
            doc_id,
            str(e),
            exc_info=True,
        )
        return False


def update_document_failed(
    table_name: str,
    user_id: str,
    doc_id: str,
    error_message: str,
    processing_time_ms: int,
    dynamodb_resource: Any,
) -> bool:
    """Marca el documento como FAILED en DynamoDB.

    Evita que un documento quede "En Proceso" indefinidamente si la
    redacción falla. Registra el mensaje de error y el paso (REDACTION).

    Args:
        table_name: Nombre de la tabla DynamoDB.
        user_id: ID del usuario propietario.
        doc_id: ID del documento.
        error_message: Mensaje de error a registrar.
        processing_time_ms: Tiempo transcurrido hasta el error.
        dynamodb_resource: Resource boto3 DynamoDB.

    Returns:
        True si la actualización fue exitosa.
    """
    try:
        table = dynamodb_resource.Table(table_name)
        completed_at = datetime.now(timezone.utc).isoformat()
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"},
            UpdateExpression=(
                "SET #status = :status, "
                "errorMessage = :error_message, "
                "errorStep = :error_step, "
                "processingTimeMs = :processing_time_ms, "
                "completedAt = :completed_at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":error_message": error_message[:1000],
                ":error_step": "REDACTION",
                ":processing_time_ms": processing_time_ms,
                ":completed_at": completed_at,
            },
        )
        logger.info("DynamoDB actualizado: document=%s, status=FAILED", doc_id)
        return True
    except Exception as e:
        logger.error(
            "Error marcando FAILED para document=%s: %s",
            doc_id,
            str(e),
            exc_info=True,
        )
        return False
