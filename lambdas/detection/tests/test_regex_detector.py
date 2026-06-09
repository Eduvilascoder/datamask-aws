"""Tests unitarios para el módulo regex_detector.

El detector aplica EXCLUSIVAMENTE reglas regex configurables
(no hay patrones hardcodeados). Las reglas se pasan como lista de
dicts {type, pattern, enabled}, replicando lo que llega desde la
configuración del usuario en DynamoDB.
"""

from models import DetectedEntity
from regex_detector import (
    REGEX_CONFIDENCE,
    detect_with_rules,
)

# Reglas de ejemplo equivalentes a las de la configuración por defecto.
DNI_RULE = {"type": "DNI", "pattern": r"\b\d{1,2}\.\d{3}\.\d{3}\b", "enabled": True}
CUIT_RULE = {
    "type": "CUIT_CUIL",
    "pattern": r"\b(20|23|24|27|30|33|34)[-]?\d{8}[-]?\d\b",
    "enabled": True,
}
PASSPORT_RULE = {
    "type": "PASAPORTE",
    "pattern": r"\bAA[A-Z]?\d{6}\b",
    "enabled": True,
}


class TestDetectWithRulesDNI:
    """Tests de detección de DNI vía regla configurable."""

    def test_detects_valid_dni(self):
        entities = detect_with_rules("DNI: 35.123.456", [DNI_RULE])
        dni = [e for e in entities if e.type == "DNI"]
        assert len(dni) == 1
        assert dni[0].text == "35.123.456"

    def test_detects_multiple_dni(self):
        text = "Juan: 35.123.456, María: 4.567.890"
        entities = detect_with_rules(text, [DNI_RULE])
        assert len([e for e in entities if e.type == "DNI"]) == 2

    def test_dni_confidence_and_source(self):
        entities = detect_with_rules("DNI: 35.123.456", [DNI_RULE])
        assert entities[0].confidence == REGEX_CONFIDENCE
        assert entities[0].source == "regex"

    def test_rejects_non_matching_text(self):
        entities = detect_with_rules("Texto sin datos", [DNI_RULE])
        assert entities == []


class TestDetectWithRulesCuit:
    """Tests de detección de CUIT/CUIL vía regla configurable."""

    def test_detects_valid_cuit(self):
        entities = detect_with_rules("CUIT: 20-12345678-9", [CUIT_RULE])
        cuit = [e for e in entities if e.type == "CUIT_CUIL"]
        assert len(cuit) == 1


class TestDetectWithRulesPassport:
    """Tests de detección de pasaporte vía regla configurable."""

    def test_detects_valid_passport(self):
        entities = detect_with_rules("Pasaporte: AAB123456", [PASSPORT_RULE])
        passports = [e for e in entities if e.type == "PASAPORTE"]
        assert len(passports) == 1


EXPEDIENTE_GDE_RULE = {
    "type": "EXPEDIENTE_GDE",
    "pattern": r"\b[A-Z]{2,5}-\d{4}-\d{6,}-[A-Z]{2,}-[A-Z0-9]+#[A-Z]+\b",
    "enabled": True,
}


class TestExpedienteGde:
    """Tests del expediente GDE (formato APN)."""

    def test_detects_ex_format(self):
        text = "Expediente: EX-2025-12345678-APN-SCEYM#MEC"
        entities = detect_with_rules(text, [EXPEDIENTE_GDE_RULE])
        assert len(entities) == 1
        assert entities[0].text == "EX-2025-12345678-APN-SCEYM#MEC"
        assert entities[0].type == "EXPEDIENTE_GDE"

    def test_detects_other_doc_types(self):
        text = "IF-2024-00098765-APN-DGD#MEC y NO-2023-111111-APN-SSGA#JGM"
        entities = detect_with_rules(text, [EXPEDIENTE_GDE_RULE])
        assert len(entities) == 2

    def test_rejects_short_number(self):
        # Menos de 6 dígitos en el número → no es un expediente válido.
        text = "EX-2025-1-APN-X#Y"
        assert detect_with_rules(text, [EXPEDIENTE_GDE_RULE]) == []


class TestDetectWithRulesGeneral:
    """Tests generales de detect_with_rules."""

    def test_empty_text_returns_empty(self):
        assert detect_with_rules("", [DNI_RULE]) == []

    def test_empty_rules_returns_empty(self):
        assert detect_with_rules("DNI: 35.123.456", []) == []

    def test_disabled_rule_is_skipped(self):
        disabled = dict(DNI_RULE, enabled=False)
        assert detect_with_rules("DNI: 35.123.456", [disabled]) == []

    def test_invalid_pattern_is_skipped(self):
        bad_rule = {"type": "BAD", "pattern": r"([", "enabled": True}
        # No debe abortar: omite la regla inválida y procesa el resto.
        entities = detect_with_rules("DNI: 35.123.456", [bad_rule, DNI_RULE])
        assert len([e for e in entities if e.type == "DNI"]) == 1

    def test_rule_without_type_or_pattern_is_skipped(self):
        assert detect_with_rules("35.123.456", [{"enabled": True}]) == []

    def test_multiple_types_in_same_text(self):
        text = "DNI: 35.123.456, CUIT: 20-12345678-9, Pasaporte: AAB123456"
        entities = detect_with_rules(text, [DNI_RULE, CUIT_RULE, PASSPORT_RULE])
        types = {e.type for e in entities}
        assert {"DNI", "CUIT_CUIL", "PASAPORTE"} <= types

    def test_start_offset_is_applied(self):
        entities = detect_with_rules("35.123.456", [DNI_RULE], start_offset=100)
        assert len(entities) == 1
        assert entities[0].start_offset == 100

    def test_returns_detected_entity_instances(self):
        entities = detect_with_rules("DNI: 35.123.456", [DNI_RULE])
        assert isinstance(entities[0], DetectedEntity)
        assert entities[0].page == 1
