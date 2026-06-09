"""Tests para el módulo config.py."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

# Agregar el directorio padre al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AppConfig, load_config


class TestLoadConfig:
    """Tests para load_config."""

    def test_loads_defaults_when_no_env(self) -> None:
        """Carga valores por defecto cuando las env vars no existen."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        assert config.documents_bucket == ""
        assert config.documents_table == ""
        assert config.secrets_arn == ""
        assert config.frontend_url == ""

    def test_loads_from_env_vars(self) -> None:
        """Carga valores de variables de entorno."""
        env = {
            "DOCUMENTS_BUCKET": "my-bucket",
            "DOCUMENTS_TABLE": "my-table",
            "SECRETS_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:x",
            "FRONTEND_URL": "https://app.com",
        }

        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        assert config.documents_bucket == "my-bucket"
        assert config.documents_table == "my-table"
        assert config.secrets_arn == "arn:aws:secretsmanager:us-east-1:123:secret:x"
        assert config.frontend_url == "https://app.com"


class TestAppConfig:
    """Tests para AppConfig dataclass."""

    def test_is_frozen(self) -> None:
        """AppConfig es inmutable."""
        config = AppConfig(
            documents_bucket="bucket",
            documents_table="table",
            secrets_arn="arn",
            frontend_url="https://app.com",
        )

        try:
            config.documents_bucket = "other"  # type: ignore
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass  # Esperado: dataclass frozen
