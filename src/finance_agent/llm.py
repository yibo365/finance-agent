"""LLM 供应方接线：把模型解析为 Agents SDK 可用的 Model。

OpenRouter/自建网关的模型名（如 deepseek/deepseek-v4-pro、openai/gpt-5.5）
必须**原样透传**——不能走 SDK 的 MultiProvider 前缀解析（它只认识少数前缀，
且会剥离前缀，真实事故：'Unknown prefix: deepseek'）。因此非 OpenAI 直连时
直接构造绑定自定义 AsyncOpenAI 客户端的 OpenAIChatCompletionsModel。
"""

from __future__ import annotations

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI

from finance_agent.config import Settings

_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def _client_for(settings: Settings) -> AsyncOpenAI:
    key = (settings.base_url or "", settings.api_key)
    if key not in _clients:
        _clients[key] = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key or "missing-key",
            default_headers={"X-Title": "finance-agent"},  # OpenRouter 归因头（可选）
        )
    return _clients[key]


def get_model(settings: Settings) -> str | OpenAIChatCompletionsModel:
    """OpenAI 直连返回模型名字符串（SDK 默认行为）；否则返回绑定客户端的 Model。"""
    if settings.provider == "openai" and not settings.base_url:
        return settings.model
    # tracing 上传目标是 OpenAI 平台，非直连时关闭（否则无 OpenAI key 只会刷警告）
    set_tracing_disabled(True)
    return OpenAIChatCompletionsModel(model=settings.model, openai_client=_client_for(settings))


def configure_llm(settings: Settings) -> None:
    """进程级准备（幂等）。模型绑定由 get_model 完成，这里只处理全局开关。"""
    if settings.provider != "openai" or settings.base_url:
        set_tracing_disabled(True)
