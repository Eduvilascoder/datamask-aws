"""
Verificación de PII residual con Amazon Macie (segunda capa de protección).

Tras ofuscar un documento, esta capa OPCIONAL lanza un job de descubrimiento
de datos sensibles de Macie acotado al objeto ofuscado en S3. Si Macie
encuentra PII residual, el documento queda marcado para revisión.

Diseño resiliente: si Macie no está habilitado en la cuenta, falla o no está
disponible, NO rompe el pipeline — se registra el estado y la ofuscación se
considera completada igual (la verificación es complementaria, no un gate).

El job de Macie es asíncrono: esta función lo CREA y devuelve su estado inicial
(QUEUED). Los hallazgos se consultan luego vía la consola/EventBridge o un
proceso aparte; acá solo se deja registrado que la verificación fue solicitada.
"""

import logging
import re
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# Estados de verificación Macie persistidos en la auditoría.
MACIE_DISABLED = "DISABLED"        # No solicitada (config off).
MACIE_REQUESTED = "REQUESTED"      # Job creado correctamente.
MACIE_UNAVAILABLE = "UNAVAILABLE"  # Macie no habilitado / error al crear job.

# Macie exige nombres de job con caracteres acotados.
_JOB_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_JOB_NAME_LEN = 64


def _job_name(document_id: str) -> str:
    """Construye un nombre de job válido y único para Macie."""
    safe = _JOB_NAME_RE.sub("-", f"datamask-verify-{document_id}")
    return safe[:_MAX_JOB_NAME_LEN]


def verify_redacted_object(
    bucket: str,
    s3_key: str,
    document_id: str,
    account_id: str,
    macie_client: Any = None,
) -> str:
    """Solicita a Macie un escaneo de PII sobre el objeto ofuscado.

    Crea un classification job de una sola ejecución (ONE_TIME) acotado al
    prefijo del documento ofuscado. No espera el resultado (es asíncrono).

    Args:
        bucket: Bucket S3 de documentos.
        s3_key: Key del PDF ofuscado a verificar.
        document_id: ID del documento (para nombrar el job).
        account_id: Account ID dueño del bucket (requerido por Macie).
        macie_client: Cliente boto3 de Macie2 (inyectable para tests).

    Returns:
        Estado de la verificación: MACIE_REQUESTED si el job se creó,
        MACIE_UNAVAILABLE si Macie no está disponible o falló.
    """
    if not bucket or not s3_key or not account_id:
        logger.warning("Datos insuficientes para verificación Macie — omitida")
        return MACIE_UNAVAILABLE

    if macie_client is None:
        macie_client = boto3.client("macie2")

    # Acotar el job al prefijo del documento ofuscado (no a todo el bucket).
    prefix = s3_key.rsplit("/", 1)[0] + "/" if "/" in s3_key else s3_key

    try:
        response = macie_client.create_classification_job(
            jobType="ONE_TIME",
            name=_job_name(document_id),
            s3JobDefinition={
                "bucketDefinitions": [
                    {"accountId": account_id, "buckets": [bucket]}
                ],
                "scoping": {
                    "includes": {
                        "and": [
                            {
                                "simpleScopeTerm": {
                                    "comparator": "STARTS_WITH",
                                    "key": "OBJECT_KEY",
                                    "values": [prefix],
                                }
                            }
                        ]
                    }
                },
            },
        )
        logger.info(
            "Macie: job de verificación creado para %s (jobId=%s)",
            document_id,
            response.get("jobId", "?"),
        )
        return MACIE_REQUESTED

    except Exception as exc:
        # Macie no habilitado, sin permisos, o error transitorio: no abortar.
        logger.warning(
            "Macie no disponible — verificación omitida (document=%s): %s",
            document_id,
            str(exc),
        )
        return MACIE_UNAVAILABLE
