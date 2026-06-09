"""
Tests unitarios para metrics — publicación de métricas en CloudWatch.

Verifica:
- Publicación de ServiceInvocationError con namespace y dimensiones correctas
- Publicación de PipelineError con step correcto
- Manejo de errores de CloudWatch
- Formato de métricas (Unit, Value, Timestamp)
"""

from unittest.mock import MagicMock, call

import pytest
from botocore.exceptions import ClientError

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from metrics import publish_pipeline_error_metric, publish_service_error_metric


@pytest.fixture
def mock_cw_client() -> MagicMock:
    """Crea un mock de cliente CloudWatch."""
    client = MagicMock()
    client.put_metric_data.return_value = {}
    return client


class TestPublishServiceErrorMetric:
    """Tests para publish_service_error_metric."""

    def test_publishes_metric_with_correct_namespace(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Verifica namespace DataMask/{env}."""
        result = publish_service_error_metric(
            environment="dev",
            service_name="Textract",
            cloudwatch_client=mock_cw_client,
        )

        assert result is True
        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        assert call_kwargs["Namespace"] == "DataMask/dev"

    def test_publishes_metric_with_service_name_dimension(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Verifica dimensión ServiceName."""
        publish_service_error_metric(
            environment="prod",
            service_name="Textract",
            cloudwatch_client=mock_cw_client,
        )

        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        metric_data = call_kwargs["MetricData"][0]
        assert metric_data["MetricName"] == "ServiceInvocationError"
        assert metric_data["Value"] == 1.0
        assert metric_data["Unit"] == "Count"

        dimensions = metric_data["Dimensions"]
        assert {"Name": "ServiceName", "Value": "Textract"} in dimensions

    def test_includes_error_type_dimension_when_provided(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Incluye dimensión ErrorType cuando se proporciona."""
        publish_service_error_metric(
            environment="dev",
            service_name="Textract",
            error_type="TEXTRACT_TIMEOUT",
            cloudwatch_client=mock_cw_client,
        )

        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        dimensions = call_kwargs["MetricData"][0]["Dimensions"]
        assert {"Name": "ErrorType", "Value": "TEXTRACT_TIMEOUT"} in dimensions

    def test_omits_error_type_dimension_when_none(
        self, mock_cw_client: MagicMock
    ) -> None:
        """No incluye dimensión ErrorType cuando es None."""
        publish_service_error_metric(
            environment="dev",
            service_name="Comprehend",
            error_type=None,
            cloudwatch_client=mock_cw_client,
        )

        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        dimensions = call_kwargs["MetricData"][0]["Dimensions"]
        assert len(dimensions) == 1
        assert dimensions[0]["Name"] == "ServiceName"

    def test_returns_false_on_client_error(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Retorna False si CloudWatch lanza ClientError."""
        mock_cw_client.put_metric_data.side_effect = ClientError(
            error_response={"Error": {"Code": "InternalError", "Message": "Service unavailable"}},
            operation_name="PutMetricData",
        )

        result = publish_service_error_metric(
            environment="dev",
            service_name="Textract",
            cloudwatch_client=mock_cw_client,
        )

        assert result is False

    def test_returns_false_on_unexpected_exception(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Retorna False si ocurre un error inesperado."""
        mock_cw_client.put_metric_data.side_effect = RuntimeError("Network error")

        result = publish_service_error_metric(
            environment="dev",
            service_name="Bedrock",
            cloudwatch_client=mock_cw_client,
        )

        assert result is False

    def test_prod_environment_namespace(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Verifica namespace correcto para ambiente prod."""
        publish_service_error_metric(
            environment="prod",
            service_name="Comprehend",
            cloudwatch_client=mock_cw_client,
        )

        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        assert call_kwargs["Namespace"] == "DataMask/prod"


class TestPublishPipelineErrorMetric:
    """Tests para publish_pipeline_error_metric."""

    def test_publishes_pipeline_error_metric(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Verifica publicación correcta de PipelineError."""
        result = publish_pipeline_error_metric(
            environment="dev",
            error_step="TEXTRACT",
            cloudwatch_client=mock_cw_client,
        )

        assert result is True
        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        assert call_kwargs["Namespace"] == "DataMask/dev"

        metric_data = call_kwargs["MetricData"][0]
        assert metric_data["MetricName"] == "PipelineError"
        assert metric_data["Value"] == 1.0
        assert metric_data["Unit"] == "Count"

        dimensions = metric_data["Dimensions"]
        assert {"Name": "ErrorStep", "Value": "TEXTRACT"} in dimensions

    def test_unhandled_error_step(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Verifica step UNHANDLED para errores no manejados."""
        publish_pipeline_error_metric(
            environment="dev",
            error_step="UNHANDLED",
            cloudwatch_client=mock_cw_client,
        )

        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        dimensions = call_kwargs["MetricData"][0]["Dimensions"]
        assert {"Name": "ErrorStep", "Value": "UNHANDLED"} in dimensions

    def test_validation_error_step(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Verifica step VALIDATION para errores de validación."""
        publish_pipeline_error_metric(
            environment="prod",
            error_step="VALIDATION",
            cloudwatch_client=mock_cw_client,
        )

        call_kwargs = mock_cw_client.put_metric_data.call_args[1]
        assert call_kwargs["Namespace"] == "DataMask/prod"
        dimensions = call_kwargs["MetricData"][0]["Dimensions"]
        assert {"Name": "ErrorStep", "Value": "VALIDATION"} in dimensions

    def test_returns_false_on_client_error(
        self, mock_cw_client: MagicMock
    ) -> None:
        """Retorna False si CloudWatch lanza ClientError."""
        mock_cw_client.put_metric_data.side_effect = ClientError(
            error_response={"Error": {"Code": "InternalError", "Message": "Fail"}},
            operation_name="PutMetricData",
        )

        result = publish_pipeline_error_metric(
            environment="dev",
            error_step="TEXTRACT",
            cloudwatch_client=mock_cw_client,
        )

        assert result is False
