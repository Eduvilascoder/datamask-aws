"""
Configuración avanzada de detección de PII, configurable por usuario.

Extiende la configuración de tipos PII con:
- Reglas regex deterministas editables (antes hardcodeadas en la Lambda
  detection). Cada regla: {type, pattern, enabled}.
- Modelo de Bedrock seleccionable.
- Prompt enviado al modelo, editable.
- Lista de entidades (valores literales) que el modelo debe ignorar.

Persiste en DynamoDB bajo PK=USER#{userId}, SK=CONFIG#DETECTION.
Si el usuario no tiene configuración, se usan los valores por defecto de
este módulo (que replican el comportamiento histórico, ahora sin hardcodear
en el pipeline de detección).
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from bedrock_models import (
    DEFAULT_BEDROCK_MODEL_ID,
    is_valid_model,
    list_available_models,
)

logger = logging.getLogger(__name__)

SK_DETECTION = "CONFIG#DETECTION"

# Métodos de ofuscación disponibles. Determinan qué motores de detección se
# aplican sobre cada documento:
#   - "ai":   solo IA (Amazon Bedrock)
#   - "regex": solo reglas regex deterministas
#   - "both": ambos motores, fusionados (comportamiento por defecto histórico)
DETECTION_METHOD_AI = "ai"
DETECTION_METHOD_REGEX = "regex"
DETECTION_METHOD_BOTH = "both"
VALID_DETECTION_METHODS = frozenset(
    {DETECTION_METHOD_AI, DETECTION_METHOD_REGEX, DETECTION_METHOD_BOTH}
)
DEFAULT_DETECTION_METHOD = DETECTION_METHOD_BOTH

# Temperatura por defecto del modelo (0.0 = determinista, recomendado para
# extracción precisa de PII). Configurable por el usuario en [0.0, 1.0].
DEFAULT_BEDROCK_TEMPERATURE = 0.0
MIN_BEDROCK_TEMPERATURE = 0.0
MAX_BEDROCK_TEMPERATURE = 1.0

# Reglas regex por defecto, editables por el usuario desde la UI.
# Las reglas de alta precisión vienen activas; NOMBRE y DIRECCION vienen
# desactivadas por defecto porque por regex son ruidosas (generan falsos
# positivos) y suelen detectarse mejor con el motor de IA / Comprehend.
# El usuario puede activarlas o ajustar el patrón.
DEFAULT_REGEX_RULES: list[dict[str, Any]] = [
    # --- Documentos / identificadores argentinos (alta precisión) ---
    {"type": "DNI", "pattern": r"\b\d{1,2}\.\d{3}\.\d{3}\b", "enabled": True},
    {
        "type": "CUIT_CUIL",
        "pattern": r"\b(20|23|24|27|30|33|34)[-]?\d{8}[-]?\d\b",
        "enabled": True,
    },
    {"type": "PASAPORTE", "pattern": r"\b[A-Z]{2,3}\d{6,7}\b", "enabled": True},
    # --- Email ---
    {
        "type": "EMAIL",
        "pattern": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "enabled": True,
    },
    # --- Teléfonos (múltiples formatos: país, área, separadores variados) ---
    # Fijo: con prefijo internacional/área opcional, 6-8 dígitos finales.
    # Boundaries: (?<![\d\-]) y (?![\d\-]) evitan matchear el tramo
    # "AÑO-NÚMERO" de un expediente GDE (ej. 2025-12345678 dentro de
    # EX-2025-12345678-APN-...#MEC), donde el número va rodeado de guiones.
    {
        "type": "TELEFONO",
        "pattern": (
            r"(?<![\d\-])(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)"
            r"\d{3,4}[\s.\-]?\d{4}(?![\d\-])"
        ),
        "enabled": True,
    },
    # Celular: incluye el 9 / 15 argentino y formatos +54 9 11 ...
    {
        "type": "CELULAR",
        "pattern": (
            r"(?<![\d\-])(?:\+?\d{1,3}[\s.\-]?)?(?:9[\s.\-]?)?"
            r"(?:\(?\d{2,4}\)?[\s.\-]?)?(?:15[\s.\-]?)?"
            r"\d{3,4}[\s.\-]?\d{4}(?![\d\-])"
        ),
        "enabled": False,
    },
    # --- Tarjetas de crédito/débito (13-16 dígitos, separadores opcionales) ---
    {
        "type": "TARJETA_CREDITO",
        "pattern": r"\b(?:\d[ \-]?){13,16}\b",
        "enabled": True,
    },
    # --- Cuentas bancarias ---
    # CBU/CVU Argentina: 22 dígitos.
    {"type": "CUENTA_BANCARIA", "pattern": r"\b\d{22}\b", "enabled": True},
    # IBAN (incluye formatos internacionales).
    {
        "type": "CUENTA_BANCARIA",
        "pattern": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
        "enabled": True,
    },
    # Cuenta US: routing (9 dígitos) y/o account number (6-17 dígitos).
    {
        "type": "CUENTA_BANCARIA",
        "pattern": r"\b\d{9}\b(?:[\s\-]?\d{6,17}\b)?",
        "enabled": False,
    },
    # CBU con alias (cuenta con guiones/puntos en bloques).
    # --- Nombres (ruidoso por regex; desactivado por defecto) ---
    # "Apellido, Nombre" o "Nombre Apellido" con palabras capitalizadas.
    {
        "type": "NOMBRE",
        "pattern": (
            r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+,\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
            r"(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*\b"
            r"|\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,2}\b"
        ),
        "enabled": False,
    },
    # --- Direcciones (ruidoso por regex; desactivado por defecto) ---
    # "Calle Nombre 1234", "Av. Corrientes 1234 Piso 5", con código postal.
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
    # --- Fecha (DD/MM/AAAA, DD-MM-AAAA, AAAA-MM-DD) ---
    {
        "type": "FECHA",
        "pattern": (
            r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
            r"|\d{4}[/\-]\d{1,2}[/\-]\d{1,2})\b"
        ),
        "enabled": False,
    },
    # --- Tipos personalizados ---
    # Teléfono Chile: +56 9 XXXX XXXX (celular) o +56 2 XXXX XXXX (fijo).
    {
        "type": "TELEFONO_CHILE",
        "pattern": (
            r"(?<!\d)(?:\+?56[\s.\-]?)?(?:9|2|\d{1,2})[\s.\-]?"
            r"\d{3,4}[\s.\-]?\d{4}(?!\d)"
        ),
        "enabled": False,
    },
    # Expediente: EXP-2024-123456, EXPEDIENTE 12345/2024, etc.
    {
        "type": "EXPEDIENTE",
        "pattern": (
            r"\b(?:EXP|EXPTE|EXPEDIENTE)[\s.\-N°º]*\d{1,6}[\-/]\d{2,4}"
            r"(?:[\-/]\d{1,6})?\b"
        ),
        "enabled": False,
    },
    # Número de serie: bloques alfanuméricos con guiones (XXXX-XXXX-XXXX).
    {
        "type": "NUMERO_SERIE",
        "pattern": r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]{4,}){2,}\b",
        "enabled": False,
    },
    # Token de GitHub (ghp_, gho_, ghu_, ghs_, ghr_ + 36 alfanuméricos).
    {
        "type": "TOKEN_GITHUB",
        "pattern": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
        "enabled": True,
    },
    # Código de trámite: TR-2024-000123, TRAMITE 123456, etc.
    {
        "type": "CODIGO_TRAMITE",
        "pattern": (
            r"\b(?:TR|TRAMITE|TRÁMITE|COD|CODIGO|CÓDIGO)[\s.\-N°º]*"
            r"\d{2,4}[\-/]?\d{3,8}\b"
        ),
        "enabled": False,
    },
    # Expediente GDE (Gestión Documental Electrónica, APN):
    # TIPO-AÑO-NÚMERO-ECOSISTEMA-REPARTICIÓN#MINISTERIO
    # Ej: EX-2025-12345678-APN-SCEYM#MEC (también IF-, NO-, PV-, RE-, etc.).
    # El bloque (?:-+\s*)+ tolera el campo de usuario vacío que GDE deja
    # como "- -" (ej. EX-2021-58954571- -APN-DGDMT#MT).
    {
        "type": "EXPEDIENTE_GDE",
        "pattern": r"\b[A-Z]{2,5}-\d{4}-\d{6,}(?:-+\s*)+[A-Z]{2,}-[A-Z0-9]+#[A-Z0-9]+\b",
        "enabled": True,
    },
]

# Prompt por defecto. Usa el marcador {text} que la Lambda detection
# reemplaza por el texto del documento. Editable por el usuario.
DEFAULT_BEDROCK_PROMPT = (
    "Sos un sistema experto en detección de datos personales sensibles "
    "(PII) en documentos en español argentino.\n\n"
    "Analizá el siguiente texto e identificá TODAS las entidades de "
    "datos personales que encuentres.\n\n"
    "Tipos de PII a detectar:\n"
    "- NOMBRE: Nombres y apellidos de personas\n"
    "- EMAIL: Direcciones de correo electrónico\n"
    "- TELEFONO: Números de teléfono fijo\n"
    "- CELULAR: Números de teléfono celular/móvil\n"
    "- DIRECCION: Direcciones físicas (calles, localidades, códigos postales)\n"
    "- DNI: Documento Nacional de Identidad argentino (7-8 dígitos)\n"
    "- CUIT_CUIL: Clave Única de Identificación Tributaria/Laboral\n"
    "- TARJETA_CREDITO: Números de tarjetas de crédito/débito\n"
    "- CUENTA_BANCARIA: Números de cuentas bancarias (CBU, CVU, alias)\n"
    "- PASAPORTE: Números de pasaporte argentino\n"
    "- FECHA: Fechas de nacimiento u otras fechas personales\n"
    "- EXPEDIENTE_GDE: número de expediente del sistema GDE de la "
    "Administración Pública argentina, con formato "
    "TIPO-AÑO-NÚMERO-ECOSISTEMA-REPARTICIÓN#MINISTERIO "
    "(ej: EX-2025-12345678-APN-SCEYM#MEC). El TIPO puede ser EX, IF, NO, "
    "PV, RE, entre otros. Detectá el identificador completo como una sola "
    "entidad.\n\n"
    "INSTRUCCIONES:\n"
    "1. Respondé ÚNICAMENTE con un JSON array válido.\n"
    "2. Cada elemento debe tener exactamente estos campos:\n"
    '   - "text": el texto exacto de la entidad tal como aparece en el documento\n'
    '   - "type": uno de los tipos listados arriba (en MAYÚSCULAS)\n'
    '   - "start_offset": la posición (índice basado en 0) donde comienza '
    "el texto de la entidad en el documento\n"
    "3. No incluyas explicaciones, solo el JSON array.\n"
    "4. Si no encontrás ninguna entidad, respondé con un array vacío: []\n"
    "5. Considerá el contexto argentino.\n\n"
    "TEXTO A ANALIZAR:\n"
    "---\n"
    "{text}\n"
    "---\n\n"
    "JSON array de entidades detectadas:"
)

# Lista por defecto de entidades a ignorar (valores literales). Vacía.
DEFAULT_IGNORE_ENTITIES: list[str] = []

# Límites de validación.
MAX_REGEX_RULES = 50
MAX_PATTERN_LENGTH = 500
MAX_PROMPT_LENGTH = 20000
MAX_IGNORE_ENTITIES = 200
MAX_IGNORE_VALUE_LENGTH = 200


def default_detection_config() -> dict[str, Any]:
    """Devuelve la configuración de detección por defecto."""
    return {
        "detectionMethod": DEFAULT_DETECTION_METHOD,
        "bedrockModelId": DEFAULT_BEDROCK_MODEL_ID,
        "bedrockTemperature": DEFAULT_BEDROCK_TEMPERATURE,
        "bedrockPrompt": DEFAULT_BEDROCK_PROMPT,
        "regexRules": [dict(rule) for rule in DEFAULT_REGEX_RULES],
        "ignoreEntities": list(DEFAULT_IGNORE_ENTITIES),
    }


def validate_detection_config(config: dict[str, Any]) -> tuple[bool, str]:
    """
    Valida la configuración de detección enviada por el usuario.

    Returns:
        (True, "") si es válida; (False, mensaje) si no.
    """
    import re

    # Método de ofuscación: define qué motores se aplican.
    method = config.get("detectionMethod", DEFAULT_DETECTION_METHOD)
    if method not in VALID_DETECTION_METHODS:
        return False, "El método de detección debe ser 'ai', 'regex' o 'both'"

    uses_ai = method in (DETECTION_METHOD_AI, DETECTION_METHOD_BOTH)
    uses_regex = method in (DETECTION_METHOD_REGEX, DETECTION_METHOD_BOTH)

    model_id = config.get("bedrockModelId", "")
    if not isinstance(model_id, str) or not model_id.strip():
        return False, "Debe seleccionar un modelo de Bedrock"
    # Rechazar modelos que no estén en la lista activa (excluye Legacy).
    if not is_valid_model(model_id):
        return False, "El modelo seleccionado no está disponible o es Legacy"

    # Temperatura: numérica y dentro del rango permitido.
    temperature = config.get("bedrockTemperature", DEFAULT_BEDROCK_TEMPERATURE)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return False, "La temperatura debe ser un número"
    if not (MIN_BEDROCK_TEMPERATURE <= float(temperature) <= MAX_BEDROCK_TEMPERATURE):
        return (
            False,
            f"La temperatura debe estar entre {MIN_BEDROCK_TEMPERATURE} y "
            f"{MAX_BEDROCK_TEMPERATURE}",
        )

    prompt = config.get("bedrockPrompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return False, "El prompt no puede estar vacío"
    if len(prompt) > MAX_PROMPT_LENGTH:
        return False, f"El prompt excede {MAX_PROMPT_LENGTH} caracteres"
    if "{text}" not in prompt:
        return False, "El prompt debe incluir el marcador {text}"

    rules = config.get("regexRules", [])
    if not isinstance(rules, list):
        return False, "regexRules debe ser una lista"
    if len(rules) > MAX_REGEX_RULES:
        return False, f"Máximo {MAX_REGEX_RULES} reglas regex"
    for rule in rules:
        if not isinstance(rule, dict):
            return False, "Cada regla regex debe ser un objeto"
        rtype = rule.get("type", "")
        pattern = rule.get("pattern", "")
        if not isinstance(rtype, str) or not rtype.strip():
            return False, "Cada regla regex requiere un 'type'"
        if not isinstance(pattern, str) or not pattern.strip():
            return False, f"La regla '{rtype}' requiere un patrón"
        if len(pattern) > MAX_PATTERN_LENGTH:
            return False, f"El patrón de '{rtype}' excede {MAX_PATTERN_LENGTH} caracteres"
        # Validar que el patrón compile (evita ReDoS por error y regex inválidas).
        try:
            re.compile(pattern)
        except re.error as exc:
            return False, f"Patrón inválido en '{rtype}': {exc}"

    # Si el método usa regex, debe existir al menos una regla habilitada.
    if uses_regex and not any(r.get("enabled", True) for r in rules):
        return (
            False,
            "El método seleccionado usa regex pero no hay ninguna regla activa",
        )
    # 'uses_ai' se valida implícitamente con el modelo/prompt (siempre presentes).
    _ = uses_ai

    ignore = config.get("ignoreEntities", [])
    if not isinstance(ignore, list):
        return False, "ignoreEntities debe ser una lista"
    if len(ignore) > MAX_IGNORE_ENTITIES:
        return False, f"Máximo {MAX_IGNORE_ENTITIES} entidades a ignorar"
    for value in ignore:
        if not isinstance(value, str):
            return False, "Cada entidad a ignorar debe ser texto"
        if len(value) > MAX_IGNORE_VALUE_LENGTH:
            return False, "Una entidad a ignorar excede el largo máximo"

    return True, ""


def get_detection_config(
    user_id: str, table_name: str, dynamodb_resource: Any
) -> dict[str, Any]:
    """
    Lee la configuración de detección del usuario desde DynamoDB.

    Si no existe, retorna los valores por defecto.
    """
    table = dynamodb_resource.Table(table_name)
    try:
        response = table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": SK_DETECTION}
        )
    except Exception:
        logger.exception("Error al leer configuración de detección")
        return default_detection_config()

    item = response.get("Item")
    if not item:
        return default_detection_config()

    defaults = default_detection_config()
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


def save_detection_config(
    user_id: str,
    config: dict[str, Any],
    table_name: str,
    dynamodb_resource: Any,
) -> bool:
    """Persiste la configuración de detección del usuario en DynamoDB."""
    table = dynamodb_resource.Table(table_name)
    item: dict[str, Any] = {
        "PK": f"USER#{user_id}",
        "SK": SK_DETECTION,
        "userId": user_id,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "detectionMethod": config.get(
            "detectionMethod", DEFAULT_DETECTION_METHOD
        ),
        "bedrockModelId": config["bedrockModelId"],
        "bedrockTemperature": Decimal(str(config.get("bedrockTemperature", DEFAULT_BEDROCK_TEMPERATURE))),
        "bedrockPrompt": config["bedrockPrompt"],
        "regexRules": config["regexRules"],
        "ignoreEntities": config["ignoreEntities"],
    }
    try:
        table.put_item(Item=item)
    except Exception:
        logger.exception("Error al persistir configuración de detección")
        return False
    return True
