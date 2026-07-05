"""AppContext：一次会话的运行时上下文，经 SDK 的 context 参数注入所有工具。

agent/LLM 看不到它（不进 prompt）；只有工具代码能经 RunContextWrapper 拿到。
这是"LLM 只传逻辑标识、真实资源句柄在系统侧"的载体。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from finance_agent.config import Settings
from finance_agent.workspace import Workspace


@dataclass
class AppContext:
    settings: Settings
    workspace: Workspace
    # 进度事件回调（FR-18）：orchestrator 的 subagent 包装工具把嵌套运行的
    # tool_call/tool_result 等事件经此上报，由 stream_turn 合流后推给前端/CLI。
    # None 表示无人订阅（如单测直接调工具 impl），emit 静默丢弃。
    on_event: Callable[[dict], None] | None = field(default=None, repr=False)
    # 当次 subagent 运行内已消耗的检索次数（真实事故：event-researcher 无预算
    # 连搜 98 次、20 轮打满后整体作废）。检索工具据此执行确定性预算收敛。
    search_calls: int = field(default=0, repr=False)
    # event-researcher 经 submit_events 增量提交的事件（真实事故：几十条事件
    # 攒到最终输出一把序列化，JSON 一坏全部作废重跑）。每次 subagent 运行重置。
    collected_events: list = field(default_factory=list, repr=False)

    def emit(self, event: dict) -> None:
        if self.on_event is not None:
            self.on_event(event)

    def begin_subagent_run(self) -> None:
        self.search_calls = 0
        self.collected_events = []
