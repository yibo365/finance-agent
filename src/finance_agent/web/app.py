"""FastAPI 薄层：多会话聊天（SSE 事件流）+ 按会话的历史/产物接口 + 运行时设置。

前后端分离：本层只提供 API；页面由 webapp/（Vite 应用）构建产物承担，
生产模式服务 webapp/dist，开发模式由 Vite dev server 代理 /api 过来。

会话归属（FR-19）：服务端不提供会话列表——单用户场景，列表由前端
localStorage 维护；新会话在首条消息时创建，session_id 作为首个 SSE 事件
返回。

安全边界：仅绑定 127.0.0.1；session_id / artifact_id 走 Workspace 与
manifest 的既有校验（无任意路径参数）；设置接口返回的密钥打码，
明文只写本机 .env。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finance_agent.config import OUTPUTS_DIR, WEBAPP_DIST_DIR, Settings, SettingsStore
from finance_agent.session import SessionCore, read_history
from finance_agent.workspace import Workspace, WorkspaceError

_NO_DIST_HINT = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>finance-agent</title><body style="font-family:sans-serif;padding:40px;line-height:1.8">
<h2>前端尚未构建</h2>
<p>开发模式：项目根目录运行 <code>./scripts/dev.sh</code>（同时启动前后端，前端在 Vite 端口）。</p>
<p>生产模式：<code>npm --prefix webapp install && npm --prefix webapp run build</code> 后刷新本页。</p>
</body></html>"""


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class SettingsRequest(BaseModel):
    api_key: str | None = None        # None = 不修改；空串 = 清除
    base_url: str | None = None
    model: str | None = None
    tavily_api_key: str | None = None


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return secret[:6] + "…" + secret[-4:] if len(secret) > 12 else "已设置"


def _settings_required_text(settings: Settings) -> str:
    if not settings.api_key.strip():
        return "缺少 API 密钥。请先在左下角“设置”中填写 API Key，再开始新会话。"
    return "API Key 仍是占位符。请先在左下角“设置”中填写真实 API Key，再开始新会话。"


class SessionRegistry:
    """进程内会话池：chat 用的 SessionCore 懒加载 + 每会话一把执行锁。

    配置从 store 现取——设置弹窗保存后，新建/新恢复的会话用新配置，
    已在内存中的会话沿用其创建时的配置。

    容量淘汰（LRU）：core 持有 orchestrator + 四个 subagent + SQLite 句柄，
    "打开过的会话永驻内存"是慢性泄漏。超容量时淘汰最久未用且未持锁的会话
    ——状态全在盘上（session.db/工作区），再次访问经 resume 无损重建。
    """

    def __init__(self, store: SettingsStore, outputs_dir: Path, max_loaded: int = 8) -> None:
        self.store = store
        self.outputs_dir = outputs_dir
        self.max_loaded = max_loaded
        self._cores: dict[str, SessionCore] = {}   # 插入序即 LRU 序
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}  # 会话 → 运行中的轮次任务（供停止）

    def add(self, core: SessionCore) -> SessionCore:
        self._cores[core.workspace.session_id] = core
        self._evict()
        return core

    def create(self) -> SessionCore:
        return self.add(SessionCore.start(self.store.current, self.outputs_dir))

    def core(self, session_id: str) -> SessionCore:
        if session_id in self._cores:
            self._cores[session_id] = self._cores.pop(session_id)  # touch → 队尾
        else:
            try:
                self.add(SessionCore.resume(self.store.current, session_id, self.outputs_dir))
            except WorkspaceError as exc:
                raise HTTPException(404, str(exc)) from exc
        return self._cores[session_id]

    def _evict(self) -> None:
        while len(self._cores) > self.max_loaded:
            victim = next(
                (sid for sid in self._cores
                 if not (sid in self._locks and self._locks[sid].locked())),
                None,
            )
            if victim is None:
                return  # 全部在跑：宁可暂时超容量，不淘汰运行中的会话
            del self._cores[victim]

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

    def running(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return lock is not None and lock.locked()

    def set_task(self, session_id: str, task: asyncio.Task) -> None:
        self._tasks[session_id] = task

    def stop(self, session_id: str) -> bool:
        """取消该会话运行中的轮次。返回是否确有任务被取消。"""
        task = self._tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def start_locked_turn(
    core: SessionCore, lock: asyncio.Lock, message: str
) -> tuple[asyncio.Queue, asyncio.Task]:
    """在独立任务中执行一轮，事件经队列外发；锁随任务完成释放。

    锁的生命周期必须绑定"运行"而非"SSE 连接"：浏览器刷新会取消响应生成器，
    但 SDK 的运行任务并不随之停止——若锁跟着连接释放，用户再发消息就会与
    幽灵旧轮并发写同一 session.db 与工作区（历史交错、事件回调被改挂）。
    调用前必须已持有 lock（await lock.acquire()）。

    返回 (事件队列, 运行任务)——任务句柄供人工停止（task.cancel() 会沿
    stream_turn 一路取消到 SDK 的 result.cancel()）。
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def _run() -> None:
        try:
            async for event in core.stream_turn(message):
                queue.put_nowait(event)
        except asyncio.CancelledError:
            queue.put_nowait({
                "type": "error",
                "text": "任务已被手动停止（本轮未完成的结果不会进入对话历史；"
                        "已抓取的数据与溯源记录保留在工作区）。",
            })
            raise  # 保持任务的取消语义
        except Exception as exc:  # noqa: BLE001 —— 错误也以事件形式送达前端
            queue.put_nowait({"type": "error", "text": str(exc)})
        finally:
            queue.put_nowait(None)  # 结束哨兵
            lock.release()

    task = asyncio.get_running_loop().create_task(_run())
    return queue, task


def create_app(
    settings: Settings,
    *,
    outputs_dir: Path | None = None,
    initial_core: SessionCore | None = None,
    frontend_dist: Path | None = None,
    settings_store: SettingsStore | None = None,
) -> FastAPI:
    store = settings_store or SettingsStore(settings)
    registry = SessionRegistry(store, outputs_dir or OUTPUTS_DIR)
    dist = frontend_dist or WEBAPP_DIST_DIR
    initial_session_id = None
    if initial_core is not None:  # --web --resume <id>：并入前端左栏
        registry.add(initial_core)
        initial_session_id = initial_core.workspace.session_id

    app = FastAPI(title="finance-agent", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = dist / "index.html"
        return page.read_text(encoding="utf-8") if page.is_file() else _NO_DIST_HINT

    @app.get("/api/state")
    def state() -> dict:
        current = store.current
        return {
            "model": current.model,
            "base_url": current.base_url or "",
            "api_key_configured": current.mock_mode or current.has_api_key(),
            "initial_session_id": initial_session_id,
        }

    @app.get("/api/settings")
    def get_settings() -> dict:
        current = store.current
        return {
            "base_url": current.base_url or "",
            "model": current.model,
            "api_key_masked": _mask(current.api_key) if current.has_api_key() else "",
            "tavily_api_key_masked": (
                _mask(current.tavily_api_key) if current.has_tavily_api_key() else ""
            ),
        }

    @app.put("/api/settings")
    def put_settings(request: SettingsRequest) -> dict:
        store.update(**request.model_dump(exclude_none=True))
        return {"ok": True, "note": "已保存并写回 .env；对新建/新恢复的会话生效。",
                **get_settings()}

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        current = store.current
        if not (current.mock_mode or current.has_api_key()):
            async def settings_required_stream():
                yield _sse({
                    "type": "error",
                    "code": "settings_required",
                    "text": _settings_required_text(current),
                })

            return StreamingResponse(
                settings_required_stream(),
                media_type="text/event-stream",
            )

        core = registry.core(request.session_id) if request.session_id else registry.create()
        session_id = core.workspace.session_id
        lock = registry.lock(session_id)

        if lock.locked():
            # 一次一轮：并发消息直接拒绝，不排队（排队会让用户对着
            # 空屏等上一轮跑完，还以为是自己这条卡了）
            async def busy_stream():
                yield _sse({"type": "session", "session_id": session_id})
                yield _sse({"type": "error", "text": "该会话正在处理上一条消息，请等它完成后再发。"})

            return StreamingResponse(busy_stream(), media_type="text/event-stream")

        await lock.acquire()
        queue, task = start_locked_turn(core, lock, request.message)  # 锁由运行任务释放
        registry.set_task(session_id, task)

        async def event_stream():
            # 客户端断连只会取消本生成器；运行任务继续消化到完成并释放锁
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield _sse(event)

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
            "running": registry.running(session_id),  # 前端据此恢复运行态 UI
            "artifacts": workspace.list_artifacts(),
            "datasets": workspace.dataset_index(),
        }

    @app.post("/api/sessions/{session_id}/stop")
    def stop_session(session_id: str) -> dict:
        registry.workspace(session_id)  # 校验会话存在（不存在 → 404）
        return {"session_id": session_id, "stopped": registry.stop(session_id)}

    @app.get("/api/sessions/{session_id}/artifacts/{artifact_id}/file")
    def artifact_file(
        session_id: str,
        artifact_id: str,
        version: int | None = None,
        download: bool = False,
    ) -> FileResponse:
        workspace = registry.workspace(session_id)
        record = workspace.manifest().get(artifact_id)
        if record is None:
            raise HTTPException(404, f"产物不存在：{artifact_id}")
        v = version or record.current_version
        matches = [item for item in record.versions if item.v == v]
        if not matches:
            raise HTTPException(404, f"版本不存在：{artifact_id} v{v}")
        path = workspace.dir / matches[0].file
        disposition = "attachment" if download or record.kind != "html" else "inline"
        return FileResponse(path, filename=path.name, content_disposition_type=disposition)

    if (dist / "assets").is_dir():  # Vite 构建产物的静态资源
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

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
