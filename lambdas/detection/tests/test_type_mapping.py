"""Tests unitarios para el módulo type_mapping."""

import pytest

from lambdas.detection.type_mapping import COMPREHEND_TYPE_MAP, map_comprehend_type


class TestComprehendTypeMap:
    """Tests del diccionario de mapeo de tipos."""

    def test_map_contains_all_required_types(self):
        """El mapeo incluye todos los tipos requeridos por la spec."""
        required_mappings = {
            "NAME": "NOMBRE",
            "EMAIL_ADDRESS": "EMAIL",
            "PHONE": "TELEFONO",
            "ADDRESS": "DIRECCION",
            "CREDIT_DEBIT_NUMBER": "TARJETA_CREDITO",
            "BANK_ACCOUNT_NUMBER": "CUENTA_BANCARIA",
            "PASSPORT_NUMBER": "PASAPORTE",
            "DATE_TIME": "FECHA",
        }
        for comp_type, sys_type in required_mappings.items():
            assert COMPREHEND_TYPE_MAP[comp_type] == sys_type

    def test_map_has_exactly_8_entries(self):
        """El mapeo tiene exactamente 8 tipos definidos."""
        assert len(COMPREHEND_TYPE_MAP) == 8


class TestMapComprehendType:
    """Tests de la función map_comprehend_type."""

    @pytest.mark.parametrize(
        "input_type,expected",
        [
            ("NAME", "NOMBRE"),
            ("EMAIL_ADDRESS", "EMAIL"),
            ("PHONE", "TELEFONO"),
            ("ADDRESS", "DIRECCION"),
            ("CREDIT_DEBIT_NUMBER", "TARJETA_CREDITO"),
            ("BANK_ACCOUNT_NUMBER", "CUENTA_BANCARIA"),
            ("PASSPORT_NUMBER", "PASAPORTE"),
            ("DATE_TIME", "FECHA"),
        ],
    )
    def test_maps_known_types(self, input_type: str, expected: str):
        """Tipos conocidos se mapean correctamente."""
        assert map_comprehend_type(input_type) == expected

    @pytest.mark.parametrize(
        "input_type",
        [
            "OTHER",
            "AGE",
            "SSN",
            "DRIVER_ID",
            "IP_ADDRESS",
            "MAC_ADDRESS",
            "URL",
            "USERNAME",
            "PASSWORD",
            "",
            "UNKNOWN_TYPE",
        ],
    )
    def test_unknown_types_return_none(self, input_type: str):
        """Tipos no mapeados retornan None (se descartan)."""
        assert map_comprehend_type(input_type) is None

    def test_case_sensitive(self):
        """El mapeo es case-sensitive (Comprehend usa UPPER_CASE)."""
        assert map_comprehend_type("name") is None
        assert map_comprehend_type("Name") is None
        assert map_comprehend_type("NAME") == "NOMBRE"
