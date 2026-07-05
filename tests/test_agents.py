"""agent 层单测（不调 LLM）：权限矩阵、动态 prompt、工具 impl、契约模型。"""

import json

import pytest
from agents import RunContextWrapper, WebSearchTool
from pydantic import ValidationError

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import TaskBrief
from finance_agent.orchestrator import _instructions, build_orchestrator
from finance_agent.subagents.alignment_analyst import build_alignment_analyst
from finance_agent.subagents.data_collector import build_data_collector
from finance_agent.subagents.event_researcher import build_event_researcher
from finance_agent.subagents.report_builder import build_report_builder
from finance_agent.tools.agent_tools import (
    detect_changepoints_impl,
    fetch_market_data_impl,
    list_artifacts_impl,
    list_skills_impl,
    search_hn_impl,
)
from finance_agent.tools.market import FetchError
from finance_agent.workspace import Workspace

MOCK = Settings(mock_mode=True)
LIVE = Settings(api_key="k")
OPENROUTER = Settings(
    provider="openrouter", api_key="sk-or-k",
    base_url="https://openrouter.ai/api/v1",
    model="openai/gpt-5.5", search_model="openai/gpt-5-mini", web_max_results=3,
)


@pytest.fixture()
def app(tmp_path):
    return AppContext(settings=MOCK, workspace=Workspace.create(tmp_path / "outputs"))


def tool_names(agent):
    return {getattr(t, "name", type(t).__name__) for t in agent.tools}


# ---------- 权限矩阵（architecture-and-flow §5 的可执行版本） ----------

def test_permission_matrix_data_collector():
    assert tool_names(build_data_collector(MOCK)) == {
        "fetch_market_data", "run_changepoint_detection",
    }


def test_permission_matrix_event_researcher():
    assert tool_names(build_event_researcher(MOCK)) == {
        "search_hn_news", "search_yahoo_finance_news",
    }
    live = build_event_researcher(LIVE)
    assert any(isinstance(t, WebSearchTool) for t in live.tools)


def test_event_researcher_openrouter_uses_web_plugin_tool():
    # OpenRouter 无 Responses API 托管搜索 → 换用 web 插件 function tool
    agent = build_event_researcher(OPENROUTER)
    assert "web_search" in tool_names(agent)
    assert not any(isinstance(t, WebSearchTool) for t in agent.tools)


def test_permission_matrix_alignment_analyst_has_no_tools():
    assert build_alignment_analyst(MOCK).tools == []


def test_permission_matrix_report_builder():
    assert tool_names(build_report_builder(MOCK)) == {
        "list_skills", "load_skill", "list_artifacts",
        "read_artifact", "render_artifact", "update_artifact",
    }


def test_permission_matrix_orchestrator():
    # 调度 + 只读工具；没有任何写产物/碰网络的直接能力
    assert tool_names(build_orchestrator(MOCK)) == {
        "run_data_collector", "run_event_researcher",
        "run_alignment_analyst", "run_report_builder",
        "list_skills", "list_artifacts", "read_artifact",
    }


def test_subagent_prompts_carry_today():
    # 真实事故：DeepSeek 把"近五年"算成 2020-2025（时间基准停在训练数据年代），
    # 而 subagent prompt 里没有今天日期、回声校验无从发现。全员注入。
    from datetime import date

    today = date.today().isoformat()
    for build in (build_data_collector, build_event_researcher,
                  build_alignment_analyst, build_report_builder):
        assert today in build(MOCK).instructions


def test_subagents_declare_structured_output():
    for build in (build_data_collector, build_event_researcher,
                  build_alignment_analyst, build_report_builder):
        assert build(MOCK).output_type is not None


def test_openrouter_model_name_passthrough():
    """OpenRouter 模型名必须原样透传（如 deepseek/deepseek-v4-pro）。

    不能走 SDK MultiProvider 前缀解析——它只认少数前缀且会剥离前缀，
    真实事故：'Unknown prefix: deepseek'。
    """
    from agents import OpenAIChatCompletionsModel

    deepseek = Settings(provider="openrouter", api_key="k",
                        base_url="https://openrouter.ai/api/v1",
                        model="deepseek/deepseek-v4-pro")
    for build in (build_data_collector, build_event_researcher, build_alignment_analyst,
                  build_report_builder, build_orchestrator):
        model = build(deepseek).model
        assert isinstance(model, OpenAIChatCompletionsModel)
        assert model.model == "deepseek/deepseek-v4-pro"
    # OpenAI 直连仍走 SDK 默认（字符串）
    assert isinstance(build_data_collector(LIVE).model, str)


def test_all_agents_cap_output_tokens():
    # 控成本 + OpenRouter 按 max_tokens 做预算检查（未设置时按模型最大值预留，低额度 key 会 402）
    for build in (build_data_collector, build_event_researcher, build_alignment_analyst,
                  build_report_builder, build_orchestrator):
        agent = build(MOCK)
        assert agent.model_settings.max_tokens == MOCK.max_output_tokens


# ---------- 动态 instructions ----------

def test_orchestrator_instructions_inject_workspace_state(app):
    ctx = RunContextWrapper(context=app)
    text = _instructions(ctx, build_orchestrator(MOCK))
    assert "意图路由" in text and "TaskBrief" in text and "终检清单" in text
    assert "kline-html-report" in text          # skill 索引已注入
    assert app.workspace.session_id in text
    assert "（暂无）" in text                    # 空工作区提示


# ---------- 工具 impl（mock 模式，不碰网络） ----------

def test_fetch_market_data_impl_mock_uses_seed_and_registers_dataset(app):
    out = fetch_market_data_impl(app, "NVDA", "2022-01-01", "2023-01-01")
    assert out["dataset_id"] == "ds-nvda-20220101-20230101"
    assert 200 < out["rows"] < 300
    assert out["dataset_id"] in app.workspace.dataset_index()
    assert out["evidence_id"].startswith("ev-")


def test_fetch_market_data_impl_mock_rejects_unseeded_ticker(app):
    with pytest.raises(FetchError, match="仅有 NVDA 种子"):
        fetch_market_data_impl(app, "GC=F", "2022-01-01", "2023-01-01")


def test_detect_changepoints_impl_reports_filtering(app):
    ds = fetch_market_data_impl(app, "NVDA", "2022-01-01", "2024-01-01")["dataset_id"]
    out = detect_changepoints_impl(app, ds, min_severity=2)
    assert out["total_detected"] >= out["returned"] > 0
    assert all(cp["severity"] >= 2 for cp in out["changepoints"])
    assert out["evidence_id"].startswith("ev-")


def test_detect_changepoints_impl_caps_output(app):
    # 大列表原样穿过 LLM 输出会撞 max_tokens 截断（真实事故），必须有确定性硬上限
    ds = fetch_market_data_impl(app, "NVDA", "2021-07-04", "2026-07-02")["dataset_id"]
    out = detect_changepoints_impl(app, ds, min_severity=1, max_points=30)
    assert out["returned"] == 30
    assert out["omitted"] == out["after_min_severity"] - 30 > 0
    dates = [cp["date"] for cp in out["changepoints"]]
    assert dates == sorted(dates)  # 截取后仍按时间升序，便于阅读
    # 截取偏向高 severity：省略的都不高于保留的最低 severity
    assert min(cp["severity"] for cp in out["changepoints"]) >= 1


def test_search_hn_impl_rejects_boolean_and_long_queries(app):
    # Algolia 不支持 OR 语法（真实事故：12 连败零命中），工具侧确定性拒绝并指导改法
    with pytest.raises(ValueError, match="1-2 个词"):
        search_hn_impl(app, "NVIDIA OR ChatGPT OR DeepSeek", "2022-01-01", "2022-12-31")
    with pytest.raises(ValueError, match="1-2 个词"):
        search_hn_impl(app, "nvidia gpu export control policy news", "2022-01-01", "2022-12-31")


def test_search_hn_impl_mock_filters_by_window(app):
    out = search_hn_impl(app, "chatgpt", "2022-11-01", "2022-12-15")
    assert out["mock"] is True
    assert [item["title"] for item in out["items"]] == [
        "ChatGPT: Optimizing Language Models for Dialogue"
    ]


def test_list_impls(app):
    skills = list_skills_impl(app)
    assert any("kline-html-report" in line for line in skills["skills"])
    listing = list_artifacts_impl(app)
    assert listing["session_id"] == app.workspace.session_id
    assert listing["artifacts"] == []


# ---------- OpenRouter 联网搜索 impl ----------

def test_openrouter_web_search_impl_parses_citations_and_records_evidence(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from finance_agent.tools.agent_tools import openrouter_web_search_impl

    app = AppContext(settings=OPENROUTER, workspace=Workspace.create(tmp_path / "o"))
    captured = {}

    class StubCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(
                content="DeepSeek-R1 于 2025-01-20 发布。",
                annotations=[{
                    "type": "url_citation",
                    "url_citation": {"title": "Reuters 报道", "url": "https://reuters.com/x"},
                }],
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    stub = SimpleNamespace(chat=SimpleNamespace(completions=StubCompletions()))
    out = asyncio.run(openrouter_web_search_impl(app, "deepseek r1 发布日期", client=stub))

    assert captured["model"] == "openai/gpt-5-mini"
    assert captured["extra_body"] == {"plugins": [{"id": "web", "max_results": 3}]}
    assert out["citations"] == [{"title": "Reuters 报道", "url": "https://reuters.com/x"}]
    assert out["evidence_id"].startswith("ev-")
    recorded = app.workspace.evidence.get(out["evidence_id"])
    assert recorded.kind == "search"
    assert recorded.source_url == "https://reuters.com/x"


# ---------- 结构化输出 schema 兼容性 ----------

def test_llm_facing_schemas_have_no_prefix_items():
    """OpenAI 结构化输出不支持 prefixItems（定长 tuple 的 schema 形态）。

    所有面向 LLM 的 schema——subagent 的 output_type 与 ArtifactSpec/TaskBrief
    工具参数——都不得含 prefixItems。真实事故：MarketData 里的 tuple[str,str]
    曾导致 API 400 'array schema missing items'。
    """
    import json as json_mod

    from finance_agent.artifacts.spec import ArtifactSpec
    from finance_agent.contracts import (
        AlignmentMatrix,
        ArtifactRefs,
        EventList,
        MarketData,
    )

    for model in (MarketData, EventList, AlignmentMatrix, ArtifactRefs,
                  TaskBrief, ArtifactSpec):
        schema = json_mod.dumps(model.model_json_schema())
        assert "prefixItems" not in schema, f"{model.__name__} 的 schema 含 prefixItems"


# ---------- 契约 ----------

def test_taskbrief_requires_original_request():
    brief = TaskBrief(original_request="回顾英伟达近五年行情", objective="采集行情")
    payload = json.loads(brief.model_dump_json())
    assert payload["original_request"].startswith("回顾")
    with pytest.raises(ValidationError):
        TaskBrief(objective="缺原话")  # type: ignore[call-arg]
