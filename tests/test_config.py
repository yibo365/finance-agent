"""config 层单测：OpenAI 兼容三元组、旧配置兼容、SettingsStore 运行时更新、LLM 接线。"""

import pytest

from finance_agent.config import Settings, SettingsStore


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # 屏蔽仓库 .env（本地开发配置了真实 key 时测试必须仍然确定）
    import finance_agent.config as config

    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_BASE_URL",
                "FINANCE_AGENT_MODEL", "FINANCE_AGENT_MOCK", "FINANCE_AGENT_BASE_URL",
                "FINANCE_AGENT_WEB_MAX_RESULTS", "TAVILY_API_KEY",
                "FINANCE_AGENT_JSON_MODE"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_openai_official(monkeypatch):
    settings = Settings.from_env()
    assert settings.base_url is None          # 空 = OpenAI 官方
    assert settings.model == "gpt-5.5"
    assert settings.mock_mode is False


def test_openai_compatible_gateway(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://my-gateway/v1")
    monkeypatch.setenv("FINANCE_AGENT_MODEL", "deepseek/deepseek-v4-pro")
    settings = Settings.from_env()
    assert settings.api_key == "sk-x"
    assert settings.base_url == "https://my-gateway/v1"
    assert settings.model == "deepseek/deepseek-v4-pro"


def test_legacy_openrouter_key_still_works(monkeypatch):
    # 兼容旧配置：只设 OPENROUTER_API_KEY 时自动采用其 key 与 base_url
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    settings = Settings.from_env()
    assert settings.api_key == "sk-or-xxx"
    assert settings.base_url == "https://openrouter.ai/api/v1"
    assert settings.model == "openai/gpt-5.5"  # OpenRouter 模型名带厂商前缀


def test_openai_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-b")
    settings = Settings.from_env()
    assert settings.api_key == "sk-a" and settings.base_url is None


def test_tavily_key_loaded(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    assert Settings.from_env().tavily_api_key == "tvly-x"


def test_missing_api_key_raises_with_guidance(monkeypatch):
    settings = Settings.from_env()
    with pytest.raises(RuntimeError) as exc_info:
        settings.require_api_key()
    assert "OPENAI_API_KEY" in str(exc_info.value)
    assert "设置" in str(exc_info.value)  # 指向 Web 设置弹窗


def test_mock_mode_skips_api_key(monkeypatch):
    monkeypatch.setenv("FINANCE_AGENT_MOCK", "1")
    settings = Settings.from_env()
    settings.require_api_key()  # 不应抛异常
    assert settings.mock_mode is True


# ---------- SettingsStore：运行时更新 + .env 持久化 ----------

def test_settings_store_updates_and_persists(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# 注释保留\nOPENAI_API_KEY=old\nFINANCE_AGENT_MOCK=1\n", encoding="utf-8")
    store = SettingsStore(Settings(api_key="old"), env_path=env)
    updated = store.update(api_key="new-key", base_url="https://gw/v1",
                           model="m1", tavily_api_key="tvly-1")
    assert updated is store.current
    assert store.current.api_key == "new-key"
    assert store.current.base_url == "https://gw/v1"
    content = env.read_text(encoding="utf-8")
    assert "# 注释保留" in content and "FINANCE_AGENT_MOCK=1" in content  # 其余行不动
    assert "OPENAI_API_KEY=new-key" in content
    assert "OPENAI_BASE_URL=https://gw/v1" in content
    assert "FINANCE_AGENT_MODEL=m1" in content and "TAVILY_API_KEY=tvly-1" in content


def test_settings_store_clears_base_url_with_empty_string(tmp_path):
    store = SettingsStore(Settings(base_url="https://gw/v1"), env_path=tmp_path / ".env")
    store.update(base_url="")
    assert store.current.base_url is None     # 清空 = 回到 OpenAI 官方


def test_settings_store_ignores_unknown_fields(tmp_path):
    store = SettingsStore(Settings(), env_path=tmp_path / ".env")
    store.update(model="m2", nonsense="x")    # 未知字段静默忽略
    assert store.current.model == "m2"


# ---------- LLM 接线 ----------

def test_get_model_gateway_binds_custom_client(monkeypatch):
    from agents import OpenAIChatCompletionsModel

    import finance_agent.llm as llm

    monkeypatch.setattr(llm, "set_tracing_disabled", lambda flag: None)
    settings = Settings(api_key="sk-or-x", base_url="https://openrouter.ai/api/v1",
                        model="deepseek/deepseek-v4-pro")
    model = llm.get_model(settings)
    assert isinstance(model, OpenAIChatCompletionsModel)
    assert model.model == "deepseek/deepseek-v4-pro"  # 原样透传，不剥前缀


def test_get_model_openai_direct_returns_plain_string(monkeypatch):
    import finance_agent.llm as llm

    assert llm.get_model(Settings(api_key="sk-a", model="gpt-5.5")) == "gpt-5.5"


def test_configure_llm_disables_tracing_for_gateway(monkeypatch):
    import finance_agent.llm as llm

    calls = []
    monkeypatch.setattr(llm, "set_tracing_disabled", lambda flag: calls.append(flag))
    llm.configure_llm(Settings(api_key="k", base_url="https://gw/v1"))
    assert calls == [True]
    calls.clear()
    llm.configure_llm(Settings(api_key="sk-a"))
    assert calls == []


def test_json_mode_default_and_override(monkeypatch):
    assert Settings.from_env().json_mode == "object"          # 网关兼容的最大公约数
    monkeypatch.setenv("FINANCE_AGENT_JSON_MODE", "schema")
    assert Settings.from_env().json_mode == "schema"
    monkeypatch.setenv("FINANCE_AGENT_JSON_MODE", "不合法")
    assert Settings.from_env().json_mode == "object"          # 非法值回落默认


def test_rewrite_response_format_modes():
    from finance_agent.llm import rewrite_response_format

    schema_rf = {"type": "json_schema", "json_schema": {"name": "X", "schema": {}}}
    # object：降级（Kimi/DeepSeek 只认 json_object）
    out = rewrite_response_format({"response_format": dict(schema_rf)}, "object")
    assert out["response_format"] == {"type": "json_object"}
    # off：整体移除
    out = rewrite_response_format({"response_format": dict(schema_rf)}, "off")
    assert "response_format" not in out
    # schema：原样透传
    out = rewrite_response_format({"response_format": dict(schema_rf)}, "schema")
    assert out["response_format"]["type"] == "json_schema"
    # 非 json_schema 请求（如 orchestrator 无 output_type）不受影响
    assert rewrite_response_format({"messages": []}, "object") == {"messages": []}


def test_output_schema_note_carries_contract():
    from finance_agent.contracts import EventList
    from finance_agent.llm import output_schema_note

    note = output_schema_note(EventList)
    assert "coverage_notes" in note and "单个 JSON 对象" in note
