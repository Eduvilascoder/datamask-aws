"""
Creación de jobs de Amazon Macie para escanear PII en los buckets de DataMask.

Crea dos classification jobs ONE_TIME diferenciados:
  - "originales": escanea el prefijo originales/{userId}/ (datos sin ofuscar).
  - "ofuscados":  escanea el prefijo ofuscados/{userId}/ (resultado ofuscado).

Para que los hallazgos de ambos se distingan en la consola de Macie, cada job:
  - usa un NOMBRE con sufijo (-originales / -ofuscados),
  - lleva un TAG `DataMaskScope` (ORIGINALES / OFUSCADOS),
  - escribe ese scope también en el campo de la respuesta para la UI.

Diseño resiliente: si Macie no está habilitado o falla, se devuelve un estado
claro en lugar de un error duro.
"""

import logging
import re
import time
from typing import Any

import boto3

logger = logging.getLogger(__name__)

SCOPE_ORIGINALES = "ORIGINALES"
SCOPE_OFUSCADOS = "OFUSCADOS"

# Mapeo scope -> prefijo S3 base (se completa con el userId).
_SCOPE_PREFIX = {
    SCOPE_ORIGINALES: "originales/",
    SCOPE_OFUSCADOS: "ofuscados/",
}

_JOB_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_JOB_NAME_LEN = 64


def _macie_client() -> Any:
    return boto3.client("macie2")


def _job_name(scope: str, user_id: str) -> str:
    """Nombre de job único y válido, con sufijo de scope para diferenciarlo."""
    suffix = scope.lower()
    stamp = int(time.time())
    raw = f"datamask-{suffix}-{user_id}-{stamp}"
    return _JOB_NAME_RE.sub("-", raw)[:_MAX_JOB_NAME_LEN]


def create_scan_jobs(
    user_id: str,
    bucket: str,
    account_id: str,
    macie_client: Any = None,
) -> dict[str, Any]:
    """Crea los dos jobs de Macie (originales y ofuscados) para el usuario.

    Args:
        user_id: Usuario actual (acota el escaneo a sus prefijos).
        bucket: Bucket de documentos.
        account_id: Account ID dueño del bucket.
        macie_client: Cliente boto3 macie2 (inyectable para tests).

    Returns:
        Dict con:
        - macieEnabled (bool)
        - jobs (list): [{scope, jobId, jobName, prefix, status}]
        - message (str)
    """
    if not user_id or not bucket or not account_id:
        return {
            "macieEnabled": False,
            "jobs": [],
            "message": "Datos insuficientes para crear los jobs de Macie.",
        }

    if macie_client is None:
        macie_client = _macie_client()

    # Verificar que Macie esté habilitado antes de intentar crear jobs.
    try:
        session = macie_client.get_macie_session()
        if session.get("status") != "ENABLED":
            return _disabled()
    except Exception:
        logger.info("Macie no habilitado o sin permisos (get_macie_session)")
        return _disabled()

    jobs: list[dict[str, Any]] = []
    for scope in (SCOPE_ORIGINALES, SCOPE_OFUSCADOS):
        prefix = f"{_SCOPE_PREFIX[scope]}{user_id}/"
        job = _create_one_job(
            macie_client, scope, user_id, bucket, account_id, prefix
        )
        jobs.append(job)

    created = sum(1 for j in jobs if j["status"] == "CREATED")
    return {
        "macieEnabled": True,
        "jobs": jobs,
        "message": (
            f"Se crearon {created} de {len(jobs)} job(s) de Macie. "
            "Los resultados aparecerán en unos minutos."
        ),
    }


def _create_one_job(
    client: Any,
    scope: str,
    user_id: str,
    bucket: str,
    account_id: str,
    prefix: str,
) -> dict[str, Any]:
    """Crea un único job de Macie acotado a un prefijo, con tag de scope."""
    try:
        response = client.create_classification_job(
            jobType="ONE_TIME",
            name=_job_name(scope, user_id),
            description=f"DataMask {scope} — escaneo PII de {prefix}",
            tags={"DataMaskScope": scope, "DataMaskUser": user_id},
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
        return {
            "scope": scope,
            "jobId": response.get("jobId", ""),
            "jobName": response.get("jobName", ""),
            "prefix": prefix,
            "status": "CREATED",
        }
    except Exception as exc:
        logger.warning("No se pudo crear el job Macie (%s): %s", scope, exc)
        return {
            "scope": scope,
            "jobId": "",
            "jobName": "",
            "prefix": prefix,
            "status": "FAILED",
        }


def _disabled() -> dict[str, Any]:
    return {
        "macieEnabled": False,
        "jobs": [],
        "message": (
            "Amazon Macie no está habilitado en esta cuenta. Habilítelo para "
            "crear los jobs de escaneo."
        ),
    }
