"""
Módulo de validación de archivos PDF.

Responsabilidades:
- Verificar firma mágica %PDF en los primeros bytes del archivo.
- Verificar parseabilidad del PDF mediante PyPDF2.
- Validar tamaño del archivo contra el límite de 500MB.
"""

import io
import logging
from dataclasses import dataclass

import boto3
from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

# Constantes de validación
MAX_FILE_SIZE_BYTES: int = 524_288_000  # 500 MB
PDF_MAGIC_BYTES: bytes = b"%PDF"
PDF_HEADER_READ_SIZE: int = 1024


@dataclass
class ValidationResult:
    """Resultado de la validación de un archivo PDF."""

    is_valid: bool
    error_reason: str | None = None
    page_count: int | None = None


def validate_file_size(object_size: int) -> ValidationResult:
    """
    Valida que el tamaño del archivo no exceda el límite de 500MB.

    Args:
        object_size: Tamaño del objeto en bytes (de metadata S3).

    Returns:
        ValidationResult indicando si el tamaño es válido.
    """
    if object_size >= MAX_FILE_SIZE_BYTES:
        size_mb = object_size / (1024 * 1024)
        return ValidationResult(
            is_valid=False,
            error_reason=(
                f"Archivo excede tamaño máximo permitido: "
                f"{size_mb:.1f}MB (máximo: 500MB)"
            ),
        )
    return ValidationResult(is_valid=True)


def validate_pdf_header(
    bucket: str,
    key: str,
    s3_client: "boto3.client" = None,
) -> ValidationResult:
    """
    Valida que el archivo contenga la firma mágica %PDF en su encabezado.

    Descarga los primeros 1024 bytes del objeto S3 y verifica la presencia
    del magic byte %PDF.

    Args:
        bucket: Nombre del bucket S3.
        key: Key del objeto S3.
        s3_client: Cliente S3 (opcional, se crea uno si no se proporciona).

    Returns:
        ValidationResult indicando si el encabezado es válido.
    """
    if s3_client is None:
        s3_client = boto3.client("s3")

    try:
        response = s3_client.get_object(
            Bucket=bucket,
            Key=key,
            Range=f"bytes=0-{PDF_HEADER_READ_SIZE - 1}",
        )
        header_bytes = response["Body"].read()
    except Exception as e:
        logger.error("Error leyendo encabezado del archivo: %s", str(e))
        return ValidationResult(
            is_valid=False,
            error_reason=f"Error leyendo archivo desde S3: {str(e)}",
        )

    if not header_bytes.startswith(PDF_MAGIC_BYTES):
        return ValidationResult(
            is_valid=False,
            error_reason=(
                "Archivo no es un PDF válido: "
                "no contiene firma de encabezado %PDF"
            ),
        )

    return ValidationResult(is_valid=True)


def validate_pdf_parseable(
    bucket: str,
    key: str,
    s3_client: "boto3.client" = None,
) -> ValidationResult:
    """
    Valida que el archivo sea parseable como documento PDF.

    Descarga el archivo completo de S3 e intenta parsearlo con PyPDF2.
    Extrae el número de páginas si es exitoso.

    Args:
        bucket: Nombre del bucket S3.
        key: Key del objeto S3.
        s3_client: Cliente S3 (opcional, se crea uno si no se proporciona).

    Returns:
        ValidationResult con is_valid y page_count si es parseable.
    """
    if s3_client is None:
        s3_client = boto3.client("s3")

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        file_content = response["Body"].read()
    except Exception as e:
        logger.error("Error descargando archivo para validación: %s", str(e))
        return ValidationResult(
            is_valid=False,
            error_reason=f"Error descargando archivo desde S3: {str(e)}",
        )

    try:
        reader = PdfReader(io.BytesIO(file_content))
        page_count = len(reader.pages)
    except PdfReadError as e:
        logger.warning("PDF no es parseable: %s", str(e))
        return ValidationResult(
            is_valid=False,
            error_reason=f"Archivo no es un PDF parseable: {str(e)}",
        )
    except Exception as e:
        logger.warning("Error inesperado al parsear PDF: %s", str(e))
        return ValidationResult(
            is_valid=False,
            error_reason=f"Error al intentar parsear el PDF: {str(e)}",
        )

    return ValidationResult(is_valid=True, page_count=page_count)


def validate_pdf(
    bucket: str,
    key: str,
    object_size: int,
    s3_client: "boto3.client" = None,
) -> ValidationResult:
    """
    Ejecuta validación completa del archivo PDF.

    Orden de validación:
    1. Tamaño (<500MB)
    2. Firma mágica %PDF en encabezado
    3. Parseabilidad con PyPDF2

    Args:
        bucket: Nombre del bucket S3.
        key: Key del objeto S3.
        object_size: Tamaño del objeto en bytes.
        s3_client: Cliente S3 (opcional).

    Returns:
        ValidationResult con el resultado de la validación completa.
    """
    # 1. Validar tamaño
    size_result = validate_file_size(object_size)
    if not size_result.is_valid:
        return size_result

    # 2. Validar firma mágica %PDF
    header_result = validate_pdf_header(bucket, key, s3_client)
    if not header_result.is_valid:
        return header_result

    # 3. Validar parseabilidad
    parse_result = validate_pdf_parseable(bucket, key, s3_client)
    if not parse_result.is_valid:
        return parse_result

    return parse_result
