"""运行配置。全部经环境变量注入，密钥只存在于进程环境，不落盘、不入库。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent
BUILTIN_SKILLS_DIR = PACKAGE_ROOT / "skills" / "builtin"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    model: str
    mock_mode: bool

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("FINANCE_AGENT_MODEL", "gpt-5.5"),
            mock_mode=os.environ.get("FINANCE_AGENT_MOCK", "") == "1",
        )

    def require_api_key(self) -> None:
        if not self.mock_mode and not self.openai_api_key:
            raise RuntimeError(
                "缺少 OPENAI_API_KEY。请复制 .env.example 为 .env 并填写，"
                "或设置 FINANCE_AGENT_MOCK=1 以离线 mock 模式运行。"
            )
