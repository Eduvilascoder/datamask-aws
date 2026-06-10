"""Tests para el envío de alertas SNS (notifications)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import notifications


class TestPublishAlert:
    """Comportamiento de publish_alert gobernado por el flag."""

    def test_disabled_does_not_publish(self):
        client = MagicMock()
        sent = notifications.publish_alert(
            "asunto", "msg", enabled=False, sns_client=client
        )
        assert sent is False
        client.publish.assert_not_called()

    def test_enabled_publishes(self, monkeypatch):
        monkeypatch.setattr(notifications, "ALERTS_TOPIC_ARN", "arn:aws:sns:x:y:t")
        client = MagicMock()
        sent = notifications.publish_alert(
            "asunto", "msg", enabled=True, sns_client=client
        )
        assert sent is True
        client.publish.assert_called_once()

    def test_no_topic_does_not_publish(self, monkeypatch):
        monkeypatch.setattr(notifications, "ALERTS_TOPIC_ARN", "")
        client = MagicMock()
        sent = notifications.publish_alert(
            "asunto", "msg", enabled=True, sns_client=client
        )
        assert sent is False
        client.publish.assert_not_called()

    def test_publish_error_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(notifications, "ALERTS_TOPIC_ARN", "arn:aws:sns:x:y:t")
        client = MagicMock()
        client.publish.side_effect = Exception("boom")
        sent = notifications.publish_alert(
            "asunto", "msg", enabled=True, sns_client=client
        )
        assert sent is False
