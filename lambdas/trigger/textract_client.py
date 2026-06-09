"""
Módulo de invocación a AWS Textract para extracción de texto de PDFs.

Responsabilidades:
- Invocar Textract síncrono (DetectDocumentText) para PDFs <15 páginas.
- Invocar Textract asíncrono (StartDocumentTextDetection) para PDFs ≥15 páginas.
- Reconstruir texto preservando orden de páginas, bloques y bounding boxes.
- Almacenar resultado en S3 procesamiento/{userId}/{documentId}/textract_output.json.
- Manejar errores: TEXTRACT_ERROR, TEXTRACT_TIMEOUT (300s).

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Constantes
PAGE_THRESHOLD: int = 15
ASYNC_POLL_INTERVAL_SECONDS: int = 5
# Menor que el timeout de la Lambda (300s) para permitir un error
# controlado antes de que AWS mate la ejecución.
ASYNC_TIMEOUT_SECONDS: int = 240


@dataclass
class BoundingBox:
    """Coordenadas normalizadas del bounding box (0-1)."""

    left: float
    top: float
    width: float
    height: float


@dataclass
class WordBlock:
    """Bloque de tipo WORD extraído por Textract."""

    text: str
    bounding_box: BoundingBox


@dataclass
class LineBlock:
    """Bloque de tipo LINE extraído por Textract."""

    type: str  # "LINE"
    text: str
    bounding_box: BoundingBox
    words: list[WordBlock] = field(default_factory=list)


@dataclass
class PageResult:
    """Resultado de extracción de una página."""

    page_number: int
    blocks: list[LineBlock] = field(default_factory=list)


@dataclass
class TextractResult:
    """Resultado completo de la extracción de texto con Textract."""

    document_id: str
    pages: list[PageResult] = field(default_factory=list)
    total_pages: int = 0
    extracted_at: str = ""
    error: str | None = None
    error_code: str | None = None


def extract_text(
    bucket: str,
    key: str,
    page_count: int,
    document_id: str,
    s3_client: Any,
    textract_client: Any,
) -> TextractResult:
    """
    Extrae texto de un PDF usando Textract.

    Selecciona automáticamente el modo síncrono o asíncrono según
    la cantidad de páginas del documento.

    Args:
        bucket: Nombre del bucket S3.
        key: Key del objeto PDF en S3.
        page_count: Número de páginas del PDF.
        document_id: Identificador del documento.
        s3_client: Cliente boto3 S3.
        textract_client: Cliente boto3 Textract.

    Returns:
        TextractResult con las páginas extraídas o información de error.
    """
    # Textract síncrono (DetectDocumentText con Bytes) NO soporta PDFs
    # multipágina — solo imágenes de una sola página. Para documentos PDF
    # se debe usar siempre la API asíncrona basada en S3Object
    # (StartDocumentTextDetection), independientemente del número de páginas.
    logger.info(
        "Usando Textract asíncrono (S3Object) para documento %s (%d páginas)",
        document_id,
        page_count,
    )
    return extract_text_async(
        bucket=bucket,
        key=key,
        document_id=document_id,
        textract_client=textract_client,
    )


def extract_text_sync(
    bucket: str,
    key: str,
    document_id: str,
    s3_client: Any,
    textract_client: Any,
) -> TextractResult:
    """
    Extrae texto usando Textract síncrono (DetectDocumentText).

    Lee el archivo de S3 y lo envía directamente a Textract como bytes.

    Args:
        bucket: Nombre del bucket S3.
        key: Key del objeto PDF en S3.
        document_id: Identificador del documento.
        s3_client: Cliente boto3 S3.
        textract_client: Cliente boto3 Textract.

    Returns:
        TextractResult con las páginas extraídas o información de error.
    """
    try:
        # Leer el documento de S3
        response = s3_client.get_object(Bucket=bucket, Key=key)
        document_bytes = response["Body"].read()
    except Exception as e:
        logger.error("Error leyendo PDF de S3 para Textract: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Error leyendo PDF de S3: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )

    try:
        textract_response = textract_client.detect_document_text(
            Document={"Bytes": document_bytes}
        )
    except textract_client.exceptions.UnsupportedDocumentException as e:
        logger.error("Textract no soporta el documento: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Documento no soportado por Textract: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )
    except textract_client.exceptions.InvalidParameterException as e:
        logger.error("Parámetro inválido en Textract: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Parámetro inválido en Textract: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )
    except textract_client.exceptions.BadDocumentException as e:
        logger.error("Documento corrupto o no procesable: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Documento corrupto o no procesable: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )
    except Exception as e:
        logger.error("Error invocando Textract síncrono: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Error invocando Textract: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )

    # Reconstruir texto a partir de la respuesta
    pages = _reconstruct_pages_from_blocks(textract_response.get("Blocks", []))

    return TextractResult(
        document_id=document_id,
        pages=pages,
        total_pages=len(pages),
        extracted_at=datetime.now(timezone.utc).isoformat(),
    )


def extract_text_async(
    bucket: str,
    key: str,
    document_id: str,
    textract_client: Any,
) -> TextractResult:
    """
    Extrae texto usando Textract asíncrono (StartDocumentTextDetection).

    Inicia un job asíncrono y hace polling cada 5 segundos hasta completar
    o alcanzar el timeout de 300 segundos.

    Args:
        bucket: Nombre del bucket S3.
        key: Key del objeto PDF en S3.
        document_id: Identificador del documento.
        textract_client: Cliente boto3 Textract.

    Returns:
        TextractResult con las páginas extraídas o información de error.
    """
    # Iniciar job asíncrono
    try:
        start_response = textract_client.start_document_text_detection(
            DocumentLocation={
                "S3Object": {
                    "Bucket": bucket,
                    "Name": key,
                }
            }
        )
        job_id = start_response["JobId"]
        logger.info("Job Textract asíncrono iniciado: %s", job_id)
    except textract_client.exceptions.UnsupportedDocumentException as e:
        logger.error("Textract no soporta el documento: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Documento no soportado por Textract: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )
    except textract_client.exceptions.InvalidParameterException as e:
        logger.error("Parámetro inválido en Textract: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Parámetro inválido en Textract: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )
    except textract_client.exceptions.BadDocumentException as e:
        logger.error("Documento corrupto o no procesable: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Documento corrupto o no procesable: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )
    except Exception as e:
        logger.error("Error iniciando job Textract asíncrono: %s", str(e))
        return TextractResult(
            document_id=document_id,
            error=f"Error iniciando Textract asíncrono: {str(e)}",
            error_code="TEXTRACT_ERROR",
        )

    # Polling hasta completar o timeout
    elapsed_seconds = 0
    all_blocks: list[dict[str, Any]] = []

    while elapsed_seconds < ASYNC_TIMEOUT_SECONDS:
        time.sleep(ASYNC_POLL_INTERVAL_SECONDS)
        elapsed_seconds += ASYNC_POLL_INTERVAL_SECONDS

        try:
            result_response = textract_client.get_document_text_detection(
                JobId=job_id
            )
        except Exception as e:
            logger.error("Error en polling de Textract: %s", str(e))
            return TextractResult(
                document_id=document_id,
                error=f"Error consultando estado del job Textract: {str(e)}",
                error_code="TEXTRACT_ERROR",
            )

        job_status = result_response.get("JobStatus", "")

        if job_status == "SUCCEEDED":
            all_blocks.extend(result_response.get("Blocks", []))

            # Obtener páginas adicionales si hay NextToken
            next_token = result_response.get("NextToken")
            while next_token:
                try:
                    next_response = textract_client.get_document_text_detection(
                        JobId=job_id,
                        NextToken=next_token,
                    )
                    all_blocks.extend(next_response.get("Blocks", []))
                    next_token = next_response.get("NextToken")
                except Exception as e:
                    logger.error(
                        "Error obteniendo página adicional de Textract: %s",
                        str(e),
                    )
                    break

            pages = _reconstruct_pages_from_blocks(all_blocks)
            return TextractResult(
                document_id=document_id,
                pages=pages,
                total_pages=len(pages),
                extracted_at=datetime.now(timezone.utc).isoformat(),
            )

        elif job_status == "FAILED":
            status_message = result_response.get("StatusMessage", "Desconocido")
            logger.error(
                "Job Textract %s falló: %s", job_id, status_message
            )
            return TextractResult(
                document_id=document_id,
                error=f"Job Textract falló: {status_message}",
                error_code="TEXTRACT_ERROR",
            )

        # IN_PROGRESS — continuar polling
        logger.info(
            "Job Textract %s en progreso, elapsed=%ds",
            job_id,
            elapsed_seconds,
        )

    # Timeout alcanzado
    logger.error(
        "Timeout de %ds alcanzado para job Textract %s",
        ASYNC_TIMEOUT_SECONDS,
        job_id,
    )
    return TextractResult(
        document_id=document_id,
        error=(
            f"Job Textract no completó en {ASYNC_TIMEOUT_SECONDS}s "
            f"(job_id: {job_id})"
        ),
        error_code="TEXTRACT_TIMEOUT",
    )


def store_textract_result(
    result: TextractResult,
    bucket: str,
    user_id: str,
    document_id: str,
    s3_client: Any,
) -> bool:
    """
    Almacena el resultado de Textract como JSON en S3.

    Guarda en: procesamiento/{userId}/{documentId}/textract_output.json

    Args:
        result: Resultado de la extracción de Textract.
        bucket: Nombre del bucket S3.
        user_id: Identificador del usuario.
        document_id: Identificador del documento.
        s3_client: Cliente boto3 S3.

    Returns:
        True si se almacenó correctamente, False en caso de error.
    """
    output_key = (
        f"procesamiento/{user_id}/{document_id}/textract_output.json"
    )

    try:
        json_content = json.dumps(
            _textract_result_to_dict(result),
            ensure_ascii=False,
            indent=2,
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=json_content.encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Resultado Textract almacenado en s3://%s/%s", bucket, output_key)
        return True
    except Exception as e:
        logger.error(
            "Error almacenando resultado Textract en S3: %s", str(e)
        )
        return False


def _reconstruct_pages_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[PageResult]:
    """
    Reconstruye la estructura de páginas a partir de los bloques de Textract.

    Organiza los bloques por página, preservando el orden de páginas (1..N).
    Dentro de cada página ordena los bloques LINE por posición geométrica
    (top-to-bottom, left-to-right).

    Args:
        blocks: Lista de bloques devueltos por Textract.

    Returns:
        Lista de PageResult ordenada por número de página.
    """
    # Mapear bloques por ID para resolver relaciones
    block_map: dict[str, dict[str, Any]] = {}
    for block in blocks:
        block_id = block.get("Id", "")
        if block_id:
            block_map[block_id] = block

    # Agrupar LINE blocks por página
    pages_dict: dict[int, list[LineBlock]] = {}

    for block in blocks:
        block_type = block.get("BlockType", "")
        if block_type != "LINE":
            continue

        page_number = block.get("Page", 1)
        geometry = block.get("Geometry", {}).get("BoundingBox", {})

        bounding_box = BoundingBox(
            left=float(geometry.get("Left", 0.0)),
            top=float(geometry.get("Top", 0.0)),
            width=float(geometry.get("Width", 0.0)),
            height=float(geometry.get("Height", 0.0)),
        )

        # Extraer palabras del bloque LINE via relaciones CHILD
        words = _extract_words_from_line(block, block_map)

        line_block = LineBlock(
            type="LINE",
            text=block.get("Text", ""),
            bounding_box=bounding_box,
            words=words,
        )

        if page_number not in pages_dict:
            pages_dict[page_number] = []
        pages_dict[page_number].append(line_block)

    # Ordenar páginas por número y bloques dentro de cada página
    # por posición geométrica (top-to-bottom, left-to-right)
    sorted_pages: list[PageResult] = []
    for page_num in sorted(pages_dict.keys()):
        blocks_in_page = pages_dict[page_num]
        blocks_in_page.sort(
            key=lambda b: (b.bounding_box.top, b.bounding_box.left)
        )
        sorted_pages.append(
            PageResult(page_number=page_num, blocks=blocks_in_page)
        )

    return sorted_pages


def _extract_words_from_line(
    line_block: dict[str, Any],
    block_map: dict[str, dict[str, Any]],
) -> list[WordBlock]:
    """
    Extrae los bloques WORD de un bloque LINE usando relaciones CHILD.

    Args:
        line_block: Bloque de tipo LINE.
        block_map: Mapa de todos los bloques por ID.

    Returns:
        Lista de WordBlock con texto y bounding box de cada palabra.
    """
    words: list[WordBlock] = []
    relationships = line_block.get("Relationships", [])

    for relationship in relationships:
        if relationship.get("Type") != "CHILD":
            continue
        child_ids = relationship.get("Ids", [])
        for child_id in child_ids:
            child_block = block_map.get(child_id, {})
            if child_block.get("BlockType") != "WORD":
                continue

            geometry = child_block.get("Geometry", {}).get("BoundingBox", {})
            word = WordBlock(
                text=child_block.get("Text", ""),
                bounding_box=BoundingBox(
                    left=float(geometry.get("Left", 0.0)),
                    top=float(geometry.get("Top", 0.0)),
                    width=float(geometry.get("Width", 0.0)),
                    height=float(geometry.get("Height", 0.0)),
                ),
            )
            words.append(word)

    return words


def _textract_result_to_dict(result: TextractResult) -> dict[str, Any]:
    """
    Convierte un TextractResult a diccionario serializable a JSON.

    Sigue el esquema definido en el design doc:
    {
      "documentId": "uuid",
      "pages": [...],
      "totalPages": N,
      "extractedAt": "ISO-8601"
    }

    Args:
        result: TextractResult a convertir.

    Returns:
        Diccionario serializable a JSON.
    """
    pages_list = []
    for page in result.pages:
        blocks_list = []
        for block in page.blocks:
            words_list = [
                {
                    "text": word.text,
                    "boundingBox": {
                        "left": word.bounding_box.left,
                        "top": word.bounding_box.top,
                        "width": word.bounding_box.width,
                        "height": word.bounding_box.height,
                    },
                }
                for word in block.words
            ]
            blocks_list.append({
                "type": block.type,
                "text": block.text,
                "boundingBox": {
                    "left": block.bounding_box.left,
                    "top": block.bounding_box.top,
                    "width": block.bounding_box.width,
                    "height": block.bounding_box.height,
                },
                "words": words_list,
            })
        pages_list.append({
            "pageNumber": page.page_number,
            "blocks": blocks_list,
        })

    output: dict[str, Any] = {
        "documentId": result.document_id,
        "pages": pages_list,
        "totalPages": result.total_pages,
        "extractedAt": result.extracted_at,
    }

    # Incluir error si existe (para logging/debugging)
    if result.error:
        output["error"] = result.error
        output["errorCode"] = result.error_code

    return output
