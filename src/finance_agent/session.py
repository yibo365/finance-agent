"""SessionCore：三个入口（REPL / -p 一次性 / Web）共用的会话核心。

持有 Workspace + SQLiteSession + orchestrator。stream_turn 是唯一执行引擎
（类型化事件流，FR-18），run_turn 只是它的收集器——CLI 与 Web 因此同源，
不存在两套执行口径。对话记忆只挂在 orchestrator 这一层（subagent 每次
调用都是干净上下文）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from agents import Runner, SQLiteSession

from finance_agent.config import OUTPUTS_DIR, Settings
from finance_agent.context import AppContext
from finance_agent.events import RunItemTranslator, clip
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
    def start(cls, settings: Settings, outputs_dir: Path | None = None) -> SessionCore:
        return cls(settings, Workspace.create(outputs_dir or OUTPUTS_DIR))

    @classmethod
    def resume(
        cls, settings: Settings, session_id: str, outputs_dir: Path | None = None
    ) -> SessionCore:
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

    async def run_turn(
        self, user_input: str, on_event: Callable[[dict], None] | None = None
    ) -> str:
        """stream_turn 的收集器：逐事件回调（可选），返回最终回复文本。"""
        reply = ""
        async for event in self.stream_turn(user_input):
            if on_event is not None:
                on_event(event)
            if event["type"] == "done":
                reply = event["reply"]
        return reply

    async def stream_turn(self, user_input: str) -> AsyncIterator[dict]:
        """唯一执行引擎：产出类型化事件流（协议见 events.py 模块注释）。

        orchestrator 自身的流事件与嵌套 subagent 经 ctx.emit 转发的事件在
        队列中合流——emit 与流消费同在事件循环线程，put_nowait 天然有序。
        """
        from openai.types.responses import ResponseTextDeltaEvent

        self._ensure_workspace_alive()
        yield {"type": "session", "session_id": self.workspace.session_id}

        artifacts_before = self.artifact_snapshot()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.ctx.on_event = queue.put_nowait
        translator = RunItemTranslator("orchestrator")
        result = Runner.run_streamed(
            self.orchestrator,
            user_input,
            context=self.ctx,
            session=self.chat,
            max_turns=MAX_ORCHESTRATOR_TURNS,
        )

        async def _pump() -> None:
            try:
                async for event in result.stream_events():
                    if event.type == "raw_response_event" and isinstance(
                        event.data, ResponseTextDeltaEvent
                    ):
                        queue.put_nowait({"type": "delta", "text": event.data.delta})
                    elif event.type == "run_item_stream_event":
                        translated = translator.translate(event.item)
                        if translated is not None:
                            queue.put_nowait(translated)
            finally:
                queue.put_nowait(None)  # 结束哨兵：无论正常/异常都解除消费端等待

        pump = asyncio.create_task(_pump())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await pump  # 让运行期异常在此浮出，由入口层转成 error 事件
        finally:
            if not pump.done():
                pump.cancel()
            self.ctx.on_event = None
        yield {
            "type": "done",
            "reply": str(result.final_output),
            "artifacts": self.artifact_delta(artifacts_before),
        }

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


def _text_of(content: Any) -> str:
    """SQLiteSession 里 content 既可能是纯字符串也可能是分段列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("output_text", "input_text")
        )
    return ""


def read_history(db_path: Path, session_id: str) -> list[dict]:
    """把 SQLiteSession 持久化的对话重建为展示消息（FR-19）。

    输出三类：user / assistant（文本）、action（工具调用 + 匹配结果的
    ok/摘要）——历史轮次的执行时间线由此还原，无需另行落盘事件。
    reasoning 等内部条目不外发；无法解析的条目跳过（历史容错优先于报错）。
    """
    if not Path(db_path).is_file():
        return []
    messages: list[dict] = []
    pending_calls: dict[str, dict] = {}  # call_id → 对应 action 消息（等结果回填）
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_data FROM agent_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    for (data,) in rows:
        try:
            item = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        role, itype = item.get("role"), item.get("type")
        if role == "user":
            text = _text_of(item.get("content"))
            if text.strip():
                messages.append({"role": "user", "text": text})
        elif role == "assistant" or itype == "message":
            text = _text_of(item.get("content"))
            if text.strip():
                messages.append({"role": "assistant", "text": text})
        elif itype == "function_call" or ("name" in item and "call_id" in item):
            action = {
                "role": "action",
                "tool": str(item.get("name") or "工具"),
                "detail": clip(item.get("arguments") or ""),
                "ok": None,  # 结果条目到达前未知
            }
            messages.append(action)
            call_id = str(item.get("call_id") or "")
            if call_id:
                pending_calls[call_id] = action
        elif itype == "function_call_output" or ("call_id" in item and "output" in item):
            action = pending_calls.pop(str(item.get("call_id") or ""), None)
            if action is not None:
                output = str(item.get("output") or "")
                action["ok"] = not output.startswith("An error occurred while running the tool")
                action["result"] = clip(output)
        # reasoning 及其他内部条目：不外发
    return messages
