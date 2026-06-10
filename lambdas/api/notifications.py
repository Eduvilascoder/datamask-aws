"""
Envío de notificaciones/alertas de la aplicación vía Amazon SNS.

El envío está gobernado por el flag `snsAlertsEnabled` de la configuración del
usuario (toggle en Configuración). Si está deshabilitado o no hay topic
configurado, no se publica nada (no-op silencioso).

Diseño resiliente: un fallo al publicar nunca interrumpe el flujo principal.
"""

import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)

ALERTS_TOPIC_ARN = os.environ.get("ALERTS_TOPIC_ARN", "")


def publish_alert(
    subject: str,
    message: str,
    enabled: bool,
    sns_client: Any = None,
) -> bool:
    """Publica un mensaje en el topic SNS de alertas si está habilitado.

    Args:
        subject: Asunto del mensaje (máx 100 chars; SNS lo exige).
        message: Cuerpo del mensaje.
        enabled: Resultado del flag `snsAlertsEnabled` del usuario.
        sns_client: Cliente boto3 SNS (inyectable para tests).

    Returns:
        True si se publicó, False si no (deshabilitado, sin topic o error).
    """
    if not enabled:
        logger.info("Alertas SNS deshabilitadas por configuración — no se envía")
        return False
    if not ALERTS_TOPIC_ARN:
        logger.warning("ALERTS_TOPIC_ARN no configurado — no se envía alerta")
        return False

    if sns_client is None:
        sns_client = boto3.client("sns")

    try:
        sns_client.publish(
            TopicArn=ALERTS_TOPIC_ARN,
            Subject=subject[:100],
            Message=message,
        )
        return True
    except Exception:
        logger.exception("Error publicando alerta SNS — se ignora")
        return False
