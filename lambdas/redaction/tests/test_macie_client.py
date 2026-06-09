"""Tests para la verificación opcional con Amazon Macie (macie_client)."""

from unittest.mock import MagicMock

from macie_client import (
    MACIE_REQUESTED,
    MACIE_UNAVAILABLE,
    verify_redacted_object,
    _job_name,
)


class TestVerifyRedactedObject:
    """Comportamiento de verify_redacted_object."""

    def test_creates_job_returns_requested(self):
        client = MagicMock()
        client.create_classification_job.return_value = {"jobId": "abc123"}

        status = verify_redacted_object(
            bucket="datamask-dev-documents-123",
            s3_key="ofuscados/user/doc1/file_ofuscado.pdf",
            document_id="doc1",
            account_id="123456789012",
            macie_client=client,
        )

        assert status == MACIE_REQUESTED
        client.create_classification_job.assert_called_once()

    def test_scopes_job_to_object_prefix(self):
        client = MagicMock()
        client.create_classification_job.return_value = {"jobId": "x"}

        verify_redacted_object(
            bucket="b",
            s3_key="ofuscados/user/doc1/file_ofuscado.pdf",
            document_id="doc1",
            account_id="123456789012",
            macie_client=client,
        )

        kwargs = client.create_classification_job.call_args.kwargs
        term = kwargs["s3JobDefinition"]["scoping"]["includes"]["and"][0]
        values = term["simpleScopeTerm"]["values"]
        assert values == ["ofuscados/user/doc1/"]

    def test_macie_unavailable_does_not_raise(self):
        client = MagicMock()
        client.create_classification_job.side_effect = Exception(
            "Macie is not enabled"
        )

        status = verify_redacted_object(
            bucket="b",
            s3_key="ofuscados/user/doc1/file.pdf",
            document_id="doc1",
            account_id="123456789012",
            macie_client=client,
        )

        assert status == MACIE_UNAVAILABLE

    def test_missing_inputs_returns_unavailable(self):
        status = verify_redacted_object(
            bucket="",
            s3_key="",
            document_id="doc1",
            account_id="",
            macie_client=MagicMock(),
        )
        assert status == MACIE_UNAVAILABLE


class TestJobName:
    """El nombre de job de Macie debe ser válido y acotado."""

    def test_sanitizes_invalid_chars(self):
        name = _job_name("doc/with#weird:chars")
        assert "/" not in name and "#" not in name and ":" not in name

    def test_truncates_long_name(self):
        name = _job_name("x" * 200)
        assert len(name) <= 64
