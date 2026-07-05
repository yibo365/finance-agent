"""运行配置。全部经环境变量注入，密钥只存在于进程环境，不落盘、不入库。

支持两种 LLM 供应方式：
- OpenAI 直连（默认）：OPENAI_API_KEY
- OpenRouter：OPENROUTER_API_KEY（OpenAI 兼容 API；联网搜索改用其 web 插件，
  搜索模型与结果条数可配）
只设其中一个 key 时自动选择对应 provider；两个都设时默认 OpenAI，
可用 FINANCE_AGENT_PROVIDER=openrouter 显式指定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent
BUILTIN_SKILLS_DIR = PACKAGE_ROOT / "skills" / "builtin"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODELS = {"openai": "gpt-5.5", "openrouter": "openai/gpt-5.5"}


@dataclass(frozen=True)
class Settings:
    provider: str = "openai"          # "openai" | "openrouter"
    api_key: str = ""
    model: str = "gpt-5.5"
    base_url: str | None = None       # openrouter / 自建网关时使用
    search_model: str = "gpt-5.5"     # openrouter web 插件所用模型（默认同主模型）
    web_max_results: int = 5          # openrouter web 插件的检索结果条数
    mock_mode: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        provider = os.environ.get("FINANCE_AGENT_PROVIDER", "").strip().lower()
        if provider not in ("openai", "openrouter"):
            provider = "openrouter" if openrouter_key and not openai_key else "openai"
        if provider == "openrouter":
            api_key = openrouter_key or openai_key
            base_url = os.environ.get("FINANCE_AGENT_BASE_URL", OPENROUTER_BASE_URL)
        else:
            api_key = openai_key
            base_url = os.environ.get("FINANCE_AGENT_BASE_URL") or None
        model = os.environ.get("FINANCE_AGENT_MODEL", _DEFAULT_MODELS[provider])
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            search_model=os.environ.get("FINANCE_AGENT_SEARCH_MODEL", model),
            web_max_results=int(os.environ.get("FINANCE_AGENT_WEB_MAX_RESULTS", "5")),
            mock_mode=os.environ.get("FINANCE_AGENT_MOCK", "") == "1",
        )

    def require_api_key(self) -> None:
        if not self.mock_mode and not self.api_key:
            raise RuntimeError(
                "缺少 API 密钥。请复制 .env.example 为 .env 后配置：\n"
                "  OpenAI 直连  → OPENAI_API_KEY=sk-...\n"
                "  OpenRouter  → OPENROUTER_API_KEY=sk-or-...\n"
                "（两者都设时默认 OpenAI，可用 FINANCE_AGENT_PROVIDER=openrouter 指定；"
                "或设 FINANCE_AGENT_MOCK=1 以离线 mock 模式运行。）"
            )
