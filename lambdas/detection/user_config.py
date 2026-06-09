"""
Lectura de la configuración de detección del usuario desde DynamoDB.

La Lambda detection aplica la configuración que el usuario define en la UI:
- regexRules: reglas regex editables (antes hardcodeadas).
- bedrockModelId: modelo de Bedrock seleccionado.
- bedrockPrompt: prompt enviado al modelo (incluye marcador {text}).
- ignoreEntities: valores literales que NO deben ofuscarse.

Se persiste en DynamoDB bajo PK=USER#{userId}, SK=CONFIG#DETECTION.
Si no existe configuración o la lectura falla, se usan defaults que replican
el comportamiento histórico (fail-open).
"""

import logging
from typing import Any

import boto3

logger = logging.getLogger()

SK_DETECTION = "CONFIG#DETECTION"

# Métodos de ofuscación: qué motores aplicar (ai | regex | both).
DETECTION_METHOD_AI = "ai"
DETECTION_METHOD_REGEX = "regex"
DETECTION_METHOD_BOTH = "both"
DEFAULT_DETECTION_METHOD = DETECTION_METHOD_BOTH

DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_BEDROCK_TEMPERATURE = 0.0

DEFAULT_REGEX_RULES: list[dict[str, Any]] = [
    {"type": "DNI", "pattern": r"\b\d{1,2}\.\d{3}\.\d{3}\b", "enabled": True},
    {
        "type": "CUIT_CUIL",
        "pattern": r"\b(20|23|24|27|30|33|34)[-]?\d{8}[-]?\d\b",
        "enabled": True,
    },
    {"type": "PASAPORTE", "pattern": r"\b[A-Z]{2,3}\d{6,7}\b", "enabled": True},
    {
        "type": "EMAIL",
        "pattern": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "enabled": True,
    },
    {
        "type": "TELEFONO",
        "pattern": (
            r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)"
            r"\d{3,4}[\s.\-]?\d{4}(?!\d)"
        ),
        "enabled": True,
    },
    {
        "type": "CELULAR",
        "pattern": (
            r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?(?:9[\s.\-]?)?"
            r"(?:\(?\d{2,4}\)?[\s.\-]?)?(?:15[\s.\-]?)?"
            r"\d{3,4}[\s.\-]?\d{4}(?!\d)"
        ),
        "enabled": False,
    },
    {
        "type": "TARJETA_CREDITO",
        "pattern": r"\b(?:\d[ \-]?){13,16}\b",
        "enabled": True,
    },
    {"type": "CUENTA_BANCARIA", "pattern": r"\b\d{22}\b", "enabled": True},
    {
        "type": "CUENTA_BANCARIA",
        "pattern": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
        "enabled": True,
    },
    {
        "type": "CUENTA_BANCARIA",
        "pattern": r"\b\d{9}\b(?:[\s\-]?\d{6,17}\b)?",
        "enabled": False,
    },
    {
        "type": "NOMBRE",
        "pattern": (
            r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+,\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
            r"(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*\b"
            r"|\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,2}\b"
        ),
        "enabled": False,
    },
    {
        "type": "DIRECCION",
        "pattern": (
            r"\b(?:Calle|Av\.?|Avenida|Pasaje|Pje\.?|Diag\.?|Ruta|Boulevard|Bv\.?)"
            r"\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ.]*(?:\s+[A-ZÁÉÍÓÚÑ0-9][\wáéíóúñ.]*)*"
            r"\s+\d{1,5}(?:\s*(?:Piso|Depto\.?|Dto\.?|Of\.?)\s*\w+)?"
            r"(?:\s*\(?[A-Z]?\d{4}[A-Z]{0,3}\)?)?\b"
        ),
        "enabled": False,
    },
    {
        "type": "FECHA",
        "pattern": (
            r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
            r"|\d{4}[/\-]\d{1,2}[/\-]\d{1,2})\b"
        ),
        "enabled": False,
    },
    {
        "type": "TELEFONO_CHILE",
        "pattern": (
            r"(?<!\d)(?:\+?56[\s.\-]?)?(?:9|2|\d{1,2})[\s.\-]?"
            r"\d{3,4}[\s.\-]?\d{4}(?!\d)"
        ),
        "enabled": False,
    },
    {
        "type": "EXPEDIENTE",
        "pattern": (
            r"\b(?:EXP|EXPTE|EXPEDIENTE)[\s.\-N°º]*\d{1,6}[\-/]\d{2,4}"
            r"(?:[\-/]\d{1,6})?\b"
        ),
        "enabled": False,
    },
    {
        "type": "NUMERO_SERIE",
        "pattern": r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]{4,}){2,}\b",
        "enabled": False,
    },
    {
        "type": "TOKEN_GITHUB",
        "pattern": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
        "enabled": True,
    },
    {
        "type": "CODIGO_TRAMITE",
        "pattern": (
            r"\b(?:TR|TRAMITE|TRÁMITE|COD|CODIGO|CÓDIGO)[\s.\-N°º]*"
            r"\d{2,4}[\-/]?\d{3,8}\b"
        ),
        "enabled": False,
    },
    {
        "type": "EXPEDIENTE_GDE",
        "pattern": r"\b[A-Z]{2,5}-\d{4}-\d{6,}-[A-Z]{2,}-[A-Z0-9]+#[A-Z]+\b",
        "enabled": True,
    },
]

DEFAULT_BEDROCK_PROMPT = (
    "Sos un sistema experto en detección de datos personales sensibles "
    "(PII) en documentos en español argentino.\n\n"
    "Analizá el siguiente texto e identificá TODAS las entidades de "
    "datos personales que encuentres.\n\n"
    "Tipos de PII a detectar:\n"
    "- NOMBRE, EMAIL, TELEFONO, CELULAR, DIRECCION, DNI, CUIT_CUIL,\n"
    "  TARJETA_CREDITO, CUENTA_BANCARIA, PASAPORTE, FECHA\n\n"
    "INSTRUCCIONES:\n"
    "1. Respondé ÚNICAMENTE con un JSON array válido.\n"
    "2. Cada elemento debe tener: \"text\", \"type\" (MAYÚSCULAS), "
    '"start_offset".\n'
    "3. No incluyas explicaciones, solo el JSON array.\n"
    "4. Si no encontrás nada, respondé [].\n\n"
    "TEXTO A ANALIZAR:\n---\n{text}\n---\n\n"
    "JSON array de entidades detectadas:"
)


def default_detection_config() -> dict[str, Any]:
    """Configuración por defecto (replica el comportamiento histórico)."""
    return {
        "detectionMethod": DEFAULT_DETECTION_METHOD,
        "bedrockModelId": DEFAULT_BEDROCK_MODEL_ID,
        "bedrockTemperature": DEFAULT_BEDROCK_TEMPERATURE,
        "bedrockPrompt": DEFAULT_BEDROCK_PROMPT,
        "regexRules": [dict(r) for r in DEFAULT_REGEX_RULES],
        "ignoreEntities": [],
    }


def get_detection_config(
    user_id: str,
    table_name: str,
    dynamodb_resource: Any = None,
) -> dict[str, Any]:
    """
    Lee la configuración de detección del usuario.

    Args:
        user_id: ID del usuario propietario del documento.
        table_name: Nombre de la tabla DynamoDB.
        dynamodb_resource: Recurso boto3 (inyectable para tests).

    Returns:
        Configuración de detección (con defaults si no existe).
    """
    if not user_id or not table_name:
        return default_detection_config()

    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")

    defaults = default_detection_config()
    try:
        table = dynamodb_resource.Table(table_name)
        response = table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": SK_DETECTION}
        )
    except Exception:
        logger.exception("Error leyendo config de detección — usando defaults")
        return defaults

    item = response.get("Item")
    if not item:
        return defaults

    raw_temp = item.get("bedrockTemperature", defaults["bedrockTemperature"])
    return {
        "detectionMethod": item.get(
            "detectionMethod", defaults["detectionMethod"]
        ),
        "bedrockModelId": item.get("bedrockModelId", defaults["bedrockModelId"]),
        "bedrockTemperature": float(raw_temp),
        "bedrockPrompt": item.get("bedrockPrompt", defaults["bedrockPrompt"]),
        "regexRules": item.get("regexRules", defaults["regexRules"]),
        "ignoreEntities": item.get("ignoreEntities", defaults["ignoreEntities"]),
    }
