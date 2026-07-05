"""agent 层单测（不调 LLM）：权限矩阵、动态 prompt、工具 impl、契约模型。"""

import json

import pytest
from agents import RunContextWrapper, WebSearchTool

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

MOCK = Settings(openai_api_key="", model="gpt-5.5", mock_mode=True)
LIVE = Settings(openai_api_key="k", model="gpt-5.5", mock_mode=False)


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


def test_subagents_declare_structured_output():
    for build in (build_data_collector, build_event_researcher,
                  build_alignment_analyst, build_report_builder):
        assert build(MOCK).output_type is not None


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


# ---------- 契约 ----------

def test_taskbrief_requires_original_request():
    brief = TaskBrief(original_request="回顾英伟达近五年行情", objective="采集行情")
    payload = json.loads(brief.model_dump_json())
    assert payload["original_request"].startswith("回顾")
    with pytest.raises(Exception):
        TaskBrief(objective="缺原话")  # type: ignore[call-arg]
