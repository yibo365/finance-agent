"""上下文治理单测：材料引用传递、主 agent 历史修剪、检索预算、错误截断。

对应真实事故：子代理运行内上下文滚到 7.8M tokens（超 8MB）；
event-researcher 无预算连搜 98 次后 Max turns 作废；
51KB brief 驻留主对话历史全程陪跑。
"""

import pytest

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import (
    AlignmentEntry,
    AlignmentMatrix,
    ChangepointOut,
    EventItem,
    EventList,
    MarketData,
    MarketDatasetSummary,
)
from finance_agent.orchestrator import _digest
from finance_agent.session import trim_history
from finance_agent.tools.agent_tools import search_hn_impl, truncated_tool_error
from finance_agent.workspace import Workspace, WorkspaceError

MOCK = Settings(mock_mode=True)


# ---------- 材料引用传递 ----------

def test_material_store_load_roundtrip(tmp_path):
    ws = Workspace.create(tmp_path / "outputs", "s-20260705-mat")
    payload = {"events": [{"date": "2025-01-27", "title": "DeepSeek R1"}]}
    mid = ws.store_material("events", payload)
    assert mid == "mat-events-1"
    assert ws.load_material(mid) == payload
    assert ws.store_material("events", payload) == "mat-events-2"  # 序号递增


def test_material_guards_ids(tmp_path):
    ws = Workspace.create(tmp_path / "outputs", "s-20260705-mat2")
    with pytest.raises(WorkspaceError, match="非法 material"):
        ws.store_material("../evil", {})
    with pytest.raises(WorkspaceError, match="非法 material_id"):
        ws.load_material("../../etc/passwd")
    ws.store_material("events", {"a": 1})
    with pytest.raises(WorkspaceError, match="mat-events-1"):  # 报错列出可用材料
        ws.load_material("mat-events-9")


# ---------- 子代理输出摘要（进主对话历史的就这些） ----------

def test_digest_market_data_compacts_changepoints():
    output = MarketData(
        datasets=[MarketDatasetSummary(
            dataset_id="ds-nvda", ticker="NVDA", rows=1254,
            start="2021-07-06", end="2026-07-02", source="Yahoo", evidence_id="ev-1",
            changepoints=[ChangepointOut(
                date="2025-01-27", kind="drawdown", rule="单日 -8%",
                severity=3, window=["2025-01-24", "2025-01-28"],
            )],
        )],
        echo="已取 NVDA 五年日线",
    )
    digest = _digest(output)
    assert digest["datasets"][0]["changepoints"] == ["2025-01-27 drawdown sev3"]
    assert "rule" not in str(digest)  # 全量细节留在材料里


def test_digest_events_and_alignment_are_one_liners():
    events = EventList(
        events=[EventItem(date="2025-01-27", title="DeepSeek 冲击", impact=5,
                          direction="down", sources=[], notes="长备注" * 100)],
        coverage_notes="覆盖完整",
    )
    d = _digest(events)
    assert d["events"] == ["2025-01-27 [事件] DeepSeek 冲击（impact 5，down）"]
    assert "长备注" not in str(d)

    matrix = AlignmentMatrix(
        entries=[AlignmentEntry(changepoint_date="2025-01-27", changepoint_kind="drawdown",
                                verdict="match", matched_event_titles=["DeepSeek 冲击"],
                                reasoning="详细论证" * 200)],
        overall_notes="",
    )
    d2 = _digest(matrix)
    assert d2["verdicts"] == {"match": 1}
    assert "详细论证" not in str(d2)


# ---------- 主 agent 历史修剪 ----------

def _turn(n: int) -> list[dict]:
    return [
        {"role": "user", "content": f"任务{n}"},
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "想法"}]},
        {"type": "function_call", "name": "run_data_collector",
         "call_id": f"c{n}", "arguments": "x" * 1000},
        {"type": "function_call_output", "call_id": f"c{n}", "output": "y" * 1000},
        {"role": "assistant", "content": [{"type": "output_text", "text": f"结论{n}"}]},
    ]


def test_trim_history_keeps_recent_turns_and_old_text():
    items = _turn(1) + _turn(2) + _turn(3)
    trimmed = trim_history(items, keep_turns=2)
    # 旧轮（轮1）：只剩 user + assistant 文本
    old = trimmed[: len(trimmed) - 2 * len(_turn(0))]
    assert [m.get("role") for m in old] == ["user", "assistant"]
    # 最近两轮原样保留（含工具对与 reasoning）
    assert trimmed[len(old):] == _turn(2) + _turn(3)


def test_trim_history_noop_for_short_sessions():
    items = _turn(1) + _turn(2)
    assert trim_history(items, keep_turns=2) == items


# ---------- 检索预算 ----------

def test_search_budget_forces_convergence(tmp_path):
    settings = Settings(mock_mode=True, search_budget=2)
    app = AppContext(settings=settings, workspace=Workspace.create(tmp_path / "o"))
    assert "note" not in search_hn_impl(app, "chatgpt", "2022-11-01", "2022-12-15")
    assert "note" not in search_hn_impl(app, "nvidia", "2022-11-01", "2022-12-15")
    third = search_hn_impl(app, "deepseek", "2022-11-01", "2022-12-15")
    assert "预算" in third["note"] and third["items"] == []
    # 新一次 subagent 运行重置预算
    app.begin_subagent_run()
    assert "note" not in search_hn_impl(app, "chatgpt", "2022-11-01", "2022-12-15")


# ---------- 错误截断 ----------

def test_truncated_tool_error_bounds_message():
    huge = ValueError("Invalid JSON input: " + "x" * 100_000)
    message = truncated_tool_error(None, huge)
    assert len(message) < 2000
    assert message.startswith("An error occurred while running the tool")  # ok 判定前缀不变
    assert "已截断" in message
