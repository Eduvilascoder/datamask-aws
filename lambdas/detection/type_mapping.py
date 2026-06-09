"""
Mapeo de tipos de entidades entre AWS Comprehend y el sistema DataMask.

Este módulo define la correspondencia entre los tipos de PII
que reporta AWS Comprehend y los tipos internos del sistema.
Entidades con tipos no mapeados se descartan.
"""

# Mapeo de tipos Comprehend → tipos del Sistema
COMPREHEND_TYPE_MAP: dict[str, str] = {
    "NAME": "NOMBRE",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE": "TELEFONO",
    "ADDRESS": "DIRECCION",
    "CREDIT_DEBIT_NUMBER": "TARJETA_CREDITO",
    "BANK_ACCOUNT_NUMBER": "CUENTA_BANCARIA",
    "PASSPORT_NUMBER": "PASAPORTE",
    "DATE_TIME": "FECHA",
}


def map_comprehend_type(comprehend_type: str) -> str | None:
    """Mapea un tipo de entidad de Comprehend al tipo del sistema.

    Args:
        comprehend_type: Tipo de entidad reportado por Comprehend
                         (e.g., "NAME", "EMAIL_ADDRESS").

    Returns:
        Tipo mapeado del sistema (e.g., "NOMBRE", "EMAIL")
        o None si el tipo no tiene mapeo definido.
    """
    return COMPREHEND_TYPE_MAP.get(comprehend_type)
