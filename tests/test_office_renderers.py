"""office 三渲染器单测：渲染 → 用对应库重新打开 → 断言结构与公式。"""

import pandas as pd
import pytest
from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from finance_agent.artifacts.spec import ArtifactSpec
from finance_agent.provenance import EvidenceLog
from finance_agent.workspace import Workspace


@pytest.fixture()
def ws(tmp_path):
    workspace = Workspace.create(tmp_path / "outputs", "s-20260703-office")
    dates_5d = pd.date_range("2024-01-01", periods=60, freq="B").strftime("%Y-%m-%d")
    dates_7d = pd.date_range("2024-01-01", periods=80, freq="D").strftime("%Y-%m-%d")

    def make(dates, base):
        closes = [base + i * 0.5 for i in range(len(dates))]
        return pd.DataFrame({
            "date": dates, "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "volume": [1000] * len(dates),
        })

    workspace.store_dataset("ds-gold", make(dates_5d, 2000), ticker="GC=F")
    workspace.store_dataset("ds-btc", make(dates_7d, 40000), ticker="BTC-USD")
    workspace.evidence.record("market_data", source_url="https://example.com/gold", excerpt="60 行")
    workspace.save_evidence()
    return workspace


def test_xlsx_backtest_formulas_and_sheets(ws):
    spec = ArtifactSpec.model_validate({
        "artifact_id": "gold-btc-backtest",
        "kind": "xlsx",
        "title": "黄金 vs 比特币回测底稿",
        "blocks": [
            {"type": "heading", "text": "口径声明"},
            {"type": "narrative", "text": "多资产按共同交易日对齐。",
             "evidence_refs": ["ev-s-20260703-office-1"]},
            {"type": "data_sheet", "sheet_name": "黄金", "data_ref": "ds-gold", "ticker": "GC=F"},
            {"type": "data_sheet", "sheet_name": "比特币", "data_ref": "ds-btc", "ticker": "BTC-USD"},
            {"type": "metrics_sheet", "data_refs": ["ds-gold", "ds-btc"],
             "labels": ["黄金", "比特币"], "rolling_window": 20},
        ],
    })
    version = ws.render_artifact(spec)
    wb = load_workbook(ws.dir / version.file)
    assert {"说明", "黄金", "比特币", "参数", "对齐", "指标", "汇总", "图表", "溯源"} <= set(wb.sheetnames)
    # 参数化：窗口值在参数 sheet，指标公式引用它（OFFSET 联动）
    assert wb["参数"]["B2"].value == 20
    vol_formula = wb["指标"]["E30"].value  # 黄金滚动波动列
    assert isinstance(vol_formula, str) and "OFFSET" in vol_formula and "'参数'!$B$2" in vol_formula
    # 日收益、回撤是公式非数值
    assert str(wb["指标"]["B3"].value).startswith("=")
    assert "MAX(" in wb["指标"]["D10"].value
    # 汇总含相关系数公式
    summary_formulas = [row[1].value for row in wb["汇总"].iter_rows(min_row=2)]
    assert any(isinstance(v, str) and "CORREL" in v for v in summary_formulas)
    # 对齐 sheet 行数 = 共同交易日（工作日 60 天 ⊂ 自然日 80 天中的工作日）
    assert wb["对齐"].max_row - 1 <= 60
    # 溯源 sheet 有记录
    assert wb["溯源"].max_row >= 2


def test_xlsx_requires_data_sheet(ws):
    spec = ArtifactSpec.model_validate({
        "artifact_id": "empty-backtest", "kind": "xlsx", "title": "空",
        "blocks": [{"type": "heading", "text": "x"}],
    })
    with pytest.raises(Exception, match="data_sheet"):
        ws.render_artifact(spec)


def test_pptx_slides_and_evidence_page(ws):
    spec = ArtifactSpec.model_validate({
        "artifact_id": "gold-btc-deck",
        "kind": "pptx",
        "title": "黄金与比特币：避险与抗通胀比较",
        "blocks": [
            {"type": "slide", "layout": "title", "title": "黄金与比特币：避险与抗通胀比较",
             "subtitle": "决策框架 v1"},
            {"type": "slide", "layout": "section", "title": "一、比较框架"},
            {"type": "slide", "layout": "bullets", "title": "核心结论",
             "bullets": ["黄金危机相关性更稳定", "比特币波动率显著更高"],
             "evidence_refs": ["ev-s-20260703-office-1"]},
            {"type": "slide", "layout": "table", "title": "多维对照",
             "table_headers": ["维度", "黄金", "比特币"],
             "table_rows": [["年化波动", "低", "高"], ["历史长度", "长", "短"]]},
        ],
    })
    version = ws.render_artifact(spec)
    prs = Presentation(ws.dir / version.file)
    assert len(prs.slides) == 5  # 4 页 spec + 1 页自动溯源清单
    titles = [s.shapes.title.text for s in prs.slides if s.shapes.title]
    assert "数据来源与溯源清单" in titles
    # 表格页确实有表格
    table_slide = prs.slides[3]
    assert any(shape.has_table for shape in table_slide.shapes)


def test_pptx_rejects_non_slide_blocks(ws):
    spec = ArtifactSpec.model_validate({
        "artifact_id": "bad-deck", "kind": "pptx", "title": "x",
        "blocks": [{"type": "heading", "text": "不该出现在PPT里"}],
    })
    with pytest.raises(Exception, match="不支持"):
        ws.render_artifact(spec)


def test_docx_structure_and_appendix(ws):
    spec = ArtifactSpec.model_validate({
        "artifact_id": "gold-btc-report",
        "kind": "docx",
        "title": "黄金与比特币策略研究报告",
        "subtitle": "避险/抗通胀属性比较",
        "blocks": [
            {"type": "heading", "text": "一、摘要"},
            {"type": "narrative", "text": "黄金的危机期相关性更稳定。\n\n比特币波动率更高。",
             "evidence_refs": ["ev-s-20260703-office-1"]},
            {"type": "heading", "text": "二、数据与方法"},
            {"type": "table", "caption": "数据说明", "headers": ["标的", "区间"],
             "rows": [["GC=F", "2024"], ["BTC-USD", "2024"]]},
            {"type": "changepoint_table", "changepoints": [
                {"date": "2024-02-01", "kind": "drawdown", "rule": "回撤 -16%",
                 "severity": 3, "window": ["2024-01-15", "2024-02-01"]},
            ]},
        ],
    })
    version = ws.render_artifact(spec)
    doc = Document(str(ws.dir / version.file))
    texts = [p.text for p in doc.paragraphs]
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert "一、摘要" in headings and "附录：溯源明细" in headings
    assert any("ev-s-20260703-office-1" in t for t in texts)   # 正文溯源标记 + 附录
    assert any("不构成投资建议" in t for t in texts)
    assert len(doc.tables) == 2  # 数据说明 + 变化点明细


def test_docx_rejects_slide_block(ws):
    spec = ArtifactSpec.model_validate({
        "artifact_id": "bad-doc", "kind": "docx", "title": "x",
        "blocks": [{"type": "slide", "layout": "bullets", "title": "PPT block"}],
    })
    with pytest.raises(Exception, match="不支持"):
        ws.render_artifact(spec)
