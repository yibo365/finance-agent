"""AppContext：一次会话的运行时上下文，经 SDK 的 context 参数注入所有工具。

agent/LLM 看不到它（不进 prompt）；只有工具代码能经 RunContextWrapper 拿到。
这是"LLM 只传逻辑标识、真实资源句柄在系统侧"的载体。
"""

from __future__ import annotations

from dataclasses import dataclass

from finance_agent.config import Settings
from finance_agent.workspace import Workspace


@dataclass
class AppContext:
    settings: Settings
    workspace: Workspace
