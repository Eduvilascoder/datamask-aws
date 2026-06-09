"""Tests unitarios para el módulo bedrock_client."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lambdas.detection.bedrock_client import (
    DEFAULT_MODEL_ID,
    MAX_TEXT_LENGTH,
    VALID_PII_TYPES,
    _build_prompt,
    _parse_response,
    _validate_entities,
    invoke_bedrock,
)
from lambdas.detection.models import DetectedEntity


def _make_bedrock_response(entities: list[dict]) -> dict:
    """Helper para crear respuesta mock de Bedrock (API Converse)."""
    response_text = json.dumps(entities)
    return {
        "output": {
            "message": {
                "content": [{"text": response_text}],
            }
        }
    }


def _make_converse_text_response(text: str) -> dict:
    """Helper para una respuesta Converse con texto arbitrario."""
    return {
        "output": {"message": {"content": [{"text": text}]}}
    }


# Plantilla de prompt de prueba (simula la config del usuario).
TEST_PROMPT = (
    "Detectá PII en el siguiente texto y respondé en JSON array.\n"
    "{text}"
)


def _make_stream_body(content: str) -> MagicMock:
    """Helper para simular StreamingBody de boto3."""
    mock_body = MagicMock()
    mock_body.read.return_value = content.encode("utf-8")
    return mock_body


class TestInvokeBedrock:
    """Tests para la función principal invoke_bedrock."""

    def test_returns_validated_entities(self):
        """Retorna entidades válidas de la respuesta de Bedrock."""
        text = "Juan Pérez vive en Buenos Aires"
        entities_response = [
            {"text": "Juan Pérez", "type": "NOMBRE", "start_offset": 0},
            {"text": "Buenos Aires", "type": "DIRECCION", "start_offset": 19},
        ]

        mock_client = MagicMock()
        mock_client.converse.return_value = _make_bedrock_response(
            entities_response
        )

        result = invoke_bedrock(
            text=text,
            document_id="doc-123",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        assert len(result) == 2
        assert result[0].text == "Juan Pérez"
        assert result[0].type == "NOMBRE"
        assert result[0].source == "bedrock"
        assert result[1].text == "Buenos Aires"
        assert result[1].type == "DIRECCION"

    def test_returns_empty_on_api_error(self):
        """Retorna lista vacía si Bedrock lanza excepción."""
        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("Service unavailable")

        result = invoke_bedrock(
            text="texto de prueba",
            document_id="doc-456",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        assert result == []

    def test_returns_empty_on_invalid_json_response(self):
        """Retorna lista vacía si la respuesta no es JSON válido."""
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_converse_text_response(
            "No puedo analizar esto"
        )

        result = invoke_bedrock(
            text="texto de prueba",
            document_id="doc-789",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        assert result == []

    def test_truncates_text_to_max_length(self):
        """Trunca el texto a MAX_TEXT_LENGTH caracteres."""
        long_text = "a" * 20000
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_bedrock_response([])

        invoke_bedrock(
            text=long_text,
            document_id="doc-long",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        # Verificar que el prompt contiene texto truncado
        call_args = mock_client.converse.call_args
        prompt_text = call_args.kwargs["messages"][0]["content"][0]["text"]
        # El texto de 20000 chars no debe aparecer completo
        assert "a" * 20000 not in prompt_text
        assert "a" * MAX_TEXT_LENGTH in prompt_text

    def test_uses_default_model_id(self):
        """Usa el model_id por defecto si no se especifica."""
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_bedrock_response([])

        invoke_bedrock(
            text="texto",
            document_id="doc-test",
            model_id=None,
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        call_args = mock_client.converse.call_args
        assert call_args.kwargs.get("modelId") == DEFAULT_MODEL_ID

    def test_uses_custom_model_id(self):
        """Usa un model_id personalizado si se especifica."""
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_bedrock_response([])

        custom_model = "anthropic.claude-3-sonnet-20240229-v1:0"
        invoke_bedrock(
            text="texto",
            document_id="doc-test",
            model_id=custom_model,
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        call_args = mock_client.converse.call_args
        assert call_args.kwargs.get("modelId") == custom_model

    def test_returns_empty_on_empty_response(self):
        """Retorna lista vacía si Bedrock responde con contenido vacío."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": []}}
        }

        result = invoke_bedrock(
            text="texto",
            document_id="doc-empty",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        assert result == []


class TestBuildPrompt:
    """Tests para la construcción del prompt (siempre desde config).

    Ya no existe un prompt hardcodeado: _build_prompt EXIGE una plantilla
    (la que el usuario define en Configuración). Sin plantilla, lanza error.
    """

    def test_requires_prompt_template(self):
        """Sin plantilla configurada, lanza ValueError (sin fallback)."""
        with pytest.raises(ValueError):
            _build_prompt("texto", None)

    def test_blank_prompt_template_raises(self):
        """Una plantilla en blanco también lanza ValueError."""
        with pytest.raises(ValueError):
            _build_prompt("texto", "   ")

    def test_replaces_text_marker(self):
        """Reemplaza el marcador {text} por el texto a analizar."""
        template = "Detectá PII en:\n{text}\nFin."
        text = "Juan Pérez, DNI 35.123.456"
        prompt = _build_prompt(text, template)

        assert text in prompt
        assert "{text}" not in prompt

    def test_appends_text_when_marker_missing(self):
        """Si la plantilla no tiene {text}, anexa el texto al final."""
        template = "Detectá todas las entidades PII."
        text = "Juan Pérez, DNI 35.123.456"
        prompt = _build_prompt(text, template)

        assert template in prompt
        assert text in prompt


class TestParseResponse:
    """Tests para el parseo de la respuesta JSON."""

    def test_parses_valid_json_array(self):
        """Parsea un JSON array válido directamente."""
        entities = [
            {"text": "Juan", "type": "NOMBRE", "start_offset": 0}
        ]
        result = _parse_response(json.dumps(entities))

        assert len(result) == 1
        assert result[0]["text"] == "Juan"

    def test_parses_empty_array(self):
        """Parsea un array vacío correctamente."""
        result = _parse_response("[]")
        assert result == []

    def test_extracts_json_from_text_with_prefix(self):
        """Extrae JSON array de texto con contenido extra antes."""
        response = 'Aquí están las entidades: [{"text": "Juan", "type": "NOMBRE", "start_offset": 0}]'
        result = _parse_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "Juan"

    def test_raises_on_no_json_array(self):
        """Lanza ValueError si no hay JSON array en la respuesta."""
        with pytest.raises(ValueError):
            _parse_response("No encontré datos personales en el texto.")

    def test_handles_json_object_by_extracting_inner_array(self):
        """Si el JSON es un objeto que contiene un array, extrae el array."""
        # '{"entities": []}' contiene [] — el parser lo extrae como array vacío
        result = _parse_response('{"entities": []}')
        assert result == []

    def test_raises_on_no_brackets_at_all(self):
        """Lanza ValueError si no hay corchetes en la respuesta."""
        with pytest.raises(ValueError):
            _parse_response('No hay datos personales en este texto.')

    def test_extracts_json_with_surrounding_text(self):
        """Extrae JSON array aunque esté rodeado de texto."""
        response = (
            'Las entidades detectadas son:\n'
            '[{"text": "María", "type": "NOMBRE", "start_offset": 5}]\n'
            'Fin del análisis.'
        )
        result = _parse_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "María"


class TestValidateEntities:
    """Tests para la validación de entidades."""

    def test_accepts_valid_entity(self):
        """Acepta entidad con todos los campos válidos."""
        text = "Juan Pérez vive aquí"
        entities = [
            {"text": "Juan Pérez", "type": "NOMBRE", "start_offset": 0},
        ]

        result = _validate_entities(entities, text)

        assert len(result) == 1
        assert result[0].text == "Juan Pérez"
        assert result[0].type == "NOMBRE"
        assert result[0].start_offset == 0
        assert result[0].end_offset == 10
        assert result[0].source == "bedrock"

    def test_discards_missing_text(self):
        """Descarta entidades sin campo 'text'."""
        entities = [
            {"type": "NOMBRE", "start_offset": 0},
        ]
        result = _validate_entities(entities, "Juan Pérez")
        assert result == []

    def test_discards_empty_text(self):
        """Descarta entidades con 'text' vacío."""
        entities = [
            {"text": "", "type": "NOMBRE", "start_offset": 0},
            {"text": "   ", "type": "NOMBRE", "start_offset": 0},
        ]
        result = _validate_entities(entities, "Juan Pérez")
        assert result == []

    def test_discards_invalid_type(self):
        """Descarta entidades con tipo no válido."""
        text = "algunos datos"
        entities = [
            {"text": "algunos", "type": "INVALIDO", "start_offset": 0},
            {"text": "datos", "type": "PERSONA", "start_offset": 8},
        ]
        result = _validate_entities(entities, text)
        assert result == []

    def test_normalizes_type_to_uppercase(self):
        """Normaliza el tipo a mayúsculas."""
        text = "Juan Pérez"
        entities = [
            {"text": "Juan Pérez", "type": "nombre", "start_offset": 0},
        ]
        result = _validate_entities(entities, text)

        assert len(result) == 1
        assert result[0].type == "NOMBRE"

    def test_negative_offset_hint_is_ignored(self):
        """Un offset negativo se ignora como hint; el texto real se localiza."""
        entities = [
            {"text": "Juan", "type": "NOMBRE", "start_offset": -1},
        ]
        result = _validate_entities(entities, "Juan Pérez")
        assert len(result) == 1
        assert result[0].start_offset == 0

    def test_discards_non_integer_offset(self):
        """Offset no entero se ignora como hint; el texto se localiza igual."""
        # El offset es solo una pista; aunque sea inválido, se busca el texto
        # real. "Juan" aparece en el documento, así que se valida.
        entities = [
            {"text": "Juan", "type": "NOMBRE", "start_offset": "0"},
            {"text": "Juan", "type": "NOMBRE", "start_offset": 1.5},
        ]
        result = _validate_entities(entities, "Juan Pérez")
        assert len(result) == 2
        assert all(e.start_offset == 0 for e in result)

    def test_relocates_mismatched_offset(self):
        """Reubica al offset real si el del modelo es incorrecto (no descarta)."""
        text = "Hola mundo, Juan Pérez aquí"
        entities = [
            # "Juan Pérez" empieza en 12; el modelo reporta 50 (mal contado).
            {"text": "Juan Pérez", "type": "NOMBRE", "start_offset": 50},
        ]
        result = _validate_entities(entities, text)
        assert len(result) == 1
        assert result[0].start_offset == 12
        assert result[0].end_offset == 12 + len("Juan Pérez")

    def test_discards_text_not_in_document(self):
        """Descarta entidades cuyo texto no aparece en el documento (alucinación)."""
        text = "Hola mundo, Juan Pérez aquí"
        entities = [
            {"text": "Carlos Gómez", "type": "NOMBRE", "start_offset": 0},
        ]
        result = _validate_entities(entities, text)
        assert result == []

    def test_relocates_with_offset_hint(self):
        """Usa el offset como pista para elegir la ocurrencia más cercana."""
        text = "Juan está aquí. Más texto. Juan otra vez."
        # Dos ocurrencias de "Juan": en 0 y en 27. El hint 25 elige la segunda.
        entities = [
            {"text": "Juan", "type": "NOMBRE", "start_offset": 25},
        ]
        result = _validate_entities(entities, text)
        assert len(result) == 1
        assert result[0].start_offset == 27

    def test_discards_non_dict_entries(self):
        """Descarta entradas que no son diccionarios."""
        entities = [
            "not a dict",
            42,
            None,
            {"text": "Juan", "type": "NOMBRE", "start_offset": 0},
        ]
        result = _validate_entities(entities, "Juan Pérez")

        assert len(result) == 1

    def test_all_valid_pii_types_accepted(self):
        """Todos los 11 tipos PII válidos son aceptados."""
        text = "test" * 100
        for pii_type in VALID_PII_TYPES:
            entities = [
                {"text": "test", "type": pii_type, "start_offset": 0},
            ]
            result = _validate_entities(entities, text)
            assert len(result) == 1, f"Tipo {pii_type} no fue aceptado"


class TestGracefulDegradation:
    """Tests para verificar degradación elegante."""

    @patch("lambdas.detection.bedrock_client.logger")
    def test_logs_warning_on_failure(self, mock_logger):
        """Registra warning con document_id y error_type al fallar."""
        mock_client = MagicMock()
        mock_client.converse.side_effect = TimeoutError("Timeout")

        result = invoke_bedrock(
            text="texto",
            document_id="doc-timeout",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        assert result == []
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Bedrock falló" in warning_msg

    def test_never_propagates_exceptions(self):
        """Nunca propaga excepciones — siempre retorna lista."""
        mock_client = MagicMock()
        mock_client.converse.side_effect = RuntimeError("Unexpected")

        # No debe lanzar excepción
        result = invoke_bedrock(
            text="texto",
            document_id="doc-error",
            prompt_template=TEST_PROMPT,
            bedrock_client=mock_client,
        )

        assert isinstance(result, list)
        assert result == []
