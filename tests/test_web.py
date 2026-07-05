"""Web 薄层单测：状态/产物文件路由 + SSE 事件流（stream_turn 打桩，不调 LLM）。"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from finance_agent.artifacts.spec import ArtifactSpec
from finance_agent.config import Settings
from finance_agent.session import SessionCore
from finance_agent.web.app import create_app
from finance_agent.workspace import Workspace

MOCK = Settings(openai_api_key="", model="gpt-5.5", mock_mode=True)


@pytest.fixture()
def core(tmp_path):
    workspace = Workspace.create(tmp_path / "outputs", "s-20260703-web")
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
    return SessionCore(MOCK, workspace)


@pytest.fixture()
def client(core):
    return TestClient(create_app(core))


def test_index_served_inline(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "finance-agent 投研工作台" in resp.text
    assert "<script src=" not in resp.text  # 前端无外部资源


def test_state_reports_artifacts_and_datasets(client):
    state = client.get("/api/state").json()
    assert state["session_id"] == "s-20260703-web"
    assert state["artifacts"][0]["artifact_id"] == "nvda-kline-report"
    assert "ds-nvda" in state["datasets"]


def test_artifact_file_download_and_404(client):
    resp = client.get("/api/artifacts/nvda-kline-report/file")
    assert resp.status_code == 200
    assert "window.__REPORT_PAYLOAD__" in resp.text
    assert client.get("/api/artifacts/ghost/file").status_code == 404
    assert client.get("/api/artifacts/nvda-kline-report/file?version=9").status_code == 404


def test_chat_streams_sse_events(core, client, monkeypatch):
    async def fake_stream(_text):
        yield {"type": "status", "text": "正在调用 run_data_collector…"}
        yield {"type": "delta", "text": "已生成"}
        yield {"type": "done", "reply": "已生成 [nvda-kline-report v1]"}

    monkeypatch.setattr(core, "stream_turn", fake_stream)
    with client.stream("POST", "/api/chat", json={"message": "任务"}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    assert '"status"' in body and '"delta"' in body and '"done"' in body


def test_chat_surfaces_errors_as_events(core, client, monkeypatch):
    async def broken_stream(_text):
        raise RuntimeError("模拟失败")
        yield  # pragma: no cover

    monkeypatch.setattr(core, "stream_turn", broken_stream)
    with client.stream("POST", "/api/chat", json={"message": "任务"}) as resp:
        body = "".join(resp.iter_text())
    assert '"error"' in body and "模拟失败" in body
