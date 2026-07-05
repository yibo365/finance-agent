"""工作区单测：WorkspaceFS 守卫、manifest 版本管理、产物四操作、原子性。"""

import json

import pandas as pd
import pytest

from finance_agent.artifacts.spec import ArtifactSpec
from finance_agent.workspace import Workspace, WorkspaceError


@pytest.fixture()
def ws(tmp_path):
    return Workspace.create(tmp_path / "outputs", "s-20260703-test")


@pytest.fixture()
def df():
    return pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [48.1, 47.5, 46.8],
            "high": [49.0, 48.2, 47.0],
            "low": [47.8, 47.0, 46.0],
            "close": [48.8, 47.6, 46.5],
            "volume": [1000, 1100, 1200],
        }
    )


def html_spec(narrative="首版分析。", heading="一、结论", **overrides):
    payload = {
        "artifact_id": "nvda-kline-report",
        "kind": "html",
        "title": "NVDA 复盘",
        "skill": "kline-html-report",
        "blocks": [
            {"type": "heading", "text": heading},
            {"type": "narrative", "text": narrative},
            {"type": "kline_chart", "data_ref": "ds-nvda", "ticker": "NVDA"},
        ],
    }
    payload.update(overrides)
    return ArtifactSpec.model_validate(payload)


# ---------- 生命周期 ----------

def test_create_layout_and_duplicate_rejected(tmp_path):
    ws = Workspace.create(tmp_path / "outputs")
    assert ws.session_id.startswith("s-")
    for sub in ("artifacts", "specs", "data"):
        assert (ws.dir / sub).is_dir()
    with pytest.raises(WorkspaceError, match="已存在"):
        Workspace.create(tmp_path / "outputs", ws.session_id)


def test_open_missing_lists_available(tmp_path):
    Workspace.create(tmp_path / "outputs", "s-20260703-aaaa")
    with pytest.raises(WorkspaceError, match="s-20260703-aaaa"):
        Workspace.open(tmp_path / "outputs", "s-20260703-nope")


# ---------- WorkspaceFS 守卫 ----------

def test_join_rejects_traversal_and_absolute(ws):
    with pytest.raises(WorkspaceError, match="非法文件名"):
        ws._join("data", "../escape.json")
    with pytest.raises(WorkspaceError, match="非法文件名"):
        ws._join("data", "/etc/passwd")


def test_guard_rejects_symlink_escape(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws.dir / "data" / "link").symlink_to(outside)
    with pytest.raises(WorkspaceError, match="路径越界"):
        ws._join("data", "link/evil.json")


def test_append_only_write(ws):
    target = ws.dir / "artifacts" / "a_v1.html"
    ws._write_text(target, "v1")
    with pytest.raises(WorkspaceError, match="append-only"):
        ws._write_text(target, "覆盖尝试")
    assert target.read_text() == "v1"


def test_file_size_limit(tmp_path, df):
    small = Workspace.create(tmp_path / "outputs", "s-20260703-tiny", max_file_bytes=1024)
    small.store_dataset("ds-nvda", df, ticker="NVDA")
    with pytest.raises(WorkspaceError, match="大小上限"):
        small.render_artifact(html_spec())


# ---------- dataset 注册表 ----------

def test_dataset_roundtrip_and_index(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA", source="Yahoo", evidence_id="ev-x-1")
    loaded = ws.load_dataset("ds-nvda")
    assert loaded["close"].tolist() == df["close"].tolist()
    entry = ws.dataset_index()["ds-nvda"]
    assert entry["rows"] == 3 and entry["evidence_id"] == "ev-x-1"
    # 缓存格式兼容 LocalCacheSource：含 symbol 与 rows
    payload = json.loads((ws.dir / "data" / "ds-nvda.json").read_text())
    assert payload["symbol"] == "NVDA" and len(payload["rows"]) == 3


def test_dataset_bad_id_and_missing(ws, df):
    with pytest.raises(WorkspaceError, match="非法 dataset_id"):
        ws.store_dataset("../evil", df)
    with pytest.raises(WorkspaceError, match="未登记"):
        ws.load_dataset("ds-ghost")


# ---------- 产物操作 ----------

def test_render_v1_and_manifest(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    version = ws.render_artifact(html_spec())
    assert version.v == 1
    assert (ws.dir / version.file).is_file()
    assert (ws.dir / version.spec).is_file()
    record = ws.manifest().get("nvda-kline-report")
    assert record.current_version == 1
    with pytest.raises(WorkspaceError, match="已存在"):
        ws.render_artifact(html_spec())


def test_update_creates_v2_keeps_v1_and_untouched_blocks(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    v1 = ws.render_artifact(html_spec(narrative="首版分析。"))

    new_spec = html_spec(narrative="修订后的分析段落。")
    v2 = ws.update_artifact("nvda-kline-report", new_spec, change_summary="改写分析段")
    assert v2.v == 2
    # 旧版保留（append-only）
    assert (ws.dir / v1.file).is_file() and (ws.dir / v1.spec).is_file()
    # 定点生效：新版含新叙事，未涉及的 heading 原样保留；旧版内容不变
    html_v2 = (ws.dir / v2.file).read_text(encoding="utf-8")
    assert "修订后的分析段落。" in html_v2 and "一、结论" in html_v2
    assert "首版分析。" in (ws.dir / v1.file).read_text(encoding="utf-8")
    record = ws.manifest().get("nvda-kline-report")
    assert [item.change_summary for item in record.versions] == ["初版", "改写分析段"]


def test_update_guards_identity(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    ws.render_artifact(html_spec())
    with pytest.raises(WorkspaceError, match="不存在"):
        ws.update_artifact("ghost", html_spec(artifact_id="ghost"), change_summary="x")
    with pytest.raises(WorkspaceError, match="artifact_id 不可变更"):
        ws.update_artifact(
            "nvda-kline-report", html_spec(artifact_id="other-id"), change_summary="x"
        )


def test_failed_render_leaves_no_trace(ws):
    # dataset 未登记 → 渲染失败 → 不得出现任何产物文件或 manifest 记录
    with pytest.raises(WorkspaceError, match="未登记"):
        ws.render_artifact(html_spec())
    assert ws.manifest().artifacts == []
    assert list((ws.dir / "artifacts").iterdir()) == []
    assert list((ws.dir / "specs").iterdir()) == []


def test_read_spec_current_and_historic(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    ws.render_artifact(html_spec(narrative="v1 段落"))
    ws.update_artifact("nvda-kline-report", html_spec(narrative="v2 段落"), change_summary="改")
    assert "v2 段落" in ws.read_artifact_spec("nvda-kline-report").blocks[1].text
    assert "v1 段落" in ws.read_artifact_spec("nvda-kline-report", version=1).blocks[1].text


def test_list_artifacts_summary(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    ws.render_artifact(html_spec())
    ws.update_artifact("nvda-kline-report", html_spec(title="NVDA 复盘 v2"), change_summary="改标题")
    [summary] = ws.list_artifacts()
    assert summary["current_version"] == 2
    assert summary["title"] == "NVDA 复盘 v2"
    assert len(summary["history"]) == 2


def test_dangling_evidence_refs_rejected(ws, df):
    # 真实事故：report-builder 自造语义化 evidence id，页面锚点全部悬空
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    spec = html_spec()
    spec.blocks[1].evidence_refs = ["ev-match-deepseek"]
    with pytest.raises(WorkspaceError, match="悬空"):
        ws.render_artifact(spec)
    # 真实 id 通过
    real = ws.evidence.record("market_data", source_url="https://x", excerpt="行情")
    spec.blocks[1].evidence_refs = [real.id]
    assert ws.render_artifact(spec).v == 1


def test_event_without_source_url_rejected(ws, df):
    # PRD 硬要求确定性化：事件必须带可点击原文 URL，不靠 prompt 自律
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    spec = html_spec()
    spec.blocks[2].events = [
        {"date": "2024-01-03", "title": "无来源事件", "impact": 3, "sources": []}
    ]
    spec = ArtifactSpec.model_validate(spec.model_dump())
    with pytest.raises(WorkspaceError, match="缺少可点击的原始来源"):
        ws.render_artifact(spec)


def test_event_fabricated_source_url_rejected(ws, df):
    # 真实事故：事件材料传递中丢了 sources，builder 编造 reuters 链接（404）
    # 通过了存在性校验——URL 必须对照溯源记录做成员校验
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    news = ws.evidence.record(
        "news",
        source_url="https://hn.example/api?q=chatgpt",
        urls=["https://openai.example/index/chatgpt/"],
        excerpt="ChatGPT",
    )
    spec = html_spec()
    spec.blocks[2].events = [
        {
            "date": "2024-01-03", "title": "编造链接事件", "impact": 3,
            "sources": [{"name": "Reuters", "url": "https://reuters.example/made-up-slug/"}],
            "evidence_refs": [news.id],
        }
    ]
    spec = ArtifactSpec.model_validate(spec.model_dump())
    with pytest.raises(WorkspaceError, match="不在溯源记录"):
        ws.render_artifact(spec)
    # 出自 evidence.urls 的真实 URL 通过
    spec.blocks[2].events[0].sources[0].url = "https://openai.example/index/chatgpt/"
    assert ws.render_artifact(spec).v == 1


def test_event_refs_must_include_news_or_search(ws, df):
    # 真实事故：25 个事件的 evidence_refs 全部挂到行情数据 evidence，回链失真
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    market = ws.evidence.record("market_data", source_url="https://query1.example/chart")
    news = ws.evidence.record(
        "news", source_url="mock://hn-offline", urls=["https://openai.example/index/chatgpt/"]
    )
    spec = html_spec()
    spec.blocks[2].events = [
        {
            "date": "2024-01-03", "title": "回链挂错事件", "impact": 3,
            "sources": [{"name": "OpenAI", "url": "https://openai.example/index/chatgpt/"}],
            "evidence_refs": [market.id],
        }
    ]
    spec = ArtifactSpec.model_validate(spec.model_dump())
    with pytest.raises(WorkspaceError, match="资讯/检索类"):
        ws.render_artifact(spec)
    spec.blocks[2].events[0].evidence_refs = [news.id]
    assert ws.render_artifact(spec).v == 1


def _events_material(ws, *, with_evidence=True):
    refs, urls = [], []
    if with_evidence:
        ev = ws.evidence.record("search", source_url="https://reuters.example/a",
                                urls=["https://reuters.example/a"])
        refs, urls = [ev.id], ["https://reuters.example/a"]
    return ws.store_material("events", {
        "events": [
            {"date": "2024-01-03", "title": "材料事件A", "impact": 4, "direction": "up",
             "category": "事件", "move": "", "notes": "",
             "sources": [{"name": "Reuters", "url": u} for u in urls],
             "evidence_refs": refs},
            {"date": "2024-01-04", "title": "材料事件B", "impact": 3, "direction": "down",
             "category": "事件", "move": "", "notes": "",
             "sources": [{"name": "Reuters", "url": u} for u in urls],
             "evidence_refs": refs},
        ],
        "coverage_notes": "x",
    })


def test_kline_material_refs_resolved_at_render(ws, df):
    # 真实事故：24 事件+40 变化点内联进 spec 超输出上限被截断——改为按引用注入
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    mid = _events_material(ws)
    cp_mid = ws.store_material("market", {
        "datasets": [{"dataset_id": "ds-nvda", "changepoints": [
            {"date": "2024-01-03", "kind": "drawdown", "rule": "r", "severity": 2,
             "window": ["2024-01-02", "2024-01-04"], "evidence_refs": []},
        ]}],
        "echo": "",
    })
    spec = html_spec()
    spec.blocks[2].events_material = mid
    spec.blocks[2].changepoints_material = cp_mid
    version = ws.render_artifact(spec)
    html = (ws.dir / version.file).read_text(encoding="utf-8")
    assert "材料事件A" in html and "材料事件B" in html    # 材料事件注入渲染
    # spec 快照保留紧凑引用形态（不落全量），update 流程读回仍是引用
    snapshot = ws.read_artifact_spec("nvda-kline-report")
    assert snapshot.blocks[2].events_material == mid
    assert snapshot.blocks[2].events == []


def test_kline_inline_overrides_material_entry(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    ev = ws.evidence.record("search", source_url="https://cnbc.example/b",
                            urls=["https://cnbc.example/b"])
    mid = _events_material(ws)
    spec = html_spec()
    spec.blocks[2].events_material = mid
    spec.blocks[2].events = [  # 与材料事件A 同日期同标题 → 内联覆盖
        {"date": "2024-01-03", "title": "材料事件A", "impact": 5, "direction": "down",
         "sources": [{"name": "CNBC", "url": "https://cnbc.example/b"}],
         "evidence_refs": [ev.id]},
    ]
    spec = ArtifactSpec.model_validate(spec.model_dump())
    spec.blocks[2].events_material = mid
    version = ws.render_artifact(spec)
    html = (ws.dir / version.file).read_text(encoding="utf-8")
    assert "cnbc.example" in html                       # 覆盖后的来源生效
    assert html.count("材料事件A") >= 1 and "材料事件B" in html


def test_kline_material_wrong_shape_rejected(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    bad = ws.store_material("alignment", {"entries": []})   # 不是事件材料
    spec = html_spec()
    spec.blocks[2].events_material = bad
    with pytest.raises(WorkspaceError, match="不是事件列表材料"):
        ws.render_artifact(spec)


def test_unknown_skill_rejected(ws, df):
    ws.store_dataset("ds-nvda", df, ticker="NVDA")
    spec = html_spec(skill="no-such-skill")
    with pytest.raises(WorkspaceError, match="skill 不存在"):
        ws.render_artifact(spec)
