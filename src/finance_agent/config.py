"""运行配置。经环境变量注入（.env 自动加载），密钥不落库；
Web 设置弹窗可在运行时修改并写回 .env（save_to_env），对新会话生效。

LLM 供应：任何 OpenAI 兼容 API——只有三元组 base_url + api_key + model。
base_url 为空即 OpenAI 官方；OpenRouter/自建网关填对应地址即可。
（兼容旧配置：只设 OPENROUTER_API_KEY 时自动采用其 key 与 base_url。）

联网搜索：Tavily（TAVILY_API_KEY）——确定性搜索 API，结构化结果不经
LLM 转述，检索数据与 LLM 供应方解耦。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent
BUILTIN_SKILLS_DIR = PACKAGE_ROOT / "skills" / "builtin"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
WEBAPP_DIST_DIR = PROJECT_ROOT / "webapp" / "dist"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _looks_like_placeholder_secret(secret: str) -> bool:
    value = secret.strip().lower()
    if not value:
        return False
    return value in {"sk-...", "tvly-..."} or value.endswith("...")


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    base_url: str | None = None       # None = OpenAI 官方；其余填 OpenAI 兼容网关地址
    model: str = "gpt-5.5"
    tavily_api_key: str = ""
    web_max_results: int = 5          # 联网搜索每次返回的结果条数
    search_budget: int = 36           # 单次 subagent 运行的检索次数预算（确定性收敛闸）
    # 结构化输出的 response_format 策略（仅网关路径）：object=降级为 json_object
    # （Kimi/DeepSeek 等通吃）| schema=原样 json_schema（OpenRouter 等支持时更严格）
    # | off=不发。schema 说明始终随提示词下达。
    json_mode: str = "object"
    max_output_tokens: int = 12000    # 单次调用输出上限：控成本，部分网关按此做预算检查
    mock_mode: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(PROJECT_ROOT / ".env")
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        api_key = openai_key or openrouter_key
        base_url = (
            os.environ.get("FINANCE_AGENT_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or (OPENROUTER_BASE_URL if (openrouter_key and not openai_key) else None)
        ) or None
        # 兼容：走 OpenRouter 时模型名需带厂商前缀
        default_model = "openai/gpt-5.5" if base_url == OPENROUTER_BASE_URL else "gpt-5.5"
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=os.environ.get("FINANCE_AGENT_MODEL", default_model),
            tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
            web_max_results=int(os.environ.get("FINANCE_AGENT_WEB_MAX_RESULTS", "5")),
            search_budget=int(os.environ.get("FINANCE_AGENT_SEARCH_BUDGET", "36")),
            json_mode=(
                os.environ.get("FINANCE_AGENT_JSON_MODE", "").strip().lower()
                if os.environ.get("FINANCE_AGENT_JSON_MODE", "").strip().lower()
                in ("object", "schema", "off") else "object"
            ),
            max_output_tokens=int(os.environ.get("FINANCE_AGENT_MAX_TOKENS", "12000")),
            mock_mode=os.environ.get("FINANCE_AGENT_MOCK", "") == "1",
        )

    def require_api_key(self) -> None:
        if self.mock_mode:
            return
        if not self.api_key.strip():
            raise RuntimeError(
                "缺少 API 密钥。任何 OpenAI 兼容供应方均可：\n"
                "  .env 配置 → OPENAI_API_KEY=...（配合 OPENAI_BASE_URL 指定网关，"
                "留空为 OpenAI 官方）\n"
                "  或启动 Web 界面后在左下角\"设置\"中填写。\n"
                "（设 FINANCE_AGENT_MOCK=1 可离线 mock 模式运行。）"
            )
        if _looks_like_placeholder_secret(self.api_key):
            raise RuntimeError(
                "API 密钥仍是占位符。请在 .env 或 Web 界面左下角\"设置\"中填写真实 API Key。"
            )

    def has_api_key(self) -> bool:
        return bool(self.api_key.strip()) and not _looks_like_placeholder_secret(self.api_key)

    def has_tavily_api_key(self) -> bool:
        return bool(self.tavily_api_key.strip()) and not _looks_like_placeholder_secret(
            self.tavily_api_key
        )


class SettingsStore:
    """运行时可变的配置持有者（Web 设置弹窗的落点）。

    Settings 本身保持 frozen——更新即整体替换；已建会话沿用其创建时的
    配置，新会话取 current。持久化写回 .env，重启不丢。
    """

    _ENV_KEYS = {
        "api_key": "OPENAI_API_KEY",
        "base_url": "OPENAI_BASE_URL",
        "model": "FINANCE_AGENT_MODEL",
        "tavily_api_key": "TAVILY_API_KEY",
    }

    def __init__(self, settings: Settings, env_path: Path | None = None) -> None:
        self.current = settings
        self.env_path = env_path or (PROJECT_ROOT / ".env")

    def update(self, **fields: str | None) -> Settings:
        cleaned = {
            name: (value.strip() if isinstance(value, str) else value)
            for name, value in fields.items()
            if name in self._ENV_KEYS
        }
        if "base_url" in cleaned and not cleaned["base_url"]:
            cleaned["base_url"] = None
        self.current = replace(self.current, **cleaned)
        self._write_env(cleaned)
        return self.current

    def _write_env(self, cleaned: dict) -> None:
        """更新 .env 中对应键（保留其余行与注释）。密钥仍只落在本机 .env。"""
        lines = (
            self.env_path.read_text(encoding="utf-8").splitlines()
            if self.env_path.is_file()
            else []
        )
        for name, value in cleaned.items():
            env_key = self._ENV_KEYS[name]
            rendered = f"{env_key}={value or ''}"
            for i, line in enumerate(lines):
                if line.split("=", 1)[0].strip().lstrip("# ") == env_key and "=" in line:
                    lines[i] = rendered
                    break
            else:
                lines.append(rendered)
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
