"""
Modelos de datos para el motor de redacción PDF.

Define las estructuras de resultado y configuración
para el pipeline de redacción.
"""

from dataclasses import dataclass, field


@dataclass
class RedactionResult:
    """Resultado de la operación de redacción PDF.

    Attributes:
        success: Indica si la redacción fue exitosa.
        redacted_pdf_bytes: Bytes del PDF redactado (None si fallo).
        entities_redacted: Cantidad total de entidades redactadas.
        entities_by_type: Desglose de entidades por tipo.
        error_code: Código de error específico si success=False
                    (PASSWORD_PROTECTED, CORRUPTED, None).
        error_message: Mensaje descriptivo del error.
        processing_time_ms: Tiempo de procesamiento en milisegundos.
    """

    success: bool
    redacted_pdf_bytes: bytes | None = None
    entities_redacted: int = 0
    entities_by_type: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    processing_time_ms: int = 0
