"""
Módulo de fusión, deduplicación y salida de entidades detectadas.

Combina los resultados de los tres motores de detección (Comprehend,
Bedrock, Regex), aplica reglas de fusión y deduplicación, y genera
el JSON de salida final para persistir en S3.

Reglas de fusión (Req 6.5, 6.6):
- Mismo tipo + solapamiento → conservar Comprehend
- Solapamiento + distinto tipo + Bedrock cubre más → conservar Bedrock
- Solapamiento + distinto tipo + Bedrock NO cubre más → conservar ambas
- Sin solapamiento → incluir ambas

Reglas de deduplicación (Req 7.3, 7.4):
- Duplicados: mismo tipo, intersección/longitud_menor > 80%
- Conservar mayor span; si iguales, mayor confianza
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from models import DetectedEntity

logger = logging.getLogger(__name__)

# Umbral de solapamiento para considerar duplicados
DEDUP_OVERLAP_THRESHOLD = 0.80


def merge_entities(
    bedrock: list[DetectedEntity],
    regex: list[DetectedEntity],
) -> list[DetectedEntity]:
    """Fusiona entidades de IA (Bedrock) y regex, resuelve solapamientos y ordena.

    Args:
        bedrock: Entidades detectadas por Bedrock (IA).
        regex: Entidades detectadas por regex.

    Returns:
        Lista final de entidades sin solapamientos, ordenada.
    """
    merged: list[DetectedEntity] = list(bedrock) + list(regex)

    # Resolver TODOS los solapamientos (también cross-type) para que cada
    # porción de texto tenga una sola entidad/etiqueta.
    deduplicated = _resolve_overlaps(merged)

    # Ordenar por página ascendente, luego start_offset ascendente.
    deduplicated.sort(key=lambda e: (e.page, e.start_offset))

    return deduplicated


def _entities_overlap(a: DetectedEntity, b: DetectedEntity) -> bool:
    """Verifica si dos entidades se solapan (al menos 1 carácter).

    Dos entidades se solapan si están en la misma página y sus rangos
    de caracteres tienen al menos 1 carácter en común.

    Args:
        a: Primera entidad.
        b: Segunda entidad.

    Returns:
        True si se solapan en al menos 1 carácter.
    """
    # Deben estar en la misma página para solaparse
    if a.page != b.page:
        return False

    # Rangos [start, end) — se solapan si max(starts) < min(ends)
    overlap_start = max(a.start_offset, b.start_offset)
    overlap_end = min(a.end_offset, b.end_offset)

    return overlap_start < overlap_end


def _overlap_ratio(a: DetectedEntity, b: DetectedEntity) -> float:
    """Calcula la proporción de solapamiento entre dos entidades.

    Ratio = intersección / longitud de la entidad más corta.

    Args:
        a: Primera entidad.
        b: Segunda entidad.

    Returns:
        Float entre 0.0 y 1.0 representando el ratio de solapamiento.
        Retorna 0.0 si no están en la misma página.
    """
    if a.page != b.page:
        return 0.0

    # Calcular intersección
    overlap_start = max(a.start_offset, b.start_offset)
    overlap_end = min(a.end_offset, b.end_offset)
    intersection = max(0, overlap_end - overlap_start)

    # Calcular longitud de la entidad más corta
    len_a = a.end_offset - a.start_offset
    len_b = b.end_offset - b.start_offset
    shorter_length = min(len_a, len_b)

    if shorter_length == 0:
        return 0.0

    return intersection / shorter_length


def _deduplicate(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Elimina entidades duplicadas según reglas de deduplicación.

    Duplicados: mismo tipo, intersección/longitud_menor > 80%.
    Se conserva la entidad con mayor span; si spans iguales, mayor
    confianza.

    Args:
        entities: Lista de entidades a deduplicar.

    Returns:
        Lista sin duplicados.
    """
    if len(entities) <= 1:
        return list(entities)

    # Marcar entidades a descartar
    discarded: set[int] = set()

    for i in range(len(entities)):
        if i in discarded:
            continue

        for j in range(i + 1, len(entities)):
            if j in discarded:
                continue

            e_i = entities[i]
            e_j = entities[j]

            # Solo deduplicar entidades del mismo tipo
            if e_i.type != e_j.type:
                continue

            # Verificar si el ratio de solapamiento supera el umbral
            ratio = _overlap_ratio(e_i, e_j)
            if ratio <= DEDUP_OVERLAP_THRESHOLD:
                continue

            # Son duplicados — decidir cuál conservar
            span_i = e_i.end_offset - e_i.start_offset
            span_j = e_j.end_offset - e_j.start_offset

            if span_i > span_j:
                discarded.add(j)
            elif span_j > span_i:
                discarded.add(i)
                break  # i fue descartado, no comparar más
            else:
                # Spans iguales → conservar mayor confianza
                if e_i.confidence >= e_j.confidence:
                    discarded.add(j)
                else:
                    discarded.add(i)
                    break  # i fue descartado

    return [
        entity for idx, entity in enumerate(entities)
        if idx not in discarded
    ]


# Prioridad de motor cuando dos entidades del mismo span empatan en cobertura
# y confianza. Mayor número = gana. Bedrock (IA) se prioriza sobre Comprehend
# para que, ante la misma detección, prevalezca y quede visible la de IA.
_SOURCE_PRIORITY: dict[str, int] = {
    "regex": 3,
    "bedrock": 2,
}


def _resolve_overlaps(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Resuelve TODOS los solapamientos para que cada porción de texto quede
    cubierta por una sola entidad (también entre tipos distintos).

    Evita que el mismo texto (p.ej. un número de tarjeta) se redacte con
    varias etiquetas apiladas (TARJETA_CREDITO + DNI + CUENTA_BANCARIA).

    Criterio de selección entre dos entidades solapadas:
      1. Mayor cobertura (span más largo).
      2. Mayor confianza.
      3. Mayor prioridad de motor (regex > bedrock > comprehend).

    Args:
        entities: Entidades candidatas (de los tres motores).

    Returns:
        Lista sin solapamientos.
    """
    if len(entities) <= 1:
        return list(entities)

    discarded: set[int] = set()

    for i in range(len(entities)):
        if i in discarded:
            continue
        for j in range(i + 1, len(entities)):
            if j in discarded:
                continue

            e_i, e_j = entities[i], entities[j]
            if not _entities_overlap(e_i, e_j):
                continue

            loser = _pick_loser(i, e_i, j, e_j)
            discarded.add(loser)
            if loser == i:
                break  # i descartado: no compararlo más

    return [
        entity for idx, entity in enumerate(entities)
        if idx not in discarded
    ]


def _pick_loser(
    i: int, e_i: DetectedEntity, j: int, e_j: DetectedEntity
) -> int:
    """Devuelve el índice de la entidad a descartar en un solapamiento."""
    span_i = e_i.end_offset - e_i.start_offset
    span_j = e_j.end_offset - e_j.start_offset
    if span_i != span_j:
        return j if span_i > span_j else i

    if e_i.confidence != e_j.confidence:
        return j if e_i.confidence > e_j.confidence else i

    prio_i = _SOURCE_PRIORITY.get(e_i.source, 0)
    prio_j = _SOURCE_PRIORITY.get(e_j.source, 0)
    return j if prio_i >= prio_j else i


def generate_output_json(
    entities: list[DetectedEntity],
    document_id: str,
) -> dict[str, Any]:
    """Genera el JSON de salida con la estructura requerida.

    Estructura:
    {
        "documentId": str,
        "totalEntities": int,
        "entities": [...],
        "sourceContributions": {"comprehend": N, "bedrock": N, "regex": N},
        "processedAt": "ISO-8601"
    }

    Args:
        entities: Lista final de entidades (ya fusionadas y deduplicadas).
        document_id: Identificador del documento.

    Returns:
        Diccionario con la estructura JSON de salida completa.
    """
    # Calcular contribuciones por fuente
    source_contributions: dict[str, int] = {
        "bedrock": 0,
        "regex": 0,
    }
    for entity in entities:
        if entity.source in source_contributions:
            source_contributions[entity.source] += 1

    # Construir JSON de salida
    output: dict[str, Any] = {
        "documentId": document_id,
        "totalEntities": len(entities),
        "entities": [entity.to_dict() for entity in entities],
        "sourceContributions": source_contributions,
        "processedAt": datetime.now(timezone.utc).isoformat(),
    }

    return output


def store_entities_result(
    output: dict[str, Any],
    bucket: str,
    user_id: str,
    document_id: str,
    s3_client: Any,
) -> bool:
    """Almacena el JSON de entidades en S3.

    Ruta: procesamiento/{userId}/{documentId}/entities.json

    Args:
        output: Diccionario JSON a almacenar.
        bucket: Nombre del bucket S3.
        user_id: Identificador del usuario.
        document_id: Identificador del documento.
        s3_client: Cliente boto3 de S3.

    Returns:
        True si el almacenamiento fue exitoso, False en caso contrario.
    """
    s3_key = f"procesamiento/{user_id}/{document_id}/entities.json"

    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=json.dumps(output, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )
        logger.info(
            "Entidades almacenadas en s3://%s/%s", bucket, s3_key
        )
        return True

    except Exception as e:
        logger.error(
            "Error almacenando entidades en S3: bucket=%s, key=%s, error=%s",
            bucket,
            s3_key,
            str(e),
        )
        return False
