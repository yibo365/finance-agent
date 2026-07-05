"""LLM 供应方接线：把模型解析为 Agents SDK 可用的 Model。

OpenRouter/自建网关的模型名（如 deepseek/deepseek-v4-pro、openai/gpt-5.5）
必须**原样透传**——不能走 SDK 的 MultiProvider 前缀解析（它只认识少数前缀，
且会剥离前缀，真实事故：'Unknown prefix: deepseek'）。因此非 OpenAI 直连时
直接构造绑定自定义 AsyncOpenAI 客户端的 OpenAIChatCompletionsModel。

response_format 兼容（真实事故）：SDK 对带 output_type 的 agent 一律发送
{"type": "json_schema", ...}，Kimi/DeepSeek 官方 API 不支持该类型
（"This response_format type is unavailable now"），只认 json_object。
网关路径默认把 json_schema 降级为 json_object（各家通吃，且供应方保证输出
为合法 JSON——顺带在源头压制围栏/截断类坏 JSON）；schema 本身改由提示词
携带（output_schema_note）。可用 FINANCE_AGENT_JSON_MODE 覆盖：
object（默认）| schema（原样透传，供应方支持时更严格）| off（不发）。
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


def _patch_json_mode(client: AsyncOpenAI, mode: str) -> AsyncOpenAI:
    if mode == "schema":
        return client
    original = client.chat.completions.create

    async def create(*args: Any, **kwargs: Any):
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
        _clients[key] = _patch_json_mode(client, settings.json_mode)
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
