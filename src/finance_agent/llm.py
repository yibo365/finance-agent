"""LLM 供应方接线：把 Agents SDK 的默认客户端指向所选 provider。

OpenRouter 走 OpenAI 兼容的 Chat Completions API（它不提供 OpenAI Responses API），
因此需要：自定义 AsyncOpenAI 客户端（base_url）+ 切换 SDK 默认 API 形态 +
关闭 tracing 上传（tracing 上传目标是 OpenAI 平台，无 OpenAI key 时只会刷警告）。
"""

from __future__ import annotations

from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled
from openai import AsyncOpenAI

from finance_agent.config import Settings


def configure_llm(settings: Settings) -> None:
    """进程级全局配置，SessionCore 初始化时调用（重复调用安全）。"""
    if settings.provider == "openrouter":
        client = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key or "missing-key",
            default_headers={
                # OpenRouter 归因头（可选，用于其控制台统计）
                "X-Title": "finance-agent",
            },
        )
        set_default_openai_client(client, use_for_tracing=False)
        set_default_openai_api("chat_completions")
        set_tracing_disabled(True)
    elif settings.base_url:
        # OpenAI 兼容自建网关
        client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
        set_default_openai_client(client, use_for_tracing=False)
        set_default_openai_api("chat_completions")
        set_tracing_disabled(True)
    # OpenAI 直连：SDK 默认行为（读 OPENAI_API_KEY 环境变量），无需接线
