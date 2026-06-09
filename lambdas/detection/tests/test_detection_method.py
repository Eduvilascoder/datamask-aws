"""Tests del enrutamiento por método de ofuscación en el handler de detección.

Verifica que `_detect_all_pages` ejecute solo los motores que correspondan
según `detectionMethod` (ai | regex | both).
"""

from unittest.mock import patch

import handler
from models import DetectedEntity


def _bedrock_entity() -> DetectedEntity:
    return DetectedEntity(
        text="Juan Pérez", type="NOMBRE", page=1,
        start_offset=0, end_offset=10, source="bedrock", confidence=0.85,
    )


def _regex_entity() -> DetectedEntity:
    return DetectedEntity(
        text="35.123.456", type="DNI", page=1,
        start_offset=20, end_offset=30, source="regex", confidence=0.95,
    )


def _config(method: str) -> dict:
    return {
        "detectionMethod": method,
        "bedrockModelId": "model-x",
        "bedrockTemperature": 0.0,
        "bedrockPrompt": "Detectá PII: {text}",
        "regexRules": [{"type": "DNI", "pattern": r"\d+", "enabled": True}],
        "ignoreEntities": [],
    }


PAGES = [{"page_number": 1, "text": "Juan Pérez DNI 35.123.456"}]


def test_method_ai_runs_only_bedrock():
    with patch.object(handler, "_run_bedrock", return_value=[_bedrock_entity()]) as br, \
         patch.object(handler, "_run_regex", return_value=[_regex_entity()]) as rx:
        result = handler._detect_all_pages("txt", PAGES, "doc-1", _config("ai"))

    br.assert_called_once()
    rx.assert_not_called()
    assert {e.source for e in result} == {"bedrock"}


def test_method_regex_runs_only_regex():
    with patch.object(handler, "_run_bedrock", return_value=[_bedrock_entity()]) as br, \
         patch.object(handler, "_run_regex", return_value=[_regex_entity()]) as rx:
        result = handler._detect_all_pages("txt", PAGES, "doc-1", _config("regex"))

    br.assert_not_called()
    rx.assert_called_once()
    assert {e.source for e in result} == {"regex"}


def test_method_both_runs_both_engines():
    with patch.object(handler, "_run_bedrock", return_value=[_bedrock_entity()]) as br, \
         patch.object(handler, "_run_regex", return_value=[_regex_entity()]) as rx:
        result = handler._detect_all_pages("txt", PAGES, "doc-1", _config("both"))

    br.assert_called_once()
    rx.assert_called_once()
    assert {e.source for e in result} == {"bedrock", "regex"}


def test_method_defaults_to_both_when_missing():
    cfg = _config("both")
    del cfg["detectionMethod"]
    with patch.object(handler, "_run_bedrock", return_value=[_bedrock_entity()]) as br, \
         patch.object(handler, "_run_regex", return_value=[_regex_entity()]) as rx:
        handler._detect_all_pages("txt", PAGES, "doc-1", cfg)

    br.assert_called_once()
    rx.assert_called_once()
