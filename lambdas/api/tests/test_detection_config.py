"""Tests para la validación del método de ofuscación en detection_config."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import detection_config


def _valid_config(**overrides):
    """Config válida base con un modelo aceptado por is_valid_model."""
    cfg = detection_config.default_detection_config()
    cfg.update(overrides)
    return cfg


class TestDetectionMethodValidation:
    """Valida el campo detectionMethod en validate_detection_config."""

    def setup_method(self):
        # Forzar que cualquier modelId sea válido (evita llamar a Bedrock).
        self._patcher = patch.object(
            detection_config, "is_valid_model", return_value=True
        )
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def test_default_method_is_both(self):
        assert detection_config.default_detection_config()["detectionMethod"] == "both"

    def test_accepts_ai(self):
        valid, _ = detection_config.validate_detection_config(
            _valid_config(detectionMethod="ai")
        )
        assert valid

    def test_accepts_regex_with_active_rule(self):
        valid, _ = detection_config.validate_detection_config(
            _valid_config(detectionMethod="regex")
        )
        assert valid

    def test_accepts_both(self):
        valid, _ = detection_config.validate_detection_config(
            _valid_config(detectionMethod="both")
        )
        assert valid

    def test_rejects_invalid_method(self):
        valid, msg = detection_config.validate_detection_config(
            _valid_config(detectionMethod="magic")
        )
        assert not valid
        assert "método" in msg.lower()

    def test_regex_method_requires_active_rule(self):
        rules = [{"type": "DNI", "pattern": r"\d+", "enabled": False}]
        valid, msg = detection_config.validate_detection_config(
            _valid_config(detectionMethod="regex", regexRules=rules)
        )
        assert not valid
        assert "regex" in msg.lower()

    def test_ai_method_does_not_require_active_rule(self):
        rules = [{"type": "DNI", "pattern": r"\d+", "enabled": False}]
        valid, _ = detection_config.validate_detection_config(
            _valid_config(detectionMethod="ai", regexRules=rules)
        )
        assert valid


class TestMacieVerificationValidation:
    """Valida el flag macieVerification."""

    def setup_method(self):
        self._patcher = patch.object(
            detection_config, "is_valid_model", return_value=True
        )
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def test_default_is_false(self):
        assert (
            detection_config.default_detection_config()["macieVerification"]
            is False
        )

    def test_accepts_true(self):
        valid, _ = detection_config.validate_detection_config(
            _valid_config(macieVerification=True)
        )
        assert valid

    def test_accepts_false(self):
        valid, _ = detection_config.validate_detection_config(
            _valid_config(macieVerification=False)
        )
        assert valid

    def test_rejects_non_boolean(self):
        valid, msg = detection_config.validate_detection_config(
            _valid_config(macieVerification="yes")
        )
        assert not valid
        assert "macie" in msg.lower()
