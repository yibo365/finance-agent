"""JSON 修复层单测：围栏/坏引号/截断的确定性打捞 + 事件增量提交 + 兜底合并。

样本形态取自真实事故：event-researcher 研究成果完整，仅因最终输出的
```json 围栏、未转义引号或截断导致 SDK 解析失败，orchestrator 整体重跑。
"""

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import EventItem, EventList
from finance_agent.json_repair import (
    extract_raw_from_error,
    parse_items_loose,
    repair_json_text,
    salvage_output,
)
from finance_agent.orchestrator import _finalize_events
from finance_agent.tools.agent_tools import submit_events_impl
from finance_agent.workspace import Workspace

_EVENT = ('{"date": "2023-08-29", "title": "Grayscale 胜诉 SEC", "category": "监管", '
          '"direction": "up", "move": "BTC 上涨", "impact": 5, "notes": "", '
          '"sources": [], "evidence_refs": []}')


def _sdk_error(raw: str) -> str:
    return (f"An error occurred while running the tool. Please try again. "
            f"Error: Invalid JSON when parsing {raw} for TypeAdapter(EventList); "
            f"1 validation error for EventList")


def test_extract_raw_from_sdk_error():
    raw = '```json\n{"events": []}\n```'
    assert extract_raw_from_error(_sdk_error(raw)) == raw
    assert extract_raw_from_error("Max turns (20) exceeded") is None


def test_repair_strips_fences_and_prose():
    raw = f'好的，以下是结果：\n```json\n{{"events": [{_EVENT}], "coverage_notes": "ok"}}\n```\n以上。'
    out = salvage_output(_sdk_error(raw), EventList)
    assert isinstance(out, EventList) and len(out.events) == 1
    assert out.events[0].title == "Grayscale 胜诉 SEC"


def test_repair_escapes_rogue_quotes():
    # 真实事故：字符串内未转义英文引号提前终止解析
    raw = ('{"events": [{"date": "2023-06-05", "title": "Vision Pro 发布", '
           '"impact": 3, "notes": "被定义为"空间计算"设备", "sources": [], '
           '"category": "产品", "direction": "up", "move": "", "evidence_refs": []}], '
           '"coverage_notes": "done"}')
    out = salvage_output(_sdk_error(raw), EventList)
    assert isinstance(out, EventList) and len(out.events) == 1
    assert "空间计算" in out.events[0].notes


def test_truncated_array_salvages_complete_items():
    # 真实事故：输出超长被截断——完整条目保留，半截条目丢弃
    raw = f'{{"events": [{_EVENT}, {_EVENT.replace("08-29", "10-07").replace("Grayscale 胜诉 SEC", "哈马斯袭击以色列")}, {{"date": "2024-01-10", "title": "现货 ETF 获批", "impact": 5, "notes": "被截断在这'
    out = salvage_output(_sdk_error(raw), EventList)
    assert isinstance(out, EventList)
    assert [e.date for e in out.events] == ["2023-08-29", "2023-10-07"]


def test_invalid_items_dropped_not_fatal():
    # 用户要求：一两条坏 JSON 丢掉即可，不拖垮整体
    bad = '{"date": "2024-02-02", "title": "缺 impact 字段"}'
    raw = f'{{"events": [{_EVENT}, {bad}], "coverage_notes": "x"'  # 尾部还缺右括号
    out = salvage_output(_sdk_error(raw), EventList)
    assert isinstance(out, EventList) and len(out.events) == 1


def test_parse_items_loose_skips_corrupt_middle():
    raw = f'{{"events": [{_EVENT}, {{"date": "坏对象" ::: }}, {_EVENT.replace("08-29", "12-01")}]}}'
    items = parse_items_loose(raw, "events")
    assert [i["date"] for i in items] == ["2023-08-29", "2023-12-01"]


def test_repair_json_text_passthrough_for_valid():
    assert repair_json_text('{"a": 1}') == '{"a": 1}'


# ---------- 增量提交与兜底合并 ----------

def _app(tmp_path):
    return AppContext(settings=Settings(mock_mode=True),
                      workspace=Workspace.create(tmp_path / "o"))


def _item(date="2025-01-27", title="DeepSeek 冲击"):
    return EventItem(date=date, title=title, impact=5)


def test_submit_events_accumulates_and_dedups(tmp_path):
    app = _app(tmp_path)
    out = submit_events_impl(app, [_item(), _item()])          # 同批去重
    assert out["accepted"] == 1 and out["duplicates_skipped"] == 1
    out = submit_events_impl(app, [_item(), _item(title="ETF 获批")])
    assert out["accepted"] == 1 and out["total_collected"] == 2
    app.begin_subagent_run()                                   # 新运行重置
    assert app.collected_events == []


def test_finalize_events_merges_and_survives_total_failure(tmp_path):
    app = _app(tmp_path)
    submit_events_impl(app, [_item()])
    # 最终输出完全损坏（None）：已提交事件挽救整轮
    out = _finalize_events(app, None)
    assert isinstance(out, EventList) and len(out.events) == 1
    assert "增量提交" in out.coverage_notes
    # 最终输出正常：合并去重，coverage_notes 保留
    final = EventList(events=[_item(), _item(title="仅在最终输出中")], coverage_notes="覆盖完整")
    out = _finalize_events(app, final)
    assert {e.title for e in out.events} == {"DeepSeek 冲击", "仅在最终输出中"}
    assert out.coverage_notes == "覆盖完整"


def test_finalize_events_none_when_nothing_to_save(tmp_path):
    assert _finalize_events(_app(tmp_path), None) is None
