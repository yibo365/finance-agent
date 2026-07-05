"""FastAPI 薄层：聊天（SSE 流式）+ 产物面板 + 产物文件下载。

安全边界：仅绑定 127.0.0.1；文件下载只服务 manifest 已登记的产物
（不接受任意路径参数）；前端为单文件原生 JS，无构建链、无外部资源。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from finance_agent.session import SessionCore

_STATIC = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str


def create_app(core: SessionCore) -> FastAPI:
    app = FastAPI(title="finance-agent", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> dict:
        return {
            "session_id": core.workspace.session_id,
            "workspace_dir": str(core.workspace.dir),
            "provider": core.settings.provider,
            "model": core.settings.model,
            "artifacts": core.workspace.list_artifacts(),
            "datasets": core.workspace.dataset_index(),
        }

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        async def event_stream():
            try:
                async for event in core.stream_turn(request.message):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001 —— 错误也以事件形式送达前端
                payload = {"type": "error", "text": str(exc)}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/artifacts/{artifact_id}/file")
    def artifact_file(artifact_id: str, version: int | None = None) -> FileResponse:
        record = core.workspace.manifest().get(artifact_id)
        if record is None:
            raise HTTPException(404, f"产物不存在：{artifact_id}")
        v = version or record.current_version
        matches = [item for item in record.versions if item.v == v]
        if not matches:
            raise HTTPException(404, f"版本不存在：{artifact_id} v{v}")
        path = core.workspace.dir / matches[0].file
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


def serve(core: SessionCore, port: int = 8765) -> None:
    import uvicorn

    print(f"Web 界面：http://127.0.0.1:{port}（会话 {core.workspace.session_id}）")
    uvicorn.run(create_app(core), host="127.0.0.1", port=port, log_level="warning")
