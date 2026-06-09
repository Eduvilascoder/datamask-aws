"""
Cliente de AWS Bedrock para detección contextual de PII.

Invoca un modelo LLM (Claude) para análisis contextual de datos
personales en texto en español argentino. Complementa la detección
determinista de Comprehend con capacidad de identificar PII contextual
(nombres compuestos, direcciones ambiguas, formatos locales).

Diseño resiliente: si Bedrock falla, timeout o responde con formato
inválido, retorna lista vacía y el pipeline continúa solo con
Comprehend + Regex.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

from models import DetectedEntity

logger = logging.getLogger(__name__)

# Constantes
MAX_TEXT_LENGTH = 10000
BEDROCK_TIMEOUT_SECONDS = 30
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Tipos PII válidos del sistema
VALID_PII_TYPES = frozenset([
    "NOMBRE",
    "EMAIL",
    "TELEFONO",
    "CELULAR",
    "DIRECCION",
    "DNI",
    "CUIT_CUIL",
    "TARJETA_CREDITO",
    "CUENTA_BANCARIA",
    "PASAPORTE",
    "FECHA",
    "EXPEDIENTE_GDE",
])


def invoke_bedrock(
    text: str,
    document_id: str = "",
    model_id: str | None = None,
    prompt_template: str | None = None,
    temperature: float = 0.0,
    bedrock_client: Any = None,
) -> list[DetectedEntity]:
    """Invoca Bedrock para detección contextual de PII.

    Construye un prompt con instrucciones para detectar datos personales
    en español argentino, envía el texto (truncado a 10000 chars) al
    modelo configurado, y parsea/valida la respuesta JSON.

    Args:
        text: Texto completo del documento a analizar.
        document_id: Identificador del documento (para logging).
        model_id: ID del modelo Bedrock. Si None, usa variable de entorno
                  BEDROCK_MODEL_ID o el default claude-3-haiku.
        prompt_template: Plantilla de prompt con marcador {text}. Si None,
                  usa el prompt por defecto.
        bedrock_client: Cliente boto3 de Bedrock Runtime (inyectable
                        para tests).

    Returns:
        Lista de DetectedEntity validadas con source="bedrock".
        Lista vacía si Bedrock falla, timeout o respuesta inválida.
    """
    if model_id is None:
        model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)

    # Truncar texto al máximo permitido
    truncated_text = text[:MAX_TEXT_LENGTH]

    try:
        # Crear cliente con timeout de 30 segundos
        if bedrock_client is None:
            bedrock_client = _create_bedrock_client()

        # Construir prompt (plantilla configurable o por defecto)
        prompt = _build_prompt(truncated_text, prompt_template)

        # Log de diagnóstico: confirmar qué prompt se está usando (sin volcar
        # el texto del documento). Indica si proviene de la config del usuario.
        _preview = (prompt_template or "")[:80].replace("\n", " ")
        logger.info(
            "Prompt usado: origen=%s, len_template=%d, preview=%r, model=%s",
            "config_usuario" if prompt_template else "default",
            len(prompt_template or ""),
            _preview,
            model_id,
        )

        # Invocar modelo
        response_body = _invoke_model(
            client=bedrock_client,
            model_id=model_id,
            prompt=prompt,
            temperature=temperature,
        )

        # Parsear respuesta JSON
        entities_raw = _parse_response(response_body)

        # Validar cada entidad
        validated_entities = _validate_entities(
            entities_raw=entities_raw,
            original_text=truncated_text,
        )

        logger.info(
            "Bedrock detectó %d entidades válidas para document_id=%s",
            len(validated_entities),
            document_id,
        )

        return validated_entities

    except Exception as e:
        _log_bedrock_failure(
            document_id=document_id,
            error=e,
        )
        return []


def _create_bedrock_client() -> Any:
    """Crea un cliente Bedrock Runtime con timeout configurado."""
    config = Config(
        read_timeout=BEDROCK_TIMEOUT_SECONDS,
        connect_timeout=BEDROCK_TIMEOUT_SECONDS,
        retries={"max_attempts": 0},
    )
    return boto3.client("bedrock-runtime", config=config)


def _build_prompt(text: str, prompt_template: str | None = None) -> str:
    """Construye el prompt para detección de PII a partir de la config.

    El prompt SIEMPRE proviene de la configuración del usuario (o del
    default configurable que persiste la API). No existe un prompt
    hardcodeado en runtime: si no se recibe plantilla, se lanza una
    excepción clara para que el fallo sea visible en CloudWatch y no se
    detecte PII con instrucciones implícitas.

    Args:
        text: Texto truncado a analizar.
        prompt_template: Plantilla configurada con marcador {text}.

    Returns:
        Prompt completo: la plantilla con {text} reemplazado por el texto.
        Si la plantilla no incluye {text}, se anexa el texto al final
        (fallback de formato, no de contenido).

    Raises:
        ValueError: Si no se provee plantilla de prompt.
    """
    if not prompt_template or not prompt_template.strip():
        raise ValueError(
            "No se recibió un prompt configurado. La detección por IA "
            "requiere el prompt definido en Configuración."
        )

    if "{text}" in prompt_template:
        return prompt_template.replace("{text}", text)

    # La plantilla no tiene el marcador {text}: anexar el texto al final
    # para no perder el contenido a analizar (fallback solo de formato).
    return f"{prompt_template}\n\nTEXTO A ANALIZAR:\n---\n{text}\n---"


def _invoke_model(
    client: Any,
    model_id: str,
    prompt: str,
    temperature: float = 0.0,
) -> str:
    """Invoca el modelo Bedrock vía la API Converse (unificada multi-proveedor).

    Usa `converse`, que funciona con cualquier proveedor de Bedrock
    (Anthropic, Amazon Nova, Meta Llama, Mistral, etc.) con un formato
    de request/response estándar. La temperatura va en inferenceConfig y
    solo se envía si el modelo la soporta (algunos modelos nuevos la
    rechazan).

    Args:
        client: Cliente boto3 de Bedrock Runtime.
        model_id: Identificador del modelo o inference profile.
        prompt: Prompt completo a enviar.
        temperature: Temperatura del modelo (0.0-1.0). 0.0 = determinista.

    Returns:
        Texto de la respuesta del modelo.

    Raises:
        Exception: Si la invocación falla o la respuesta es inválida.
    """
    safe_temperature = max(0.0, min(1.0, float(temperature)))

    converse_kwargs: dict[str, Any] = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "maxTokens": 4096,
            "temperature": safe_temperature,
        },
    }

    try:
        response = client.converse(**converse_kwargs)
    except Exception as exc:
        # Algunos modelos nuevos no soportan 'temperature' y la rechazan.
        # Reintentar sin el parámetro para no perder la detección por IA.
        if "temperature" in str(exc).lower():
            converse_kwargs["inferenceConfig"] = {"maxTokens": 4096}
            response = client.converse(**converse_kwargs)
        else:
            raise

    # Extraer texto de la respuesta estándar de Converse.
    blocks = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )
    response_text = "".join(b.get("text", "") for b in blocks)

    if not response_text.strip():
        raise ValueError("Respuesta de Bedrock vacía")

    return response_text.strip()


def _parse_response(response_text: str) -> list[dict[str, Any]]:
    """Parsea la respuesta de Bedrock como JSON array.

    Intenta extraer un JSON array de la respuesta, manejando
    posibles textos adicionales antes/después del JSON.

    Args:
        response_text: Texto crudo de la respuesta del modelo.

    Returns:
        Lista de diccionarios con entidades detectadas.

    Raises:
        ValueError: Si no se puede extraer un JSON array válido.
    """
    # Intentar parsear directamente
    try:
        result = json.loads(response_text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Intentar extraer JSON array del texto (puede haber texto extra)
    start_idx = response_text.find("[")
    end_idx = response_text.rfind("]")

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        raise ValueError(
            f"No se encontró JSON array válido en la respuesta: "
            f"{response_text[:200]}"
        )

    json_str = response_text[start_idx:end_idx + 1]

    try:
        result = json.loads(json_str)
        if isinstance(result, list):
            return result
        raise ValueError("El JSON parseado no es un array")
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Error parseando JSON array de Bedrock: {e}"
        ) from e


def _validate_entities(
    entities_raw: list[dict[str, Any]],
    original_text: str,
) -> list[DetectedEntity]:
    """Valida entidades de la respuesta de Bedrock localizándolas en el texto.

    Los LLM cuentan offsets de caracteres de forma poco confiable, por lo que
    NO se confía en el `start_offset` reportado: se busca el texto real de la
    entidad dentro del documento y se calcula el offset verdadero. Esto evita
    descartar detecciones correctas (direcciones, fechas, nombres) por un
    offset mal contado. Si el texto no aparece en el documento, la entidad se
    descarta (es una alucinación del modelo).

    Criterios de validación:
    - "text": string no vacío que efectivamente aparece en el documento.
    - "type": uno de los tipos PII válidos.

    Args:
        entities_raw: Lista de diccionarios crudos de la respuesta.
        original_text: Texto original donde se localizan las entidades.

    Returns:
        Lista de DetectedEntity validadas con source="bedrock" y offsets reales.
    """
    validated: list[DetectedEntity] = []
    text_lower = original_text.lower()

    for entity_data in entities_raw:
        if not isinstance(entity_data, dict):
            continue

        entity_text = entity_data.get("text")
        if not isinstance(entity_text, str) or not entity_text.strip():
            continue
        entity_text = entity_text.strip()

        entity_type = entity_data.get("type")
        if not isinstance(entity_type, str):
            continue
        entity_type = entity_type.upper()
        if entity_type not in VALID_PII_TYPES:
            continue

        # Localizar el texto real en el documento (no confiar en el offset
        # del modelo). Se prefiere la ocurrencia más cercana al offset que
        # reportó el LLM, pero el offset final siempre es el real.
        hint = entity_data.get("start_offset")
        hint = hint if isinstance(hint, int) and hint >= 0 else 0
        real_offset = _find_real_offset(
            original_text, text_lower, entity_text, hint
        )
        if real_offset is None:
            continue

        end_offset = real_offset + len(entity_text)
        entity = DetectedEntity(
            text=original_text[real_offset:end_offset],
            type=entity_type,
            page=1,  # Se asignará la página correcta en el handler
            start_offset=real_offset,
            end_offset=end_offset,
            source="bedrock",
            confidence=0.85,  # Confianza por defecto para Bedrock
        )
        validated.append(entity)

    return validated


def _find_real_offset(
    text: str,
    text_lower: str,
    entity_text: str,
    hint: int = 0,
) -> int | None:
    """Devuelve el offset real del texto de la entidad en el documento.

    Busca primero respetando mayúsculas/minúsculas y luego de forma
    case-insensitive. Entre varias ocurrencias, elige la más cercana al
    offset sugerido por el modelo (hint). Si el texto no aparece, retorna None.

    Args:
        text: Texto original del documento.
        text_lower: Texto del documento en minúsculas (precomputado).
        entity_text: Texto de la entidad a localizar.
        hint: Offset aproximado reportado por el modelo (para desempatar).

    Returns:
        Offset real (int) o None si el texto no se encuentra.
    """
    occurrences = _find_all(text, entity_text)
    if not occurrences:
        occurrences = _find_all(text_lower, entity_text.lower())
    if not occurrences:
        return None
    return min(occurrences, key=lambda pos: abs(pos - hint))


def _find_all(haystack: str, needle: str) -> list[int]:
    """Devuelve todas las posiciones de inicio de `needle` en `haystack`."""
    positions: list[int] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _validate_offset(
    text: str,
    entity_text: str,
    start_offset: int,
    tolerance: int = 5,
) -> bool:
    """Verifica que el offset coincide con la posición real del texto.

    Busca el entity_text en una ventana de ±tolerance caracteres
    alrededor del start_offset indicado.

    Args:
        text: Texto completo del documento.
        entity_text: Texto de la entidad a buscar.
        start_offset: Offset reportado por Bedrock.
        tolerance: Tolerancia en caracteres (±5 por defecto).

    Returns:
        True si el entity_text se encuentra en la ventana de tolerancia.
    """
    # Calcular ventana de búsqueda
    search_start = max(0, start_offset - tolerance)
    search_end = min(len(text), start_offset + len(entity_text) + tolerance)

    # Extraer ventana de texto
    search_window = text[search_start:search_end]

    # Verificar si el texto de la entidad está en la ventana
    return entity_text in search_window


def _log_bedrock_failure(
    document_id: str,
    error: Exception,
) -> None:
    """Registra un warning cuando Bedrock falla.

    Incluye document_id, tipo de error y timestamp como requiere
    el requisito 6.4.

    Args:
        document_id: Identificador del documento.
        error: Excepción que causó el fallo.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    error_type = type(error).__name__

    logger.warning(
        "Bedrock falló — continuando solo con Comprehend+Regex. "
        "document_id=%s, error_type=%s, error=%s, timestamp=%s",
        document_id,
        error_type,
        str(error),
        timestamp,
    )
