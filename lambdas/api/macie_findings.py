"""
Consulta de hallazgos de Amazon Macie para la pantalla "Monitoreo PII — Macie".

Lista los findings de PII generados por Macie sobre el bucket de documentos,
acotados a los del usuario actual (prefijo `ofuscados/{userId}/`), y un resumen
del estado de Macie en la cuenta.

Diseño resiliente: si Macie no está habilitado o la consulta falla, se devuelve
un estado claro (`macieEnabled=false`) en lugar de un error, para que la UI lo
explique al usuario.
"""

import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# Severidades de Macie a nivel descriptivo.
_SEVERITY_LABELS = {
    "High": "Alta",
    "Medium": "Media",
    "Low": "Baja",
}

# Máximo de findings a devolver por consulta.
_MAX_FINDINGS = 50


def _macie_client() -> Any:
    return boto3.client("macie2")


def get_macie_findings(user_id: str, bucket: str) -> dict[str, Any]:
    """Devuelve los hallazgos de Macie del usuario y el estado del servicio.

    Args:
        user_id: Usuario actual (para acotar al prefijo ofuscados/{userId}/).
        bucket: Bucket de documentos donde Macie escanea.

    Returns:
        Dict con:
        - macieEnabled (bool): si Macie está habilitado en la cuenta.
        - findings (list): hallazgos del usuario (puede estar vacía).
        - message (str): mensaje informativo cuando no hay datos/servicio.
    """
    client = _macie_client()

    # 1) ¿Macie está habilitado en la cuenta?
    try:
        session = client.get_macie_session()
        if session.get("status") != "ENABLED":
            return _disabled_response()
    except Exception:
        logger.info("Macie no habilitado o sin permisos para get_macie_session")
        return _disabled_response()

    # 2) Listar findings y filtrar por los prefijos del usuario (originales y
    #    ofuscados). Cada finding se etiqueta con su scope para diferenciarlos.
    prefixes = {
        f"originales/{user_id}/": "ORIGINALES",
        f"ofuscados/{user_id}/": "OFUSCADOS",
    }
    try:
        finding_ids = _list_finding_ids(client)
        if not finding_ids:
            return {
                "macieEnabled": True,
                "findings": [],
                "message": "Macie está habilitado. No hay hallazgos registrados.",
            }
        findings = _describe_findings(client, finding_ids, bucket, prefixes)
        return {
            "macieEnabled": True,
            "findings": findings,
            "message": "",
        }
    except Exception:
        logger.exception("Error consultando findings de Macie")
        return {
            "macieEnabled": True,
            "findings": [],
            "message": "No se pudieron obtener los hallazgos de Macie.",
        }


def _disabled_response() -> dict[str, Any]:
    return {
        "macieEnabled": False,
        "findings": [],
        "message": (
            "Amazon Macie no está habilitado en esta cuenta. Habilítelo y "
            "active la verificación en Configuración para ver hallazgos."
        ),
    }


def _list_finding_ids(client: Any) -> list[str]:
    """Lista IDs de findings de PII ordenados por fecha descendente."""
    response = client.list_findings(
        findingCriteria={
            "criterion": {
                "category": {"eq": ["CLASSIFICATION"]},
            }
        },
        sortCriteria={"attributeName": "updatedAt", "orderBy": "DESC"},
        maxResults=_MAX_FINDINGS,
    )
    return response.get("findingIds", [])


def _describe_findings(
    client: Any, finding_ids: list[str], bucket: str, prefixes: dict[str, str]
) -> list[dict[str, Any]]:
    """Obtiene el detalle de los findings y los filtra/etiqueta por scope.

    Solo conserva findings del bucket de documentos cuyo key empieza con
    alguno de los prefijos del usuario. A cada uno le asigna el `scope`
    (ORIGINALES u OFUSCADOS) para diferenciarlos en la UI/consola.
    """
    if not finding_ids:
        return []

    detail = client.get_findings(findingIds=finding_ids[:_MAX_FINDINGS])
    results: list[dict[str, Any]] = []

    for f in detail.get("findings", []):
        s3_obj = (
            f.get("resourcesAffected", {}).get("s3Object", {}) or {}
        )
        s3_bucket = (
            f.get("resourcesAffected", {}).get("s3Bucket", {}) or {}
        )
        key = s3_obj.get("key", "")
        # Solo findings del bucket de documentos.
        if s3_bucket.get("name") and s3_bucket.get("name") != bucket:
            continue
        # Determinar el scope según el prefijo; descartar si no es del usuario.
        scope = ""
        for prefix, scope_label in prefixes.items():
            if key.startswith(prefix):
                scope = scope_label
                break
        if not scope:
            continue

        severity = (f.get("severity", {}) or {}).get("description", "")
        results.append({
            "id": f.get("id", ""),
            "scope": scope,
            "type": f.get("type", ""),
            "title": f.get("title", ""),
            "severity": _SEVERITY_LABELS.get(severity, severity or "—"),
            "count": f.get("count", 0),
            "s3Key": key,
            "updatedAt": f.get("updatedAt", ""),
        })

    return results
