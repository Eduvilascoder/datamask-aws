"""
Detector de patrones regex configurables para PII.

Aplica EXCLUSIVAMENTE las reglas regex que el usuario define en
Configuración (persistidas en DynamoDB). No hay patrones hardcodeados:
cada regla es un dict {type, pattern, enabled} provisto por la config.
Las detecciones se asignan con confianza 0.95 y source="regex".
"""

import logging
import re

from models import DetectedEntity

logger = logging.getLogger()

# Confianza asignada a detecciones por regex
REGEX_CONFIDENCE = 0.95


def detect_with_rules(
    text: str,
    rules: list[dict],
    start_offset: int = 0,
) -> list[DetectedEntity]:
    """Escanea texto aplicando reglas regex configurables por el usuario.

    Cada regla es un dict {type, pattern, enabled}. Las reglas deshabilitadas
    o con patrón inválido se omiten (sin abortar el resto).

    Args:
        text: Texto a analizar.
        rules: Lista de reglas {type, pattern, enabled}.
        start_offset: Offset base para posiciones absolutas.

    Returns:
        Lista de DetectedEntity detectadas por las reglas activas.
    """
    entities: list[DetectedEntity] = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        entity_type = rule.get("type", "")
        pattern_str = rule.get("pattern", "")
        if not entity_type or not pattern_str:
            continue
        try:
            pattern = re.compile(pattern_str)
        except re.error:
            logger.warning("Patrón regex inválido para '%s' — omitido", entity_type)
            continue

        for match in pattern.finditer(text):
            entities.append(
                DetectedEntity(
                    text=match.group(),
                    type=entity_type,
                    page=1,  # Se asigna la página correcta en el handler
                    start_offset=start_offset + match.start(),
                    end_offset=start_offset + match.end(),
                    source="regex",
                    confidence=REGEX_CONFIDENCE,
                )
            )

    return entities
