"""Tests para la creación de jobs de Macie (macie_jobs)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import macie_jobs


class TestCreateScanJobs:
    """Comportamiento de create_scan_jobs."""

    def test_disabled_session(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "PAUSED"}
        result = macie_jobs.create_scan_jobs(
            "eduvilas", "bucket", "123456789012", macie_client=client
        )
        assert result["macieEnabled"] is False
        assert result["jobs"] == []
        client.create_classification_job.assert_not_called()

    def test_creates_two_jobs(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "ENABLED"}
        client.create_classification_job.side_effect = [
            {"jobId": "j-orig", "jobName": "n1"},
            {"jobId": "j-ofus", "jobName": "n2"},
        ]
        result = macie_jobs.create_scan_jobs(
            "eduvilas", "bucket", "123456789012", macie_client=client
        )
        assert result["macieEnabled"] is True
        assert len(result["jobs"]) == 2
        scopes = {j["scope"] for j in result["jobs"]}
        assert scopes == {"ORIGINALES", "OFUSCADOS"}
        assert all(j["status"] == "CREATED" for j in result["jobs"])

    def test_job_scopes_to_correct_prefix(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "ENABLED"}
        client.create_classification_job.return_value = {"jobId": "x", "jobName": "n"}
        macie_jobs.create_scan_jobs(
            "eduvilas", "bucket", "123456789012", macie_client=client
        )
        calls = client.create_classification_job.call_args_list
        prefixes = []
        tags = []
        for c in calls:
            term = c.kwargs["s3JobDefinition"]["scoping"]["includes"]["and"][0]
            prefixes.append(term["simpleScopeTerm"]["values"][0])
            tags.append(c.kwargs["tags"]["DataMaskScope"])
        assert "originales/eduvilas/" in prefixes
        assert "ofuscados/eduvilas/" in prefixes
        assert set(tags) == {"ORIGINALES", "OFUSCADOS"}

    def test_partial_failure_is_reported(self):
        client = MagicMock()
        client.get_macie_session.return_value = {"status": "ENABLED"}
        client.create_classification_job.side_effect = [
            {"jobId": "j1", "jobName": "n1"},
            Exception("limit exceeded"),
        ]
        result = macie_jobs.create_scan_jobs(
            "eduvilas", "bucket", "123456789012", macie_client=client
        )
        statuses = {j["scope"]: j["status"] for j in result["jobs"]}
        assert statuses["ORIGINALES"] == "CREATED"
        assert statuses["OFUSCADOS"] == "FAILED"

    def test_missing_inputs(self):
        result = macie_jobs.create_scan_jobs(
            "", "bucket", "123", macie_client=MagicMock()
        )
        assert result["macieEnabled"] is False
