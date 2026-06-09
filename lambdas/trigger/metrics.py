"""
Módulo de publicación de métricas custom en CloudWatch.

Responsabilidades:
- Publicar métricas de errores de invocación a servicios (Textract, etc.)
- Namespace: DataMask/{env}
- Métricas: ServiceInvocationError
- Dimensiones: ServiceName (e.g., Textract, Comprehend, Bedrock)

Permite monitoreo centralizado de fallos en servicios downstream y
activación de alarmas CloudWatch para notificación proactiva.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def publish_service_error_metric(
    environment: str,
    service_name: str,
    error_type: str | None = None,
    cloudwatch_client: Any = None,
) -> bool:
    """
    Publica una métrica de error de invocación de servicio en CloudWatch.

    Namespace: DataMask/{environment}
    MetricName: ServiceInvocationError
    Dimensions: ServiceName={service_name}

    Args:
        environment: Ambiente de ejecución (dev, prod).
        service_name: Nombre del servicio que falló (Textract, Comprehend, etc.).
        error_type: Tipo de error opcional para dimensión adicional.
        cloudwatch_client: Cliente boto3 CloudWatch (opcional).

    Returns:
        True si la métrica se publicó exitosamente, False en caso de error.
    """
    if cloudwatch_client is None:
        cloudwatch_client = boto3.client("cloudwatch")

    namespace = f"DataMask/{environment}"
    now = datetime.now(timezone.utc)

    dimensions = [
        {
            "Name": "ServiceName",
            "Value": service_name,
        },
    ]

    if error_type:
        dimensions.append(
            {
                "Name": "ErrorType",
                "Value": error_type,
            }
        )

    try:
        cloudwatch_client.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": "ServiceInvocationError",
                    "Dimensions": dimensions,
                    "Timestamp": now,
                    "Value": 1.0,
                    "Unit": "Count",
                },
            ],
        )
        logger.info(
            "Métrica ServiceInvocationError publicada: namespace=%s, "
            "service=%s, error_type=%s",
            namespace,
            service_name,
            error_type or "N/A",
        )
        return True
    except ClientError as e:
        logger.error(
            "Error publicando métrica en CloudWatch: %s",
            e.response["Error"]["Message"],
        )
        return False
    except Exception as e:
        logger.error(
            "Error inesperado publicando métrica: %s",
            str(e),
            exc_info=True,
        )
        return False


def publish_pipeline_error_metric(
    environment: str,
    error_step: str,
    cloudwatch_client: Any = None,
) -> bool:
    """
    Publica una métrica de error del pipeline en CloudWatch.

    Namespace: DataMask/{environment}
    MetricName: PipelineError
    Dimensions: ErrorStep={error_step}

    Args:
        environment: Ambiente de ejecución (dev, prod).
        error_step: Paso del pipeline donde ocurrió el error.
        cloudwatch_client: Cliente boto3 CloudWatch (opcional).

    Returns:
        True si la métrica se publicó exitosamente, False en caso de error.
    """
    if cloudwatch_client is None:
        cloudwatch_client = boto3.client("cloudwatch")

    namespace = f"DataMask/{environment}"
    now = datetime.now(timezone.utc)

    try:
        cloudwatch_client.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": "PipelineError",
                    "Dimensions": [
                        {
                            "Name": "ErrorStep",
                            "Value": error_step,
                        },
                    ],
                    "Timestamp": now,
                    "Value": 1.0,
                    "Unit": "Count",
                },
            ],
        )
        logger.info(
            "Métrica PipelineError publicada: namespace=%s, step=%s",
            namespace,
            error_step,
        )
        return True
    except ClientError as e:
        logger.error(
            "Error publicando métrica PipelineError en CloudWatch: %s",
            e.response["Error"]["Message"],
        )
        return False
    except Exception as e:
        logger.error(
            "Error inesperado publicando métrica PipelineError: %s",
            str(e),
            exc_info=True,
        )
        return False
