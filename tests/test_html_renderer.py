"""HTML 渲染器单测：spec fixture → 断言产物结构、转义、内嵌与溯源锚点。"""

import pandas as pd
import pytest

from finance_agent.artifacts.renderers.html import RenderError, render_html
from finance_agent.artifacts.spec import ArtifactSpec
from finance_agent.provenance import EvidenceLog
from finance_agent.skills.loader import scan_skills


@pytest.fixture(scope="module")
def skill():
    return scan_skills()["kline-html-report"]


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


@pytest.fixture()
def evidence_map():
    log = EvidenceLog("t")
    log.record("market_data", source_url="https://query1.finance.yahoo.com/x", excerpt="3 行")
    log.record("news", source_url="https://news.ycombinator.com/item?id=1", excerpt="标题")
    return {ev.id: ev for ev in log.items()}


def build_spec(**chart_overrides):
    chart = {
        "type": "kline_chart",
        "data_ref": "ds-1",
        "ticker": "NVDA",
        "events": [
            {
                "date": "2024-01-03",
                "title": "示例事件 <script>alert(1)</script>",
                "category": "测试",
                "direction": "up",
                "impact": 5,
                "sources": [{"name": "HN", "url": "https://news.ycombinator.com/item?id=1"}],
                "evidence_refs": ["ev-t-2"],
            }
        ],
        "changepoints": [
            {
                "date": "2024-01-04",
                "kind": "accel_down",
                "rule": "单日收益 -2.3σ",
                "severity": 2,
                "window": ["2024-01-02", "2024-01-04"],
                "evidence_refs": ["ev-t-1"],
            }
        ],
    }
    chart.update(chart_overrides)
    return ArtifactSpec.model_validate(
        {
            "artifact_id": "nvda-kline-report",
            "kind": "html",
            "title": "NVDA 复盘 <b>加粗注入</b>",
            "subtitle": "测试副标题",
            "skill": "kline-html-report",
            "blocks": [
                {"type": "heading", "text": "一、结论"},
                {"type": "narrative", "text": "首段。\n\n次段引用数据。", "evidence_refs": ["ev-t-1"]},
                chart,
                {
                    "type": "changepoint_table",
                    "changepoints": chart["changepoints"],
                },
            ],
        }
    )


def test_render_produces_self_contained_html(df, evidence_map, skill):
    html = render_html(build_spec(), datasets={"ds-1": df}, evidence=evidence_map, skill=skill)
    # 自包含：无任何会触发网络请求的外链资源（script src / 外链样式表）。
    # 注意不能朴素断言 "https://cdn" not in html：内联的 Plotly 源码含
    # cdn.plot.ly 字符串常量（geo topojson 默认地址，本报告不用 geo 图不会请求）。
    assert "<script src=" not in html
    assert '<link rel="stylesheet" href="http' not in html
    # 数据与渲染骨架内嵌
    assert "window.__REPORT_PAYLOAD__" in html
    assert "KlineReport.init" in html
    assert '"2024-01-02"' in html
    # 溯源锚点与回链
    assert 'id="ev-t-1"' in html and 'id="ev-t-2"' in html
    assert 'href="#ev-t-1"' in html
    # 变化点表已渲染
    assert "加速下跌" in html and "单日收益 -2.3σ" in html


def test_untrusted_text_is_escaped(df, evidence_map, skill):
    html = render_html(build_spec(), datasets={"ds-1": df}, evidence=evidence_map, skill=skill)
    assert "<b>加粗注入</b>" not in html            # 标题转义
    assert "&lt;b&gt;加粗注入&lt;/b&gt;" in html
    assert "<script>alert(1)</script>" not in html   # 事件标题进 payload 也不能闭合 script
    assert "<\\/script>" in html                     # JSON 内 </ 已转义


def test_missing_dataset_rejected(df, evidence_map, skill):
    with pytest.raises(RenderError, match="dataset 未登记"):
        render_html(build_spec(data_ref="ds-missing"),
                    datasets={"ds-1": df}, evidence=evidence_map, skill=skill)


def test_requires_exactly_one_kline_chart(df, evidence_map, skill):
    spec = ArtifactSpec.model_validate(
        {
            "artifact_id": "no-chart",
            "kind": "html",
            "title": "无图",
            "blocks": [{"type": "heading", "text": "x"}],
        }
    )
    with pytest.raises(RenderError, match="kline_chart"):
        render_html(spec, datasets={}, evidence={}, skill=skill)


def test_kind_mismatch_rejected(df, evidence_map, skill):
    spec = build_spec()
    docx_spec = spec.model_copy(update={"kind": "docx"})
    with pytest.raises(RenderError, match="kind 不匹配"):
        render_html(docx_spec, datasets={"ds-1": df}, evidence=evidence_map, skill=skill)
