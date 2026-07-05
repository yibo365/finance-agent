"""SessionCore：三个入口（REPL / -p 一次性 / Web）共用的会话核心。

持有 Workspace + SQLiteSession + orchestrator，暴露 run_turn 一个动作。
对话记忆只挂在 orchestrator 这一层（subagent 每次调用都是干净上下文）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Runner, SQLiteSession

from finance_agent.config import OUTPUTS_DIR, Settings
from finance_agent.context import AppContext
from finance_agent.orchestrator import build_orchestrator
from finance_agent.workspace import Workspace

MAX_ORCHESTRATOR_TURNS = 30


class SessionCore:
    def __init__(self, settings: Settings, workspace: Workspace) -> None:
        from finance_agent.llm import configure_llm

        configure_llm(settings)
        self.settings = settings
        self.workspace = workspace
        self.ctx = AppContext(settings=settings, workspace=workspace)
        self.chat = SQLiteSession(
            session_id=workspace.session_id, db_path=workspace.session_db_path
        )
        self.orchestrator = build_orchestrator(settings)

    @classmethod
    def start(cls, settings: Settings, outputs_dir: Path | None = None) -> "SessionCore":
        return cls(settings, Workspace.create(outputs_dir or OUTPUTS_DIR))

    @classmethod
    def resume(
        cls, settings: Settings, session_id: str, outputs_dir: Path | None = None
    ) -> "SessionCore":
        return cls(settings, Workspace.open(outputs_dir or OUTPUTS_DIR, session_id))

    def _ensure_workspace_alive(self) -> None:
        """工作区目录被外部删除时快速失败并给出可理解的报错。

        真实事故：运行中会话目录被误删，SQLiteSession 从 SDK 深处抛出晦涩的
        'unable to open database file'，排障绕了很大弯。
        """
        if not self.workspace.dir.is_dir():
            raise RuntimeError(
                f"会话工作区已不存在：{self.workspace.dir}。"
                "目录可能被外部删除——本会话无法继续，请新开会话。"
            )

    async def run_turn(self, user_input: str) -> str:
        self._ensure_workspace_alive()
        result = await Runner.run(
            self.orchestrator,
            user_input,
            context=self.ctx,
            session=self.chat,
            max_turns=MAX_ORCHESTRATOR_TURNS,
        )
        return str(result.final_output)

    async def stream_turn(self, user_input: str):
        """流式执行一轮：产出 status（工具/子agent进度）、delta（文本增量）、done。"""
        from openai.types.responses import ResponseTextDeltaEvent

        self._ensure_workspace_alive()

        result = Runner.run_streamed(
            self.orchestrator,
            user_input,
            context=self.ctx,
            session=self.chat,
            max_turns=MAX_ORCHESTRATOR_TURNS,
        )
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(
                event.data, ResponseTextDeltaEvent
            ):
                yield {"type": "delta", "text": event.data.delta}
            elif event.type == "run_item_stream_event" and event.item.type == "tool_call_item":
                name = getattr(event.item.raw_item, "name", "") or "工具"
                yield {"type": "status", "text": f"正在调用 {name}…"}
        yield {"type": "done", "reply": str(result.final_output)}

    def artifact_snapshot(self) -> dict[str, int]:
        """{artifact_id: current_version}，供入口层计算一轮对话产生的产物增量。"""
        return {
            item["artifact_id"]: item["current_version"]
            for item in self.workspace.list_artifacts()
        }

    def artifact_delta(self, before: dict[str, int]) -> list[dict[str, Any]]:
        return [
            item
            for item in self.workspace.list_artifacts()
            if item["current_version"] != before.get(item["artifact_id"])
        ]


def list_sessions(outputs_dir: Path | None = None) -> list[str]:
    base = outputs_dir or OUTPUTS_DIR
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.glob("s-*") if p.is_dir())
