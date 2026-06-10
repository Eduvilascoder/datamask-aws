"""Tests para la consulta de hallazgos de Macie (macie_findings)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import macie_findings


def _patch_client(client):
    return patch.object(macie_findings, "_macie_client", return_value=client)


class TestGetMacieFindings:
    """Comportamiento de get_macie_findings."""

    def test_macie_disabled_session(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "PAUSED"}
        with _patch_client(client):
            result = macie_findings.get_macie_findings("eduvilas", "bucket")
        assert result["macieEnabled"] is False
        assert result["findings"] == []

    def test_macie_session_error_returns_disabled(self):
        client = MagicMock()
        client.get_macie_session.side_effect = Exception("not enabled")
        with _patch_client(client):
            result = macie_findings.get_macie_findings("eduvilas", "bucket")
        assert result["macieEnabled"] is False

    def test_enabled_no_findings(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "ENABLED"}
        client.list_findings.return_value = {"findingIds": []}
        with _patch_client(client):
            result = macie_findings.get_macie_findings("eduvilas", "bucket")
        assert result["macieEnabled"] is True
        assert result["findings"] == []

    def test_filters_by_user_prefix(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "ENABLED"}
        client.list_findings.return_value = {"findingIds": ["f1", "f2"]}
        client.get_findings.return_value = {
            "findings": [
                {
                    "id": "f1",
                    "type": "SensitiveData:S3Object/Personal",
                    "title": "PII detectada",
                    "severity": {"description": "High"},
                    "count": 3,
                    "updatedAt": "2025-01-01T00:00:00Z",
                    "resourcesAffected": {
                        "s3Bucket": {"name": "bucket"},
                        "s3Object": {"key": "ofuscados/eduvilas/doc1/f.pdf"},
                    },
                },
                {
                    "id": "f2",
                    "type": "SensitiveData",
                    "title": "Otro usuario",
                    "severity": {"description": "Low"},
                    "count": 1,
                    "updatedAt": "2025-01-01T00:00:00Z",
                    "resourcesAffected": {
                        "s3Bucket": {"name": "bucket"},
                        "s3Object": {"key": "ofuscados/otro/doc9/f.pdf"},
                    },
                },
            ]
        }
        with _patch_client(client):
            result = macie_findings.get_macie_findings("eduvilas", "bucket")

        assert result["macieEnabled"] is True
        assert len(result["findings"]) == 1
        assert result["findings"][0]["id"] == "f1"
        assert result["findings"][0]["severity"] == "Alta"
