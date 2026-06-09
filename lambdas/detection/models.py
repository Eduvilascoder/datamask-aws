"""
Modelos de datos para el motor de detección PII.

Define las estructuras de datos compartidas entre los módulos
del pipeline de detección.
"""

from dataclasses import dataclass


@dataclass
class DetectedEntity:
    """Entidad PII detectada en el texto del documento.

    Attributes:
        text: Texto literal de la entidad detectada.
        type: Tipo de entidad mapeado al sistema
              (NOMBRE, EMAIL, TELEFONO, etc.).
        page: Número de página donde se encontró (1-indexed).
        start_offset: Posición absoluta de inicio en el texto completo.
        end_offset: Posición absoluta de fin en el texto completo.
        source: Motor que detectó la entidad
                ("comprehend", "bedrock", "regex").
        confidence: Score de confianza entre 0.0 y 1.0.
    """

    text: str
    type: str
    page: int
    start_offset: int
    end_offset: int
    source: str
    confidence: float

    def to_dict(self) -> dict:
        """Serializa la entidad a diccionario para JSON."""
        return {
            "text": self.text,
            "type": self.type,
            "page": self.page,
            "startOffset": self.start_offset,
            "endOffset": self.end_offset,
            "source": self.source,
            "confidence": self.confidence,
        }
