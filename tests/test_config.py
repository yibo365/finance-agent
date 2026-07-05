"""config 层单测：环境变量注入与密钥缺失时的行为。"""

import pytest

from finance_agent.config import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FINANCE_AGENT_MODEL", raising=False)
    monkeypatch.delenv("FINANCE_AGENT_MOCK", raising=False)
    settings = Settings.from_env()
    assert settings.model == "gpt-5.5"
    assert settings.mock_mode is False


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FINANCE_AGENT_MOCK", raising=False)
    settings = Settings.from_env()
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        settings.require_api_key()


def test_mock_mode_skips_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FINANCE_AGENT_MOCK", "1")
    settings = Settings.from_env()
    settings.require_api_key()  # 不应抛异常
    assert settings.mock_mode is True


def test_model_override(monkeypatch):
    monkeypatch.setenv("FINANCE_AGENT_MODEL", "gpt-5-mini")
    settings = Settings.from_env()
    assert settings.model == "gpt-5-mini"
