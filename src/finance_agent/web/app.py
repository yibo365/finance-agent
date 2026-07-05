"""FastAPI 薄层：多会话聊天（SSE 事件流）+ 按会话的历史/产物接口。

会话归属（FR-19）：服务端不提供会话列表——单用户场景，列表由前端
localStorage 维护；新会话在首条消息时创建，session_id 作为首个 SSE 事件
返回。服务端职责只剩三件：执行（chat）、回放（messages）、产物（state/file）。

安全边界：仅绑定 127.0.0.1；session_id / artifact_id 走 Workspace 与
manifest 的既有校验（无任意路径参数）；前端为单文件原生 JS，无构建链、
无外部资源。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from finance_agent.config import OUTPUTS_DIR, Settings
from finance_agent.session import SessionCore, read_history
from finance_agent.workspace import Workspace, WorkspaceError

_STATIC = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class SessionRegistry:
    """进程内会话池：chat 用的 SessionCore 懒加载 + 每会话一把执行锁。"""

    def __init__(self, settings: Settings, outputs_dir: Path) -> None:
        self.settings = settings
        self.outputs_dir = outputs_dir
        self._cores: dict[str, SessionCore] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def add(self, core: SessionCore) -> SessionCore:
        self._cores[core.workspace.session_id] = core
        return core

    def create(self) -> SessionCore:
        return self.add(SessionCore.start(self.settings, self.outputs_dir))

    def core(self, session_id: str) -> SessionCore:
        if session_id not in self._cores:
            try:
                self.add(SessionCore.resume(self.settings, session_id, self.outputs_dir))
            except WorkspaceError as exc:
                raise HTTPException(404, str(exc)) from exc
        return self._cores[session_id]

    def workspace(self, session_id: str) -> Workspace:
        """只读路由用：不为回放历史/下载产物付出建 orchestrator 的成本。"""
        if session_id in self._cores:
            return self._cores[session_id].workspace
        try:
            return Workspace.open(self.outputs_dir, session_id)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(
    settings: Settings,
    *,
    outputs_dir: Path | None = None,
    initial_core: SessionCore | None = None,
) -> FastAPI:
    registry = SessionRegistry(settings, outputs_dir or OUTPUTS_DIR)
    initial_session_id = None
    if initial_core is not None:  # --web --resume <id>：并入前端左栏
        registry.add(initial_core)
        initial_session_id = initial_core.workspace.session_id

    app = FastAPI(title="finance-agent", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> dict:
        return {
            "provider": settings.provider,
            "model": settings.model,
            "initial_session_id": initial_session_id,
        }

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        core = registry.core(request.session_id) if request.session_id else registry.create()
        session_id = core.workspace.session_id
        lock = registry.lock(session_id)

        async def event_stream():
            if lock.locked():
                # 一次一轮：并发消息直接拒绝，不排队（排队会让用户对着
                # 空屏等上一轮跑完，还以为是自己这条卡了）
                yield _sse({"type": "session", "session_id": session_id})
                yield _sse({"type": "error", "text": "该会话正在处理上一条消息，请等它完成后再发。"})
                return
            async with lock:
                try:
                    async for event in core.stream_turn(request.message):
                        yield _sse(event)
                except Exception as exc:  # noqa: BLE001 —— 错误也以事件形式送达前端
                    yield _sse({"type": "error", "text": str(exc)})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/sessions/{session_id}/messages")
    def messages(session_id: str) -> dict:
        workspace = registry.workspace(session_id)
        return {
            "session_id": session_id,
            "messages": read_history(workspace.session_db_path, session_id),
        }

    @app.get("/api/sessions/{session_id}/state")
    def session_state(session_id: str) -> dict:
        workspace = registry.workspace(session_id)
        return {
            "session_id": session_id,
            "workspace_dir": str(workspace.dir),
            "artifacts": workspace.list_artifacts(),
            "datasets": workspace.dataset_index(),
        }

    @app.get("/api/sessions/{session_id}/artifacts/{artifact_id}/file")
    def artifact_file(session_id: str, artifact_id: str, version: int | None = None) -> FileResponse:
        workspace = registry.workspace(session_id)
        record = workspace.manifest().get(artifact_id)
        if record is None:
            raise HTTPException(404, f"产物不存在：{artifact_id}")
        v = version or record.current_version
        matches = [item for item in record.versions if item.v == v]
        if not matches:
            raise HTTPException(404, f"版本不存在：{artifact_id} v{v}")
        path = workspace.dir / matches[0].file
        return FileResponse(path, filename=path.name)

    return app


def ensure_port_available(port: int, host: str = "127.0.0.1") -> None:
    """启动前预检端口，占用时给出可操作的报错。

    放在建会话之前调用：否则 uvicorn 绑定失败时已经留下一个空会话工作区。
    """
    import errno
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            raise SystemExit(
                f"端口 {port} 已被占用（可能有上一个 --web 进程还在运行）。\n"
                f"  换端口启动：finance-agent --web --port {port + 1}\n"
                f"  或找到占用进程：lsof -i :{port}"
            ) from exc


def serve(settings: Settings, port: int = 8765, initial_core: SessionCore | None = None) -> None:
    import uvicorn

    note = (
        f"（已载入会话 {initial_core.workspace.session_id}）" if initial_core else "（会话在首条消息时创建）"
    )
    print(f"Web 界面：http://127.0.0.1:{port} {note}")
    uvicorn.run(
        create_app(settings, initial_core=initial_core),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
