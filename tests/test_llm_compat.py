"""供应方兼容层单测：消息序列规整（严格供应方的邻接校验）。

样本取自真实事故：DeepSeek 官方/Kimi 对
"assistant(tool_calls) 后必须紧跟全部 tool 回执"做强校验，
而 SDK 会把同轮的模型文本拆成独立 assistant 消息插在中间 → 400。
"""

from finance_agent.llm import sanitize_chat_messages


def _tc(tid, name="lookup_price"):
    return {"id": tid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_interleaved_assistant_text_merged_into_tool_calls_message():
    # 真实复现帧：assistant(tool_calls) → assistant(文本) → tool
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "tool_calls": [_tc("c1")]},
        {"role": "assistant", "content": "我先查一下价格。"},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"price\": 123}"},
        {"role": "assistant", "content": "最终答复"},
    ]
    out = sanitize_chat_messages(messages)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert out[2]["tool_calls"][0]["id"] == "c1"
    assert "我先查一下价格" in out[2]["content"]   # 插队文本并入
    assert out[3]["tool_call_id"] == "c1"          # 回执紧随
    assert out[4]["content"] == "最终答复"          # 后续消息不受影响


def test_replies_reordered_to_declaration_order_and_missing_stubbed():
    messages = [
        {"role": "assistant", "tool_calls": [_tc("a"), _tc("b"), _tc("c")]},
        {"role": "tool", "tool_call_id": "b", "content": "B"},
        {"role": "tool", "tool_call_id": "a", "content": "A"},
        {"role": "user", "content": "下一轮"},     # c 的回执缺失（幻影调用）
    ]
    out = sanitize_chat_messages(messages)
    assert [m.get("tool_call_id") for m in out[1:4]] == ["a", "b", "c"]
    assert "未返回结果" in out[3]["content"]        # 缺失回执补占位
    assert out[4]["role"] == "user"


def test_orphan_tool_message_dropped():
    messages = [
        {"role": "user", "content": "u"},
        {"role": "tool", "tool_call_id": "ghost", "content": "无主回执"},
        {"role": "assistant", "content": "答复"},
    ]
    out = sanitize_chat_messages(messages)
    assert [m["role"] for m in out] == ["user", "assistant"]


def test_wellformed_sequences_pass_through_unchanged():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "tool_calls": [_tc("c1")], "content": "查询中"},
        {"role": "tool", "tool_call_id": "c1", "content": "R"},
        {"role": "assistant", "content": "done"},
    ]
    assert sanitize_chat_messages(messages) == messages


def test_content_parts_list_merged_as_text():
    messages = [
        {"role": "assistant", "tool_calls": [_tc("c1")]},
        {"role": "assistant", "content": [{"type": "text", "text": "分段文本"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "R"},
    ]
    out = sanitize_chat_messages(messages)
    assert out[0]["content"] == "分段文本"
    assert out[1]["role"] == "tool"
