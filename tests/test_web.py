"""Web 薄层单测：多会话 API（FR-19）+ SSE 事件流 + 历史重建（stream_turn 打桩，不调 LLM）。"""

import json
import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from finance_agent.artifacts.spec import ArtifactSpec
from finance_agent.config import Settings
from finance_agent.session import SessionCore, read_history
from finance_agent.web.app import create_app
from finance_agent.workspace import Workspace

MOCK = Settings(mock_mode=True)
SEEDED = "s-20260703-web"


@pytest.fixture()
def outputs(tmp_path):
    """预置一个带 dataset + 产物的历史会话工作区。"""
    base = tmp_path / "outputs"
    workspace = Workspace.create(base, SEEDED)
    df = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1], "low": [0.9, 1.9, 2.9],
        "close": [1.0, 2.0, 3.0], "volume": [10, 20, 30],
    })
    workspace.store_dataset("ds-nvda", df, ticker="NVDA")
    workspace.render_artifact(ArtifactSpec.model_validate({
        "artifact_id": "nvda-kline-report", "kind": "html", "title": "NVDA 复盘",
        "blocks": [{"type": "kline_chart", "data_ref": "ds-nvda", "ticker": "NVDA"}],
    }))
    return base


@pytest.fixture()
def client(outputs):
    return TestClient(create_app(MOCK, outputs_dir=outputs))


async def _fake_stream(self, _text):
    yield {"type": "session", "session_id": self.workspace.session_id}
    yield {"type": "agent_start", "agent": "data-collector"}
    yield {"type": "tool_call", "agent": "data-collector",
           "tool": "fetch_market_data", "detail": "{}"}
    yield {"type": "delta", "text": "已生成"}
    yield {"type": "done", "reply": "已生成 [nvda-kline-report v1]", "artifacts": []}


def _sse_events(body: str) -> list[dict]:
    return [json.loads(frame[6:]) for frame in body.split("\n\n") if frame.startswith("data: ")]


# ---------- 静态页与启动信息 ----------

def test_index_served_inline(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "finance-agent 投研工作台" in resp.text
    assert "<script src=" not in resp.text  # 前端无外部资源


def test_state_reports_server_info_without_session_list(client):
    state = client.get("/api/state").json()
    assert state["provider"] == "openai" and state["model"]
    assert state["initial_session_id"] is None
    assert "sessions" not in state  # 会话列表归前端 localStorage（FR-19 非目标）


def test_state_carries_resumed_session(outputs):
    core = SessionCore(MOCK, Workspace.open(outputs, SEEDED))
    client = TestClient(create_app(MOCK, outputs_dir=outputs, initial_core=core))
    assert client.get("/api/state").json()["initial_session_id"] == SEEDED


# ---------- 按会话的状态 / 产物文件 ----------

def test_session_state_reports_artifacts_and_datasets(client):
    state = client.get(f"/api/sessions/{SEEDED}/state").json()
    assert state["session_id"] == SEEDED
    assert state["artifacts"][0]["artifact_id"] == "nvda-kline-report"
    assert "ds-nvda" in state["datasets"]
    assert client.get("/api/sessions/s-20990101-dead/state").status_code == 404


def test_artifact_file_download_and_404(client):
    resp = client.get(f"/api/sessions/{SEEDED}/artifacts/nvda-kline-report/file")
    assert resp.status_code == 200
    assert "window.__REPORT_PAYLOAD__" in resp.text
    assert client.get(f"/api/sessions/{SEEDED}/artifacts/ghost/file").status_code == 404
    assert client.get(
        f"/api/sessions/{SEEDED}/artifacts/nvda-kline-report/file?version=9"
    ).status_code == 404


# ---------- 聊天：会话创建与事件流 ----------

def test_chat_without_session_creates_one_and_returns_id(client, outputs, monkeypatch):
    monkeypatch.setattr(SessionCore, "stream_turn", _fake_stream)
    with client.stream("POST", "/api/chat", json={"message": "任务"}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _sse_events("".join(resp.iter_text()))
    assert events[0]["type"] == "session"  # 首个事件必为 session（前端据此入 localStorage）
    new_id = events[0]["session_id"]
    assert new_id.startswith("s-") and new_id != SEEDED
    assert (outputs / new_id).is_dir()  # 工作区确实建了
    assert {e["type"] for e in events} >= {"agent_start", "tool_call", "delta", "done"}


def test_chat_routes_to_existing_session(client, monkeypatch):
    monkeypatch.setattr(SessionCore, "stream_turn", _fake_stream)
    with client.stream("POST", "/api/chat", json={"message": "改一下", "session_id": SEEDED}) as resp:
        events = _sse_events("".join(resp.iter_text()))
    assert events[0] == {"type": "session", "session_id": SEEDED}


def test_chat_unknown_session_is_404(client):
    assert client.post(
        "/api/chat", json={"message": "x", "session_id": "s-20990101-dead"}
    ).status_code == 404


def test_chat_surfaces_errors_as_events(client, monkeypatch):
    async def broken_stream(self, _text):
        raise RuntimeError("模拟失败")
        yield  # pragma: no cover

    monkeypatch.setattr(SessionCore, "stream_turn", broken_stream)
    with client.stream("POST", "/api/chat", json={"message": "任务", "session_id": SEEDED}) as resp:
        body = "".join(resp.iter_text())
    assert '"error"' in body and "模拟失败" in body


# ---------- 历史重建（read_history） ----------

def test_read_history_rebuilds_display_messages(tmp_path):
    db = tmp_path / "session.db"
    rows = [
        {"role": "user", "content": "回顾苹果近5年行情"},
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "内部推理"}]},
        {"type": "function_call", "name": "run_data_collector",
         "call_id": "c1", "arguments": '{"brief": {}}'},
        {"type": "function_call_output", "call_id": "c1", "output": '{"datasets": []}'},
        {"type": "function_call", "name": "run_report_builder",
         "call_id": "c2", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c2",
         "output": "An error occurred while running the tool. Please try again."},
        {"role": "assistant", "content": [{"type": "output_text", "text": "报告已生成"}]},
    ]
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE agent_messages "
                     "(id INTEGER PRIMARY KEY, session_id TEXT, message_data TEXT)")
        conn.executemany(
            "INSERT INTO agent_messages (session_id, message_data) VALUES (?, ?)",
            [("s-x", json.dumps(r)) for r in rows],
        )
    messages = read_history(db, "s-x")
    assert [m["role"] for m in messages] == ["user", "action", "action", "assistant"]
    assert messages[0]["text"] == "回顾苹果近5年行情"
    assert messages[1]["tool"] == "run_data_collector" and messages[1]["ok"] is True
    assert messages[2]["tool"] == "run_report_builder" and messages[2]["ok"] is False
    assert messages[3]["text"] == "报告已生成"
    assert all("内部推理" not in json.dumps(m, ensure_ascii=False) for m in messages)


def test_read_history_missing_db_returns_empty(tmp_path):
    assert read_history(tmp_path / "nope.db", "s-x") == []


def test_messages_endpoint_serves_history(client, outputs):
    # 空会话（无 session.db）→ 空历史；未知会话 404
    assert client.get(f"/api/sessions/{SEEDED}/messages").json() == {
        "session_id": SEEDED, "messages": [],
    }
    assert client.get("/api/sessions/s-20990101-dead/messages").status_code == 404


# ---------- 端口预检与工作区守卫（行为不变） ----------

def test_ensure_port_available_rejects_occupied_port():
    import socket

    from finance_agent.web.app import ensure_port_available

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
        occupier.bind(("127.0.0.1", 0))
        occupier.listen(1)
        port = occupier.getsockname()[1]
        with pytest.raises(SystemExit, match=f"--port {port + 1}"):
            ensure_port_available(port)


def test_ensure_port_available_passes_on_free_port():
    import socket

    from finance_agent.web.app import ensure_port_available

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    ensure_port_available(free_port)  # 不应抛出


def test_run_turn_fails_fast_when_workspace_deleted(outputs):
    import asyncio
    import shutil

    core = SessionCore(MOCK, Workspace.open(outputs, SEEDED))
    shutil.rmtree(core.workspace.dir)
    with pytest.raises(RuntimeError, match="工作区已不存在"):
        asyncio.run(core.run_turn("任意输入"))
