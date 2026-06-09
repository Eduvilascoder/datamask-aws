"""
Tests unitarios para entity_merger.py (pipeline IA + Regex, sin Comprehend).

Cubre:
- Solapamiento de entidades (_entities_overlap, _overlap_ratio)
- Deduplicación (_deduplicate)
- Resolución de solapamientos cross-type (merge_entities / _resolve_overlaps)
- Generación de JSON de salida
- Almacenamiento en S3
"""

import json
from unittest.mock import MagicMock

import pytest

from lambdas.detection.entity_merger import (
    DEDUP_OVERLAP_THRESHOLD,
    _deduplicate,
    _entities_overlap,
    _overlap_ratio,
    generate_output_json,
    merge_entities,
    store_entities_result,
)
from lambdas.detection.models import DetectedEntity


def _entity(
    text: str = "test",
    type_: str = "NOMBRE",
    page: int = 1,
    start: int = 0,
    end: int | None = None,
    source: str = "bedrock",
    confidence: float = 0.90,
) -> DetectedEntity:
    """Crea una entidad de test con valores por defecto."""
    if end is None:
        end = start + len(text)
    return DetectedEntity(
        text=text,
        type=type_,
        page=page,
        start_offset=start,
        end_offset=end,
        source=source,
        confidence=confidence,
    )


class TestEntitiesOverlap:
    def test_no_overlap_adjacent(self):
        assert _entities_overlap(_entity(start=0, end=5), _entity(start=5, end=10)) is False

    def test_overlap_one_char(self):
        assert _entities_overlap(_entity(start=0, end=5), _entity(start=4, end=10)) is True

    def test_full_overlap(self):
        assert _entities_overlap(_entity(start=0, end=10), _entity(start=2, end=8)) is True

    def test_different_pages_no_overlap(self):
        a = _entity(page=1, start=0, end=10)
        b = _entity(page=2, start=0, end=10)
        assert _entities_overlap(a, b) is False


class TestOverlapRatio:
    def test_no_overlap_returns_zero(self):
        assert _overlap_ratio(_entity(start=0, end=5), _entity(start=10, end=15)) == 0.0

    def test_full_containment_returns_one(self):
        assert _overlap_ratio(_entity(start=0, end=10), _entity(start=2, end=8)) == 1.0

    def test_partial_overlap(self):
        a = _entity(start=0, end=10)
        b = _entity(start=6, end=16)
        assert _overlap_ratio(a, b) == pytest.approx(0.4)

    def test_above_threshold(self):
        a = _entity(start=0, end=10)
        b = _entity(start=1, end=10)
        assert _overlap_ratio(a, b) > DEDUP_OVERLAP_THRESHOLD


class TestDeduplicate:
    def test_no_duplicates(self):
        entities = [
            _entity(text="Juan", type_="NOMBRE", start=0, end=4),
            _entity(text="test@x.com", type_="EMAIL", start=20, end=30),
        ]
        assert len(_deduplicate(entities)) == 2

    def test_same_type_high_overlap_keeps_larger_span(self):
        entities = [
            _entity(text="Juan", type_="NOMBRE", start=0, end=4, confidence=0.90),
            _entity(text="Juan Pérez", type_="NOMBRE", start=0, end=10, confidence=0.85),
        ]
        result = _deduplicate(entities)
        assert len(result) == 1
        assert result[0].text == "Juan Pérez"

    def test_empty_list(self):
        assert _deduplicate([]) == []


class TestMergeEntities:
    """Tests del pipeline IA (bedrock) + regex."""

    def test_full_merge(self):
        bed = [
            _entity(text="Juan Pérez", type_="NOMBRE", start=0, end=10,
                    source="bedrock", confidence=0.95),
            _entity(text="test@x.com", type_="EMAIL", start=50, end=60,
                    source="bedrock", confidence=0.85),
        ]
        regex = [
            _entity(text="35.123.456", type_="DNI", start=100, end=110,
                    source="regex", confidence=0.95),
        ]
        result = merge_entities(bedrock=bed, regex=regex)
        assert len(result) == 3
        assert [e.start_offset for e in result] == [0, 50, 100]

    def test_sorted_by_page_then_offset(self):
        bed = [
            _entity(page=2, start=5, end=10, source="bedrock"),
            _entity(page=1, start=20, end=25, source="bedrock"),
            _entity(page=1, start=5, end=10, source="bedrock"),
        ]
        result = merge_entities(bedrock=bed, regex=[])
        assert (result[0].page, result[0].start_offset) == (1, 5)
        assert (result[1].page, result[1].start_offset) == (1, 20)
        assert result[2].page == 2

    def test_empty_returns_empty(self):
        assert merge_entities(bedrock=[], regex=[]) == []

    def test_only_regex(self):
        regex = [_entity(text="35.123.456", type_="DNI", start=0, end=10, source="regex")]
        result = merge_entities(bedrock=[], regex=regex)
        assert len(result) == 1
        assert result[0].source == "regex"

    def test_overlap_cross_type_keeps_one(self):
        """Solapamiento entre IA y regex (mismo texto) → una sola entidad."""
        bed = [_entity(text="35123456", type_="DNI", start=0, end=8,
                       source="bedrock", confidence=0.9)]
        regex = [_entity(text="35123456", type_="CUENTA_BANCARIA", start=0, end=8,
                         source="regex", confidence=0.95)]
        result = merge_entities(bedrock=bed, regex=regex)
        assert len(result) == 1


class TestGenerateOutputJson:
    def test_structure_with_entities(self):
        entities = [
            _entity(text="Juan", type_="NOMBRE", start=0, end=4, source="bedrock"),
            _entity(text="35.123.456", type_="DNI", start=20, end=30, source="regex"),
        ]
        output = generate_output_json(entities, "doc-123")
        assert output["documentId"] == "doc-123"
        assert output["totalEntities"] == 2
        assert output["sourceContributions"] == {"bedrock": 1, "regex": 1}

    def test_empty_entities(self):
        output = generate_output_json([], "doc-empty")
        assert output["totalEntities"] == 0
        assert output["entities"] == []
        assert output["sourceContributions"] == {"bedrock": 0, "regex": 0}


class TestStoreEntitiesResult:
    def test_successful_storage(self):
        mock_s3 = MagicMock()
        result = store_entities_result(
            output={"documentId": "doc-1", "entities": []},
            bucket="my-bucket",
            user_id="user-123",
            document_id="doc-1",
            s3_client=mock_s3,
        )
        assert result is True
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Key"] == "procesamiento/user-123/doc-1/entities.json"

    def test_s3_error_returns_false(self):
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("Access Denied")
        result = store_entities_result(
            output={"entities": []},
            bucket="b",
            user_id="u",
            document_id="d",
            s3_client=mock_s3,
        )
        assert result is False
