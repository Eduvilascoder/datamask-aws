"""
Módulo de filtrado de entidades por configuración de usuario.

Lee la configuración PII del usuario desde DynamoDB y filtra
entidades detectadas para incluir solo los tipos activos.

Req 12.4: La detección respeta la configuración del usuario,
detectando y ofuscando únicamente los tipos activos.

Mapeo de nombres: las claves de configuración usan minúsculas
(nombre, email, etc.) mientras las entidades usan MAYÚSCULAS
(NOMBRE, EMAIL, etc.).
"""

import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# Los 11 tipos PII con su mapeo lowercase → UPPERCASE
PII_TYPE_MAPPING: dict[str, str] = {
    "nombre": "NOMBRE",
    "email": "EMAIL",
    "telefono": "TELEFONO",
    "celular": "CELULAR",
    "direccion": "DIRECCION",
    "dni": "DNI",
    "cuit_cuil": "CUIT_CUIL",
    "tarjeta_credito": "TARJETA_CREDITO",
    "cuenta_bancaria": "CUENTA_BANCARIA",
    "pasaporte": "PASAPORTE",
    "fecha": "FECHA",
}

# Configuración por defecto: todos los tipos activos
DEFAULT_CONFIG: dict[str, bool] = {key: True for key in PII_TYPE_MAPPING}


def is_macie_verification_enabled(
    user_id: str,
    table_name: str,
    dynamodb_resource: Any = None,
) -> bool:
    """Indica si el usuario habilitó la verificación con Macie.

    Lee el flag `macieVerification` de la config de detección
    (PK=USER#{userId}, SK=CONFIG#DETECTION). Fail-safe a False: si no existe
    o falla la lectura, la verificación NO se solicita.

    Args:
        user_id: ID del usuario propietario del documento.
        table_name: Nombre de la tabla DynamoDB.
        dynamodb_resource: Recurso boto3 (inyectable para tests).

    Returns:
        True si la verificación Macie está habilitada, False si no.
    """
    if not user_id or not table_name:
        return False

    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")

    try:
        table = dynamodb_resource.Table(table_name)
        response = table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": "CONFIG#DETECTION"}
        )
    except Exception:
        logger.exception("Error leyendo flag macieVerification — usando False")
        return False

    item = response.get("Item")
    if not item:
        return False
    return bool(item.get("macieVerification", False))


def get_active_types(
    user_id: str,
    table_name: str,
    dynamodb_resource: Any = None,
) -> set[str]:
    """Lee la configuración PII del usuario y retorna los tipos activos.

    Consulta DynamoDB por la configuración del usuario. Si no existe
    configuración o la lectura falla, retorna todos los tipos activos
    (fail-open para usabilidad).

    Args:
        user_id: ID del usuario propietario del documento.
        table_name: Nombre de la tabla DynamoDB.
        dynamodb_resource: Recurso boto3 de DynamoDB. Si es None,
                           se crea uno nuevo.

    Returns:
        Conjunto de nombres de tipo en MAYÚSCULAS que están activos
        en la configuración del usuario.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")

    config = _read_user_config(user_id, table_name, dynamodb_resource)

    active: set[str] = set()
    for config_key, upper_type in PII_TYPE_MAPPING.items():
        if config.get(config_key, True):
            active.add(upper_type)

    return active


def filter_entities_by_config(
    entities: list[dict[str, Any]],
    active_types: set[str],
) -> list[dict[str, Any]]:
    """Filtra entidades conservando solo tipos activos en la configuración.

    Cada entidad tiene un campo 'type' con el nombre del tipo en MAYÚSCULAS
    (e.g., 'NOMBRE', 'DNI'). Para los 11 tipos PII estándar se respeta el
    toggle del usuario (solo se conservan los activos). Los tipos
    personalizados (definidos por el usuario en reglas regex, p. ej.
    EXPEDIENTE_GDE, TOKEN_GITHUB) NO están en el mapeo estándar y se conservan
    siempre, porque su activación ya la controla la configuración de detección
    (la regla puede deshabilitarse allí).

    Args:
        entities: Lista de entidades detectadas (dicts con campo 'type').
        active_types: Conjunto de tipos estándar activos en MAYÚSCULAS.

    Returns:
        Lista filtrada de entidades.
    """
    standard_types = set(PII_TYPE_MAPPING.values())
    return [
        entity
        for entity in entities
        if entity.get("type", "") in active_types
        or entity.get("type", "") not in standard_types
    ]


def _read_user_config(
    user_id: str,
    table_name: str,
    dynamodb_resource: Any,
) -> dict[str, bool]:
    """Lee la configuración PII del usuario desde DynamoDB.

    Si el usuario no tiene configuración almacenada (nuevo usuario)
    o si la lectura falla, retorna la configuración por defecto
    con todos los tipos activos (fail-open).

    Args:
        user_id: ID del usuario.
        table_name: Nombre de la tabla DynamoDB.
        dynamodb_resource: Recurso boto3 de DynamoDB.

    Returns:
        Diccionario con cada tipo PII como clave y booleano como valor.
    """
    table = dynamodb_resource.Table(table_name)

    try:
        response = table.get_item(
            Key={
                "PK": f"USER#{user_id}",
                "SK": "CONFIG#PII_TYPES",
            }
        )
    except Exception:
        logger.exception(
            "Error al leer configuración PII del usuario %s — "
            "usando configuración por defecto",
            user_id,
        )
        return dict(DEFAULT_CONFIG)

    item = response.get("Item")
    if not item:
        logger.info(
            "Usuario %s sin configuración PII — usando defaults",
            user_id,
        )
        return dict(DEFAULT_CONFIG)

    # Extraer solo los campos de tipo PII del item
    config: dict[str, bool] = {}
    for config_key in PII_TYPE_MAPPING:
        config[config_key] = item.get(config_key, True)

    return config
