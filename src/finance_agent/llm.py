"""LLM 供应方接线：把模型解析为 Agents SDK 可用的 Model。

OpenRouter/自建网关的模型名（如 deepseek/deepseek-v4-pro、openai/gpt-5.5）
必须**原样透传**——不能走 SDK 的 MultiProvider 前缀解析（它只认识少数前缀，
且会剥离前缀，真实事故：'Unknown prefix: deepseek'）。因此非 OpenAI 直连时
直接构造绑定自定义 AsyncOpenAI 客户端的 OpenAIChatCompletionsModel。

## 供应方兼容层（唯一收口）

底层模型可以随时换（OpenAI/OpenRouter/DeepSeek 官方/Kimi/自建网关），
应用代码不感知供应方差异——所有兼容性变换集中在本模块的客户端包装里，
每一条都是确定性纯函数、有单测、对宽容供应方无害：

1. response_format 降级（真实事故 "This response_format type is unavailable
   now"）：SDK 对带 output_type 的 agent 一律发 {"type": "json_schema", ...}，
   Kimi/DeepSeek 官方只认 json_object → 网关路径默认降级为 json_object，
   schema 改由提示词携带（output_schema_note）。FINANCE_AGENT_JSON_MODE
   可覆盖：object（默认）| schema（原样透传）| off（不发）。
2. 消息序列规整（真实事故 "An assistant message with 'tool_calls' must be
   followed by tool messages…"）：模型同轮"边说话边调工具"时，SDK 把文本
   拆成独立 assistant 消息插在 tool_calls 与 tool 回执之间——OpenAI/OpenRouter
   宽容，DeepSeek 官方/Kimi 严格校验邻接并 400。发送前把插队文本并入
   tool_calls 消息、回执按声明顺序紧随、缺失回执补占位、无主回执丢弃。
"""

from __future__ import annotations

import json
from typing import Any

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI
from pydantic import BaseModel

from finance_agent.config import Settings

_clients: dict[tuple[str, str, str], AsyncOpenAI] = {}


def rewrite_response_format(kwargs: dict[str, Any], mode: str) -> dict[str, Any]:
    """按 json_mode 改写请求参数（纯函数，可单测）。只动 json_schema 类型。"""
    rf = kwargs.get("response_format")
    if not (isinstance(rf, dict) and rf.get("type") == "json_schema"):
        return kwargs
    if mode == "off":
        kwargs.pop("response_format", None)
    elif mode == "object":
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs  # mode == "schema"：原样透传


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 分段内容：取全部 text 部分
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def sanitize_chat_messages(messages: list[dict]) -> list[dict]:
    """规整消息序列以通过严格供应方（DeepSeek 官方/Kimi）的邻接校验（纯函数）。

    - assistant(tool_calls) 与其 tool 回执之间的插队 assistant 文本
      并入 tool_calls 消息的 content；
    - tool 回执按 tool_calls 声明顺序紧随其后；缺失的补占位回执；
    - 无主 tool 回执（找不到对应 tool_calls）丢弃。
    对宽容供应方而言这是语义等价的无害变换。
    """
    result: list[dict] = []
    pending: dict | None = None      # 等待回执的 assistant(tool_calls)
    extra_texts: list[str] = []      # 插队的 assistant 文本
    replies: dict[str, dict] = {}    # tool_call_id → 回执

    def flush() -> None:
        nonlocal pending, extra_texts, replies
        if pending is None:
            return
        if extra_texts:
            base = _content_text(pending.get("content"))
            pending["content"] = "\n\n".join(t for t in [base, *extra_texts] if t)
        result.append(pending)
        for tc in pending.get("tool_calls") or []:
            tid = tc.get("id")
            result.append(replies.pop(tid, None) or {
                "role": "tool", "tool_call_id": tid, "content": "（本次调用未返回结果）",
            })
        pending, extra_texts, replies = None, [], {}

    for message in messages:
        m = dict(message) if isinstance(message, dict) else message
        role = m.get("role") if isinstance(m, dict) else None
        if pending is not None and isinstance(m, dict):
            if role == "tool":
                replies[m.get("tool_call_id")] = m
                if {tc.get("id") for tc in pending.get("tool_calls") or []} <= set(replies):
                    flush()
                continue
            if role == "assistant" and not m.get("tool_calls"):
                text = _content_text(m.get("content"))
                if text:
                    extra_texts.append(text)
                continue
            flush()  # 其他角色到来：结清当前 tool_calls（缺失回执补占位）
        if isinstance(m, dict) and role == "assistant" and m.get("tool_calls"):
            pending = m
            continue
        if isinstance(m, dict) and role == "tool":
            continue  # 无主回执：严格供应方同样会拒绝，直接丢弃
        result.append(m)
    flush()
    return result


def _patch_compat(client: AsyncOpenAI, mode: str) -> AsyncOpenAI:
    original = client.chat.completions.create

    async def create(*args: Any, **kwargs: Any):
        if isinstance(kwargs.get("messages"), list):
            kwargs["messages"] = sanitize_chat_messages(kwargs["messages"])
        return await original(*args, **rewrite_response_format(kwargs, mode))

    client.chat.completions.create = create  # type: ignore[method-assign]
    return client


def _client_for(settings: Settings) -> AsyncOpenAI:
    key = (settings.base_url or "", settings.api_key, settings.json_mode)
    if key not in _clients:
        client = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key or "missing-key",
            default_headers={"X-Title": "finance-agent"},  # OpenRouter 归因头（可选）
        )
        _clients[key] = _patch_compat(client, settings.json_mode)
    return _clients[key]


def output_schema_note(model_type: type[BaseModel]) -> str:
    """输出契约的提示词说明。

    json_object/off 模式下 schema 不再经 response_format 送达模型，
    必须在提示词里给出；json_schema 模式下重复声明也无害（双保险）。
    """
    schema = json.dumps(model_type.model_json_schema(), ensure_ascii=False)
    return (
        "\n\n## 最终输出格式（硬性要求）\n\n"
        "最终输出必须是**单个 JSON 对象**：不要 markdown 围栏、不要任何前后缀"
        "文字、字符串值内不要未转义的英文双引号。结构符合以下 JSON Schema：\n"
        f"{schema}\n"
    )


def get_model(settings: Settings) -> str | OpenAIChatCompletionsModel:
    """OpenAI 官方（base_url 为空）返回模型名字符串（SDK 默认行为）；
    其余 OpenAI 兼容网关返回绑定自定义客户端的 Model。"""
    if not settings.base_url:
        return settings.model
    # tracing 上传目标是 OpenAI 平台，非直连时关闭（否则无 OpenAI key 只会刷警告）
    set_tracing_disabled(True)
    return OpenAIChatCompletionsModel(model=settings.model, openai_client=_client_for(settings))


def configure_llm(settings: Settings) -> None:
    """进程级准备（幂等）。模型绑定由 get_model 完成，这里只处理全局开关。"""
    if settings.base_url:
        set_tracing_disabled(True)
