"""
Motor de redacción PDF con PyMuPDF.

Aplica redacciones al PDF reemplazando texto PII por etiquetas [TIPO]
con color de fondo diferenciado por tipo de entidad.

Preserva la estructura del PDF (fuentes, layout, imágenes) usando
guardado incremental.
"""

import logging
import time
from typing import Any

import fitz  # PyMuPDF

from models import RedactionResult
from text_matcher import find_text_instances, find_textract_instances

logger = logging.getLogger(__name__)

# Colores de fondo por tipo de entidad (RGB normalizado 0-1)
ENTITY_COLOR_MAP: dict[str, tuple[float, float, float]] = {
    "NOMBRE": (0.8, 0.898, 1.0),        # #CCE5FF - light blue
    "EMAIL": (0.8, 1.0, 0.8),            # #CCFFCC - light green
    "TELEFONO": (1.0, 1.0, 0.8),         # #FFFFCC - light yellow
    "CELULAR": (1.0, 1.0, 0.8),          # #FFFFCC - light yellow
    "DIRECCION": (1.0, 0.898, 0.8),      # #FFE5CC - light orange
    "DNI": (1.0, 0.8, 0.8),             # #FFCCCC - light red
    "CUIT_CUIL": (0.898, 0.8, 1.0),      # #E5CCFF - light purple
    "TARJETA_CREDITO": (1.0, 0.702, 0.702),  # #FFB3B3 - red
    "CUENTA_BANCARIA": (1.0, 0.8, 0.898),    # #FFCCE5 - light pink
    "PASAPORTE": (0.8, 1.0, 1.0),        # #CCFFFF - light cyan
    "FECHA": (0.898, 0.898, 0.898),      # #E5E5E5 - light gray
}

# Color por defecto para tipos no mapeados
DEFAULT_COLOR: tuple[float, float, float] = (0.9, 0.9, 0.9)

# Sufijo de etiqueta según el motor que detectó (ganó) la entidad.
#   bedrock    -> IA  (modelo de IA)
#   comprehend -> C   (Amazon Comprehend)
#   regex      -> R   (regla determinista)
SOURCE_SUFFIX_MAP: dict[str, str] = {
    "bedrock": "IA",
    "comprehend": "C",
    "regex": "R",
}


def _entity_label(entity_type: str, source: str) -> str:
    """Construye la etiqueta de redacción con el sufijo del motor.

    Ejemplos: DNI detectado por IA -> "[DNI-IA]";
    CELULAR por Comprehend -> "[CELULAR-C]"; DNI por regex -> "[DNI-R]".
    """
    suffix = SOURCE_SUFFIX_MAP.get(source, "")
    return f"[{entity_type}-{suffix}]" if suffix else f"[{entity_type}]"


# Umbral de solapamiento de área para considerar que un rectángulo ya fue
# redactado (evita pintar etiquetas apiladas sobre la misma zona).
_RECT_OVERLAP_THRESHOLD = 0.5


def _rect_already_redacted(rect: Any, existing: list[Any]) -> bool:
    """Indica si un rectángulo se solapa significativamente con otro ya redactado.

    Usa la intersección de áreas: si más del 50% del rectángulo nuevo (el más
    chico) cae dentro de uno ya redactado, se considera duplicado visual.

    Args:
        rect: Rectángulo candidato (fitz.Rect).
        existing: Rectángulos ya redactados en la página.

    Returns:
        True si debe saltarse para no apilar etiquetas.
    """
    area = abs(rect.get_area()) if hasattr(rect, "get_area") else 0.0
    if area <= 0:
        return False

    for other in existing:
        inter = rect & other  # intersección de rectángulos en PyMuPDF
        inter_area = abs(inter.get_area()) if not inter.is_empty else 0.0
        if inter_area <= 0:
            continue
        smaller = min(area, abs(other.get_area()) or area)
        if smaller > 0 and (inter_area / smaller) >= _RECT_OVERLAP_THRESHOLD:
            return True
    return False


def _get_fill_color(entity_type: str) -> tuple[float, float, float]:
    """Obtiene el color de fondo para un tipo de entidad.

    Args:
        entity_type: Tipo de entidad PII.

    Returns:
        Tupla RGB normalizada (0-1) con el color de fondo.
    """
    return ENTITY_COLOR_MAP.get(entity_type, DEFAULT_COLOR)


def _font_size_at_rect(page: "fitz.Page", rect: "fitz.Rect") -> float:
    """Determina el tamaño de fuente del texto original en un rectángulo.

    Recorre los spans de texto de la página y devuelve el tamaño del span
    que mejor solapa con el rectángulo de la entidad detectada. Permite que
    la etiqueta [TIPO] conserve el tamaño de la palabra ofuscada.

    Args:
        page: Página PyMuPDF.
        rect: Rectángulo de la ocurrencia detectada.

    Returns:
        Tamaño de fuente en puntos. 0 si no se puede determinar (auto-ajuste).
    """
    try:
        data = page.get_text("dict")
    except Exception:
        return 0.0

    best_size = 0.0
    best_overlap = 0.0
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span.get("bbox")
                size = span.get("size", 0.0)
                if not bbox or not size:
                    continue
                span_rect = fitz.Rect(bbox)
                inter = span_rect & rect
                if inter.is_empty:
                    continue
                overlap_area = inter.width * inter.height
                if overlap_area > best_overlap:
                    best_overlap = overlap_area
                    best_size = size

    return best_size


def redact_pdf(
    pdf_bytes: bytes,
    entities: list[dict],
    textract_pages: list[dict] | None = None,
) -> RedactionResult:
    """Aplica redacciones a un PDF reemplazando PII por etiquetas [TIPO].

    Busca cada entidad en el texto del PDF por página y aplica
    anotaciones de redacción con PyMuPDF. Preserva la estructura
    del documento usando guardado incremental.

    Args:
        pdf_bytes: Bytes del PDF original.
        entities: Lista de entidades detectadas. Cada entidad es un dict
                  con campos: text, type, page, startOffset, endOffset,
                  source, confidence.
        textract_pages: Geometría de Textract (bounding boxes por palabra)
                  usada como fallback para localizar texto que está en
                  imágenes y no en la capa de texto del PDF.

    Returns:
        RedactionResult con el PDF redactado o información de error.
    """
    start_time = time.time()
    textract_pages = textract_pages or []

    # Si no hay entidades, retornar temprano sin generar archivo
    if not entities:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info("Sin entidades para redactar — omitiendo generación")
        return RedactionResult(
            success=True,
            redacted_pdf_bytes=None,
            entities_redacted=0,
            entities_by_type={},
            processing_time_ms=elapsed_ms,
        )

    try:
        doc = _open_pdf(pdf_bytes)
    except PasswordProtectedError:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return RedactionResult(
            success=False,
            error_code="PASSWORD_PROTECTED",
            error_message="El PDF está protegido con contraseña",
            processing_time_ms=elapsed_ms,
        )
    except CorruptedPdfError:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return RedactionResult(
            success=False,
            error_code="CORRUPTED",
            error_message="El PDF está corrupto o no es válido",
            processing_time_ms=elapsed_ms,
        )

    try:
        entities_redacted = 0
        entities_by_type: dict[str, int] = {}
        # Páginas (0-indexed) con redacciones pendientes de aplicar.
        pages_to_apply: set[int] = set()

        # Agrupar entidades por página
        entities_by_page: dict[int, list[dict]] = {}
        for entity in entities:
            page_num = entity.get("page", 1)
            entities_by_page.setdefault(page_num, []).append(entity)

        # Aplicar redacciones página por página
        for page_num, page_entities in entities_by_page.items():
            page_index = page_num - 1  # PyMuPDF usa 0-indexed
            if page_index < 0 or page_index >= len(doc):
                logger.warning(
                    "Página %d fuera de rango (total: %d) — omitiendo",
                    page_num,
                    len(doc),
                )
                continue

            page = doc[page_index]
            # Rectángulos ya redactados en esta página, para evitar apilar
            # etiquetas sobre la misma zona (p.ej. un DNI que es substring de
            # un número de tarjeta, o detecciones solapadas de distinto tipo).
            redacted_rects: list[Any] = []

            for entity in page_entities:
                entity_text = entity.get("text", "")
                entity_type = entity.get("type", "UNKNOWN")
                entity_source = entity.get("source", "")

                if not entity_text:
                    continue

                # Buscar las ocurrencias en la página asignada de forma
                # TOLERANTE (ignora diferencias de espacios, guiones y tildes
                # entre el texto detectado y el texto real del PDF).
                target_page = page
                target_rects = redacted_rects
                text_instances = find_text_instances(page, entity_text)

                # Fallback 1: si no aparece en la página asignada, buscar en
                # TODAS las páginas (cubre desajustes de numeración).
                if not text_instances:
                    found_page, found_instances = _search_all_pages(
                        doc, entity_text
                    )
                    if found_instances:
                        target_page = found_page
                        text_instances = found_instances
                        target_rects = []  # otra página: sin contexto previo

                # Fallback 2: texto que vive en IMÁGENES (no en la capa de
                # texto del PDF, p.ej. el nombre del encabezado de un CV). Se
                # usa la geometría de Textract para ubicarlo y redactarlo.
                if not text_instances:
                    text_instances = find_textract_instances(
                        page, entity_text, textract_pages, page_num
                    )
                    if text_instances:
                        target_page = page
                        target_rects = redacted_rects

                if not text_instances:
                    logger.info(
                        "Texto '%s' (%s) no encontrado en el documento",
                        entity_text[:50],
                        entity_type,
                    )
                    continue

                # Aplicar redacción a cada ocurrencia encontrada
                fill_color = _get_fill_color(entity_type)
                label = _entity_label(entity_type, entity_source)

                for rect in text_instances:
                    # Saltar si esta zona ya fue redactada por otra entidad
                    # (evita el apilado visual de etiquetas).
                    if _rect_already_redacted(rect, target_rects):
                        continue

                    # Conservar el tamaño de fuente del texto original.
                    font_size = _font_size_at_rect(target_page, rect)
                    target_page.add_redact_annot(
                        rect,
                        text=label,
                        fill=fill_color,
                        fontsize=font_size,  # 0 = auto-ajustar al rect
                    )
                    target_rects.append(rect)
                    pages_to_apply.add(target_page.number)
                    entities_redacted += 1
                    entities_by_type[entity_type] = (
                        entities_by_type.get(entity_type, 0) + 1
                    )

        # Aplicar las redacciones acumuladas en cada página afectada.
        for page_number in pages_to_apply:
            doc[page_number].apply_redactions()

        # Guardar PDF preservando estructura
        # Guardado incremental solo disponible con archivo original en disco.
        # Desde streams en memoria usamos deflate + garbage=0 para preservar
        # la estructura sin eliminar objetos no modificados.
        redacted_bytes = doc.tobytes(
            deflate=True,
            garbage=0,  # No eliminar objetos — preservar estructura
        )
        doc.close()

        elapsed_ms = int((time.time() - start_time) * 1000)

        logger.info(
            "Redacción completada: %d entidades en %dms",
            entities_redacted,
            elapsed_ms,
        )

        return RedactionResult(
            success=True,
            redacted_pdf_bytes=redacted_bytes,
            entities_redacted=entities_redacted,
            entities_by_type=entities_by_type,
            processing_time_ms=elapsed_ms,
        )

    except Exception as e:
        doc.close()
        elapsed_ms = int((time.time() - start_time) * 1000)
        error_msg = f"Error inesperado durante redacción: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return RedactionResult(
            success=False,
            error_message=error_msg,
            processing_time_ms=elapsed_ms,
        )


def _search_all_pages(
    doc: "fitz.Document", entity_text: str
) -> tuple[Any, list[Any]]:
    """Busca un texto en todas las páginas del documento (tolerante).

    Fallback cuando el texto de una entidad no aparece en su página asignada
    (p.ej. por desajuste de numeración entre el detector y el PDF). Devuelve
    la primera página donde se encuentra y sus rectángulos.

    Args:
        doc: Documento PyMuPDF.
        entity_text: Texto de la entidad a localizar.

    Returns:
        Tupla (página, rectángulos). Si no se encuentra, (None, []).
    """
    for page in doc:
        rects = find_text_instances(page, entity_text)
        if rects:
            return page, rects
    return None, []


def _open_pdf(pdf_bytes: bytes) -> fitz.Document:
    """Abre un documento PDF desde bytes con manejo de errores.

    Args:
        pdf_bytes: Bytes del documento PDF.

    Returns:
        Documento PyMuPDF abierto.

    Raises:
        PasswordProtectedError: Si el PDF requiere contraseña.
        CorruptedPdfError: Si el PDF está corrupto o no es válido.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        error_msg = str(e).lower()
        if "password" in error_msg or "encrypted" in error_msg:
            raise PasswordProtectedError(
                "PDF protegido con contraseña"
            ) from e
        raise CorruptedPdfError(
            f"No se pudo abrir el PDF: {e}"
        ) from e

    # Verificar si requiere contraseña para acceso
    if doc.needs_pass:
        doc.close()
        raise PasswordProtectedError("PDF protegido con contraseña")

    # Validación básica de integridad
    if doc.page_count == 0:
        doc.close()
        raise CorruptedPdfError("El PDF no contiene páginas")

    return doc


class PasswordProtectedError(Exception):
    """Error cuando un PDF está protegido con contraseña."""


class CorruptedPdfError(Exception):
    """Error cuando un PDF está corrupto o no es parseable."""
