"""事件协议单测：SDK run_item → 外发事件的翻译、摘要截断、工具名回填。"""

from types import SimpleNamespace

from finance_agent.events import DETAIL_LIMIT, RunItemTranslator, clip


def test_clip_flattens_whitespace_and_truncates():
    assert clip("a\n  b\t c") == "a b c"
    long = "字" * (DETAIL_LIMIT * 2)
    assert len(clip(long)) == DETAIL_LIMIT
    assert clip(long).endswith("…")


def test_tool_call_translated_with_agent_and_detail():
    t = RunItemTranslator("data-collector")
    item = SimpleNamespace(
        type="tool_call_item",
        raw_item=SimpleNamespace(name="fetch_market_data", call_id="c1",
                                 arguments='{"ticker": "NVDA"}'),
    )
    event = t.translate(item)
    assert event == {
        "type": "tool_call", "agent": "data-collector",
        "tool": "fetch_market_data", "detail": '{"ticker": "NVDA"}',
    }


def test_tool_result_backfills_name_and_flags_errors():
    t = RunItemTranslator("data-collector")
    t.translate(SimpleNamespace(
        type="tool_call_item",
        raw_item=SimpleNamespace(name="fetch_market_data", call_id="c1", arguments="{}"),
    ))
    # raw_item 为 dict 形态（SDK 对 function_call_output 的持久化形态）
    ok_event = t.translate(SimpleNamespace(
        type="tool_call_output_item",
        raw_item={"call_id": "c1", "output": '{"rows": 3}'},
        output='{"rows": 3}',
    ))
    assert ok_event["tool"] == "fetch_market_data" and ok_event["ok"] is True

    err_event = t.translate(SimpleNamespace(
        type="tool_call_output_item",
        raw_item={"call_id": "c9", "output": ""},
        output="An error occurred while running the tool. Please try again.",
    ))
    assert err_event["ok"] is False and err_event["tool"] == ""  # 未知 call_id 不误配


def test_error_details_keep_diagnostic_tail():
    # 真实事故：校验错误被截在字段名处（"…render_artifact_args spe…"），无法定位
    from finance_agent.events import ERROR_DETAIL_LIMIT

    t = RunItemTranslator("report-builder")
    long_error = ("An error occurred while running the tool. Please try again. "
                  "Error: Invalid JSON input for tool render_artifact: 1 validation error "
                  "for render_artifact_args spec.blocks.2.kline_chart.data_ref Field required "
                  + "x" * 400)
    event = t.translate(SimpleNamespace(
        type="tool_call_output_item", raw_item={"call_id": "c1"}, output=long_error,
    ))
    assert "spec.blocks.2.kline_chart.data_ref" in event["detail"]  # 字段路径完整可见
    assert len(event["detail"]) <= ERROR_DETAIL_LIMIT
    # 正常输出仍走短截断
    ok_event = t.translate(SimpleNamespace(
        type="tool_call_output_item", raw_item={"call_id": "c1"}, output="y" * 500,
    ))
    assert len(ok_event["detail"]) == DETAIL_LIMIT


def test_unrelated_items_ignored():
    t = RunItemTranslator("orchestrator")
    assert t.translate(SimpleNamespace(type="message_output_item", raw_item=None)) is None
