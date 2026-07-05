"""config 层单测：双 provider 探测、默认模型、密钥缺失行为、LLM 接线。"""

import pytest

from finance_agent.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "FINANCE_AGENT_PROVIDER",
                "FINANCE_AGENT_MODEL", "FINANCE_AGENT_MOCK", "FINANCE_AGENT_BASE_URL",
                "FINANCE_AGENT_SEARCH_MODEL", "FINANCE_AGENT_WEB_MAX_RESULTS"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_openai(monkeypatch):
    settings = Settings.from_env()
    assert settings.provider == "openai"
    assert settings.model == "gpt-5.5"
    assert settings.base_url is None
    assert settings.mock_mode is False


def test_openrouter_autodetected_when_only_its_key_present(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    settings = Settings.from_env()
    assert settings.provider == "openrouter"
    assert settings.api_key == "sk-or-xxx"
    assert settings.base_url == "https://openrouter.ai/api/v1"
    assert settings.model == "openai/gpt-5.5"      # OpenRouter 的模型名带厂商前缀
    assert settings.search_model == "openai/gpt-5.5"


def test_both_keys_default_openai_but_provider_overridable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-b")
    assert Settings.from_env().provider == "openai"
    monkeypatch.setenv("FINANCE_AGENT_PROVIDER", "openrouter")
    settings = Settings.from_env()
    assert settings.provider == "openrouter"
    assert settings.api_key == "sk-or-b"


def test_web_search_settings(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("FINANCE_AGENT_SEARCH_MODEL", "openai/gpt-5-mini")
    monkeypatch.setenv("FINANCE_AGENT_WEB_MAX_RESULTS", "3")
    settings = Settings.from_env()
    assert settings.search_model == "openai/gpt-5-mini"
    assert settings.web_max_results == 3


def test_missing_api_key_raises_with_both_options(monkeypatch):
    settings = Settings.from_env()
    with pytest.raises(RuntimeError) as exc_info:
        settings.require_api_key()
    assert "OPENAI_API_KEY" in str(exc_info.value)
    assert "OPENROUTER_API_KEY" in str(exc_info.value)


def test_mock_mode_skips_api_key(monkeypatch):
    monkeypatch.setenv("FINANCE_AGENT_MOCK", "1")
    settings = Settings.from_env()
    settings.require_api_key()  # 不应抛异常
    assert settings.mock_mode is True


def test_model_override(monkeypatch):
    monkeypatch.setenv("FINANCE_AGENT_MODEL", "gpt-5-mini")
    assert Settings.from_env().model == "gpt-5-mini"


def test_configure_llm_openrouter_switches_to_chat_completions(monkeypatch):
    import finance_agent.llm as llm

    calls = {}
    monkeypatch.setattr(llm, "set_default_openai_client",
                        lambda client, use_for_tracing: calls.update(client=client))
    monkeypatch.setattr(llm, "set_default_openai_api",
                        lambda api: calls.update(api=api))
    monkeypatch.setattr(llm, "set_tracing_disabled",
                        lambda flag: calls.update(tracing_off=flag))
    llm.configure_llm(Settings(provider="openrouter", api_key="sk-or-x",
                               base_url="https://openrouter.ai/api/v1"))
    assert calls["api"] == "chat_completions"
    assert calls["tracing_off"] is True
    assert str(calls["client"].base_url).startswith("https://openrouter.ai")


def test_configure_llm_openai_is_noop(monkeypatch):
    import finance_agent.llm as llm

    called = []
    monkeypatch.setattr(llm, "set_default_openai_api", lambda api: called.append(api))
    llm.configure_llm(Settings(provider="openai", api_key="sk-a"))
    assert called == []
