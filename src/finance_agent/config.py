"""运行配置。全部经环境变量注入，密钥只存在于进程环境，不落盘、不入库。

支持两种 LLM 供应方式：
- OpenAI 直连（默认）：OPENAI_API_KEY
- OpenRouter：OPENROUTER_API_KEY（OpenAI 兼容 API）
只设其中一个 key 时自动选择对应 provider；两个都设时默认 OpenAI，
可用 FINANCE_AGENT_PROVIDER=openrouter 显式指定。

联网搜索后端（与 LLM 供应方解耦——检索结果不应随换模型而漂移）：
- tavily（推荐）：TAVILY_API_KEY，确定性搜索 API，结构化结果不经 LLM 转述；
- openrouter-plugin：OpenRouter web 插件（LLM 生成摘要 + citations）；
- hosted：OpenAI Responses API 托管 WebSearchTool（仅 OpenAI 直连可用）。
设了 TAVILY_API_KEY 即默认 tavily；否则按 provider 回落到后两者。
可用 FINANCE_AGENT_SEARCH_BACKEND 显式指定。
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
    web_max_results: int = 5          # 联网搜索每次返回的结果条数
    max_output_tokens: int = 12000    # 单次调用输出上限：控成本，且 OpenRouter 按此做预算检查
    mock_mode: bool = False
    # "tavily" | "openrouter-plugin" | "hosted"；空串 = 按 provider 自动
    # （openrouter → openrouter-plugin，openai → hosted）。from_env 会解析成显式值。
    search_backend: str = ""
    tavily_api_key: str = ""

    @property
    def effective_search_backend(self) -> str:
        if self.search_backend:
            return self.search_backend
        return "openrouter-plugin" if self.provider == "openrouter" else "hosted"

    @classmethod
    def from_env(cls) -> Settings:
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
        tavily_key = os.environ.get("TAVILY_API_KEY", "")
        backend = os.environ.get("FINANCE_AGENT_SEARCH_BACKEND", "").strip().lower()
        if backend not in ("tavily", "openrouter-plugin", "hosted"):
            if tavily_key:
                backend = "tavily"
            else:
                backend = "openrouter-plugin" if provider == "openrouter" else "hosted"
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            search_model=os.environ.get("FINANCE_AGENT_SEARCH_MODEL", model),
            web_max_results=int(os.environ.get("FINANCE_AGENT_WEB_MAX_RESULTS", "5")),
            max_output_tokens=int(os.environ.get("FINANCE_AGENT_MAX_TOKENS", "12000")),
            mock_mode=os.environ.get("FINANCE_AGENT_MOCK", "") == "1",
            search_backend=backend,
            tavily_api_key=tavily_key,
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
