"""
Configuración del Lambda API Handler.

Carga variables de entorno necesarias para la operación del handler.
Principio: paths, endpoints y secrets siempre configurables via env vars.

La autenticación se resuelve con Amazon Cognito (federado con IAM Identity
Center vía SAML). API Gateway valida el JWT con un authorizer de Cognito User
Pools antes de invocar este Lambda; el middleware deriva el usuario del token.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Configuración inmutable de la aplicación cargada desde variables de entorno."""

    # S3
    documents_bucket: str

    # DynamoDB
    documents_table: str

    # Secrets Manager
    secrets_arn: str

    # Frontend
    frontend_url: str


def load_config() -> AppConfig:
    """
    Carga la configuración desde variables de entorno.

    Returns:
        AppConfig con todos los valores necesarios.
    """
    return AppConfig(
        documents_bucket=os.environ.get("DOCUMENTS_BUCKET", ""),
        documents_table=os.environ.get("DOCUMENTS_TABLE", ""),
        secrets_arn=os.environ.get("SECRETS_ARN", ""),
        frontend_url=os.environ.get("FRONTEND_URL", ""),
    )
