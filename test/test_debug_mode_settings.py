"""
Tests for the DEBUG_MODE auth-bypass guard (SEC-020).

Run with: pytest test/test_debug_mode_settings.py -v
"""
import logging

import pytest

from config import Settings, warn_if_debug_mode_enabled


def _build_settings(monkeypatch, debug_mode: str, environment: str) -> Settings:
    monkeypatch.setenv("DEBUG_MODE", debug_mode)
    monkeypatch.setenv("LOGFIRE_ENVIRONMENT", environment)
    return Settings()


class TestAuthDisabledByDebugMode:
    """DEBUG_MODE only disables auth outside the configured production environment."""

    def test_debug_mode_in_dev_environment_disables_auth(self, monkeypatch):
        app_settings = _build_settings(monkeypatch, "true", "dev")
        assert app_settings.security.debug_mode is True
        assert app_settings.is_production_environment() is False
        assert app_settings.auth_disabled_by_debug_mode() is True

    @pytest.mark.parametrize("environment", ["prod", "production", "Production", " PROD "])
    def test_debug_mode_in_production_environment_keeps_auth_enforced(self, monkeypatch, environment):
        app_settings = _build_settings(monkeypatch, "true", environment)
        assert app_settings.security.debug_mode is True
        assert app_settings.is_production_environment() is True
        assert app_settings.auth_disabled_by_debug_mode() is False

    def test_debug_mode_off_keeps_auth_enforced(self, monkeypatch):
        app_settings = _build_settings(monkeypatch, "false", "dev")
        assert app_settings.auth_disabled_by_debug_mode() is False


class TestStartupWarning:
    """warn_if_debug_mode_enabled logs a CRITICAL security warning whenever DEBUG_MODE is set."""

    def test_warns_critical_when_bypass_active(self, monkeypatch, caplog):
        app_settings = _build_settings(monkeypatch, "true", "dev")
        with caplog.at_level(logging.CRITICAL):
            assert warn_if_debug_mode_enabled(app_settings) is True
        assert any(
            record.levelno == logging.CRITICAL and "DEBUG_MODE" in record.getMessage()
            for record in caplog.records
        )

    def test_warns_critical_and_refuses_bypass_in_production(self, monkeypatch, caplog):
        app_settings = _build_settings(monkeypatch, "true", "production")
        with caplog.at_level(logging.CRITICAL):
            assert warn_if_debug_mode_enabled(app_settings) is False
        assert any(
            record.levelno == logging.CRITICAL and "Refusing to disable authentication" in record.getMessage()
            for record in caplog.records
        )

    def test_silent_when_debug_mode_off(self, monkeypatch, caplog):
        app_settings = _build_settings(monkeypatch, "false", "dev")
        with caplog.at_level(logging.CRITICAL):
            assert warn_if_debug_mode_enabled(app_settings) is False
        assert not caplog.records


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
