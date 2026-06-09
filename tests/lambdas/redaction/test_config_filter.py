"""
Tests unitarios para lambdas/redaction/config_filter.py.

Valida:
- get_active_types: lectura de config desde DynamoDB y conversión a MAYÚSCULAS
- filter_entities_by_config: filtrado de entidades por tipos activos
- Manejo de edge cases: usuario sin config, error de DynamoDB, config parcial
"""

from unittest.mock import MagicMock, patch

import pytest

from lambdas.redaction.config_filter import (
    DEFAULT_CONFIG,
    PII_TYPE_MAPPING,
    filter_entities_by_config,
    get_active_types,
)


# ------------------------------------------------------------------
# Tests para filter_entities_by_config
# ------------------------------------------------------------------


class TestFilterEntitiesByConfig:
    """Tests para la función filter_entities_by_config."""

    def test_filter_keeps_active_types(self) -> None:
        """Solo conserva entidades cuyo tipo está en active_types."""
        entities = [
            {"type": "NOMBRE", "text": "Juan Pérez", "start": 0},
            {"type": "EMAIL", "text": "juan@mail.com", "start": 20},
            {"type": "DNI", "text": "35.123.456", "start": 40},
        ]
        active_types = {"NOMBRE", "DNI"}

        result = filter_entities_by_config(entities, active_types)

        assert len(result) == 2
        assert result[0]["type"] == "NOMBRE"
        assert result[1]["type"] == "DNI"

    def test_filter_removes_inactive_types(self) -> None:
        """Elimina entidades cuyo tipo no está en active_types."""
        entities = [
            {"type": "NOMBRE", "text": "Juan Pérez", "start": 0},
            {"type": "EMAIL", "text": "juan@mail.com", "start": 20},
            {"type": "TELEFONO", "text": "011-4567-8901", "start": 40},
        ]
        active_types = {"NOMBRE"}

        result = filter_entities_by_config(entities, active_types)

        assert len(result) == 1
        assert result[0]["type"] == "NOMBRE"

    def test_filter_empty_entities_list(self) -> None:
        """Con lista vacía de entidades, retorna lista vacía."""
        result = filter_entities_by_config([], {"NOMBRE", "EMAIL"})
        assert result == []

    def test_filter_empty_active_types(self) -> None:
        """Con conjunto vacío de tipos activos, filtra todo."""
        entities = [
            {"type": "NOMBRE", "text": "Juan Pérez", "start": 0},
            {"type": "DNI", "text": "35.123.456", "start": 20},
        ]
        result = filter_entities_by_config(entities, set())
        assert result == []

    def test_filter_all_types_active(self) -> None:
        """Con todos los tipos activos, conserva todas las entidades."""
        all_types = set(PII_TYPE_MAPPING.values())
        entities = [
            {"type": "NOMBRE", "text": "Juan", "start": 0},
            {"type": "EMAIL", "text": "a@b.com", "start": 10},
            {"type": "CUIT_CUIL", "text": "20-12345678-1", "start": 20},
        ]

        result = filter_entities_by_config(entities, all_types)
        assert len(result) == 3

    def test_filter_entity_without_type_field(self) -> None:
        """Entidades sin campo 'type' se descartan."""
        entities = [
            {"text": "Juan Pérez", "start": 0},  # sin 'type'
            {"type": "NOMBRE", "text": "María López", "start": 20},
        ]
        active_types = {"NOMBRE"}

        result = filter_entities_by_config(entities, active_types)
        assert len(result) == 1
        assert result[0]["text"] == "María López"

    def test_filter_preserves_entity_data(self) -> None:
        """El filtrado no modifica los datos de las entidades."""
        original_entity = {
            "type": "DNI",
            "text": "35.123.456",
            "start": 10,
            "end": 20,
            "confidence": 0.95,
            "source": "regex",
        }
        entities = [original_entity]

        result = filter_entities_by_config(entities, {"DNI"})

        assert result[0] == original_entity


# ------------------------------------------------------------------
# Tests para get_active_types
# ------------------------------------------------------------------


class TestGetActiveTypes:
    """Tests para la función get_active_types."""

    def _make_mock_table(self, item: dict | None = None) -> MagicMock:
        """Crea un mock de tabla DynamoDB con respuesta configurada."""
        mock_table = MagicMock()
        if item is not None:
            mock_table.get_item.return_value = {"Item": item}
        else:
            mock_table.get_item.return_value = {}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        return mock_resource

    def test_all_types_active_returns_full_set(self) -> None:
        """Con todos los tipos activos, retorna el set completo."""
        item = {
            "PK": "USER#user1",
            "SK": "CONFIG#PII_TYPES",
            **{k: True for k in PII_TYPE_MAPPING},
        }
        mock_resource = self._make_mock_table(item)

        result = get_active_types("user1", "test-table", mock_resource)

        assert result == set(PII_TYPE_MAPPING.values())

    def test_some_types_inactive(self) -> None:
        """Con algunos tipos desactivados, retorna solo los activos."""
        item = {
            "PK": "USER#user1",
            "SK": "CONFIG#PII_TYPES",
            "nombre": True,
            "email": False,
            "telefono": True,
            "celular": False,
            "direccion": True,
            "dni": True,
            "cuit_cuil": False,
            "tarjeta_credito": True,
            "cuenta_bancaria": False,
            "pasaporte": True,
            "fecha": False,
        }
        mock_resource = self._make_mock_table(item)

        result = get_active_types("user1", "test-table", mock_resource)

        expected = {"NOMBRE", "TELEFONO", "DIRECCION", "DNI",
                    "TARJETA_CREDITO", "PASAPORTE"}
        assert result == expected

    def test_user_without_config_returns_all_defaults(self) -> None:
        """Usuario sin configuración retorna todos los tipos activos."""
        mock_resource = self._make_mock_table(item=None)

        result = get_active_types("new_user", "test-table", mock_resource)

        assert result == set(PII_TYPE_MAPPING.values())

    def test_dynamodb_error_returns_all_defaults(self) -> None:
        """Si DynamoDB falla, retorna todos los tipos activos (fail-open)."""
        mock_table = MagicMock()
        mock_table.get_item.side_effect = Exception("DynamoDB timeout")
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        result = get_active_types("user1", "test-table", mock_resource)

        assert result == set(PII_TYPE_MAPPING.values())

    def test_config_with_missing_fields_defaults_to_true(self) -> None:
        """Campos ausentes en DynamoDB se asumen como True."""
        # Solo tiene algunos campos definidos
        item = {
            "PK": "USER#user1",
            "SK": "CONFIG#PII_TYPES",
            "nombre": False,
            "email": True,
            # Todos los demás campos no están presentes
        }
        mock_resource = self._make_mock_table(item)

        result = get_active_types("user1", "test-table", mock_resource)

        # nombre=False -> NOMBRE no está
        assert "NOMBRE" not in result
        # email=True -> EMAIL está
        assert "EMAIL" in result
        # Los demás defaultean a True
        assert "TELEFONO" in result
        assert "DNI" in result
        assert "CUIT_CUIL" in result

    def test_creates_boto3_resource_when_none(self) -> None:
        """Si no se pasa dynamodb_resource, crea uno con boto3."""
        mock_resource = MagicMock()
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_resource.Table.return_value = mock_table

        with patch("lambdas.redaction.config_filter.boto3") as mock_boto3:
            mock_boto3.resource.return_value = mock_resource
            result = get_active_types("user1", "test-table")

            mock_boto3.resource.assert_called_once_with("dynamodb")
            assert result == set(PII_TYPE_MAPPING.values())

    def test_type_mapping_is_uppercase(self) -> None:
        """Verifica que el mapeo produce tipos en MAYÚSCULAS."""
        for _config_key, upper_type in PII_TYPE_MAPPING.items():
            assert upper_type == upper_type.upper()

    def test_only_single_type_active(self) -> None:
        """Con solo un tipo activo, retorna set con un elemento."""
        item = {
            "PK": "USER#user1",
            "SK": "CONFIG#PII_TYPES",
            **{k: False for k in PII_TYPE_MAPPING},
        }
        item["dni"] = True
        mock_resource = self._make_mock_table(item)

        result = get_active_types("user1", "test-table", mock_resource)

        assert result == {"DNI"}


# ------------------------------------------------------------------
# Tests de integración: filter + get_active_types juntos
# ------------------------------------------------------------------


class TestConfigFilterIntegration:
    """Tests que validan el flujo completo de filtrado."""

    def test_full_flow_filters_correctly(self) -> None:
        """Flujo completo: leer config → filtrar entidades."""
        # Config: solo nombre y dni activos
        item = {
            "PK": "USER#user1",
            "SK": "CONFIG#PII_TYPES",
            **{k: False for k in PII_TYPE_MAPPING},
        }
        item["nombre"] = True
        item["dni"] = True

        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": item}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        # Entidades detectadas de varios tipos
        entities = [
            {"type": "NOMBRE", "text": "Juan Pérez"},
            {"type": "EMAIL", "text": "juan@mail.com"},
            {"type": "DNI", "text": "35.123.456"},
            {"type": "TELEFONO", "text": "011-4567-8901"},
            {"type": "CUIT_CUIL", "text": "20-12345678-1"},
        ]

        active_types = get_active_types("user1", "test-table", mock_resource)
        filtered = filter_entities_by_config(entities, active_types)

        assert len(filtered) == 2
        assert filtered[0]["type"] == "NOMBRE"
        assert filtered[1]["type"] == "DNI"
