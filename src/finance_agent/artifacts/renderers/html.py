"""HTML 渲染器：ArtifactSpec → 自包含交互报告（kline-html-report skill）。

确定性纯代码：spec 校验通过才渲染；所有外部文本转义；Plotly 与数据内嵌，
产物零外部请求。可用 spec fixture 单测，不依赖 LLM。
"""

from __future__ import annotations

import html as html_escape
import json
from datetime import datetime, timezone
from typing import Mapping

import pandas as pd

from finance_agent.artifacts.spec import (
    ArtifactSpec,
    Block,
    ChangepointMarker,
    ChangepointTableBlock,
    HeadingBlock,
    KlineChartBlock,
    NarrativeBlock,
    TableBlock,
)
from finance_agent.provenance import Evidence
from finance_agent.skills.loader import SkillInfo

SUPPORTED_BLOCKS = {"heading", "narrative", "table", "kline_chart", "changepoint_table"}

_CP_KIND_LABELS = {
    "trend_up": "趋势拐头向上", "trend_down": "趋势拐头向下",
    "accel_up": "加速上涨", "accel_down": "加速下跌",
    "drawdown": "回撤确认", "rally": "反弹确认", "volume_spike": "量能异常",
}


class RenderError(RuntimeError):
    pass


def _esc(value: object) -> str:
    return html_escape.escape(str(value), quote=True)


def _evidence_links(refs: list[str]) -> str:
    if not refs:
        return ""
    links = "、".join(f'<a href="#{_esc(r)}">{_esc(r)}</a>' for r in refs)
    return f'<span class="evref">［溯源：{links}］</span>'


def _render_heading(block: HeadingBlock) -> str:
    tag = f"h{block.level}"
    return f"<{tag}>{_esc(block.text)}</{tag}>"


def _render_narrative(block: NarrativeBlock) -> str:
    paragraphs = [p.strip() for p in block.text.split("\n\n") if p.strip()]
    body = "".join(f"<p>{_esc(p)}</p>" for p in paragraphs)
    return f'<div class="panel method">{body}{_evidence_links(block.evidence_refs)}</div>'


def _render_table(block: TableBlock) -> str:
    caption = f'<div class="section-title">{_esc(block.caption)}</div>' if block.caption else ""
    head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>"
        for row in block.rows
    )
    return (
        f'<div class="panel">{caption}<div class="table-wrap"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
        f"{_evidence_links(block.evidence_refs)}</div>"
    )


def _render_changepoint_row(cp: ChangepointMarker) -> str:
    label = _CP_KIND_LABELS.get(cp.kind, cp.kind)
    return (
        f"<tr><td>{_esc(cp.date)}</td><td>{_esc(label)}</td>"
        f"<td>{cp.severity}/3</td><td>{_esc(cp.rule)}</td>"
        f"<td>{_esc(cp.window[0])} → {_esc(cp.window[1])}</td>"
        f"<td>{_evidence_links(cp.evidence_refs) or '—'}</td></tr>"
    )


def _render_changepoint_table(block: ChangepointTableBlock) -> str:
    rows = "".join(_render_changepoint_row(cp) for cp in block.changepoints)
    return (
        f'<div class="panel"><div class="section-title">{_esc(block.caption)}</div>'
        '<div class="table-wrap"><table><thead><tr>'
        "<th>日期</th><th>类型</th><th>严重度</th><th>触发规则</th><th>数据窗口</th><th>溯源</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div></div>"
    )


def _render_section(block: Block) -> str:
    if isinstance(block, HeadingBlock):
        return _render_heading(block)
    if isinstance(block, NarrativeBlock):
        return _render_narrative(block)
    if isinstance(block, TableBlock):
        return _render_table(block)
    if isinstance(block, ChangepointTableBlock):
        return _render_changepoint_table(block)
    raise RenderError(f"HTML 渲染器不支持 block 类型：{block.type}")


def _render_evidence(evidence: Mapping[str, Evidence]) -> str:
    if not evidence:
        return '<div class="method">（本报告未登记 evidence）</div>'
    items = []
    for ev in evidence.values():
        url = _esc(ev.source_url)
        link = (
            f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'
            if ev.source_url.startswith(("http://", "https://"))
            else url
        )
        query = _esc(json.dumps(ev.query, ensure_ascii=False)) if ev.query else "—"
        items.append(
            f'<div class="evidence-item" id="{_esc(ev.id)}">'
            f'<span class="eid">{_esc(ev.id)}</span> <span class="badge">{_esc(ev.kind)}</span>'
            f'<div class="meta">来源：{link}<br>抓取时间：{_esc(ev.fetched_at)}｜查询：<code>{query}</code>'
            f"<br>摘录：{_esc(ev.excerpt) or '—'}</div></div>"
        )
    return "".join(items)


def _payload_json(payload: dict) -> str:
    """内嵌 JSON。'</' 转义防止提前闭合 <script>。"""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render_html(
    spec: ArtifactSpec,
    *,
    datasets: Mapping[str, pd.DataFrame],
    evidence: Mapping[str, Evidence],
    skill: SkillInfo,
) -> str:
    """渲染自包含 HTML 报告。要求 spec 恰含一个 kline_chart block。"""
    if spec.kind != "html":
        raise RenderError(f"kind 不匹配：期望 html，得到 {spec.kind}")
    charts = [b for b in spec.blocks if isinstance(b, KlineChartBlock)]
    if len(charts) != 1:
        raise RenderError(f"kline-html-report 要求恰好 1 个 kline_chart block，实际 {len(charts)}")
    chart = charts[0]
    if chart.data_ref not in datasets:
        raise RenderError(f"dataset 未登记：{chart.data_ref}")
    df = datasets[chart.data_ref]

    template_path = skill.templates_dir / "report_template.html"
    render_js_path = skill.assets_dir / "render.js"
    plotly_path = skill.assets_dir / "plotly.min.js"
    for path in (template_path, render_js_path, plotly_path):
        if not path.is_file():
            raise RenderError(f"skill 资产缺失：{path}")

    payload = {
        "meta": {
            "ticker": chart.ticker,
            "title": spec.title,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "rows": df.to_dict(orient="records"),
        "events": [e.model_dump() for e in chart.events],
        "changepoints": [cp.model_dump() for cp in chart.changepoints],
    }
    sections = "\n".join(
        _render_section(block) for block in spec.blocks if not isinstance(block, KlineChartBlock)
    )
    pills = [
        f"标的：{chart.ticker}",
        f"区间：{df['date'].iloc[0]} 至 {df['date'].iloc[-1]}",
        f"事件：{len(chart.events)}｜变化点：{len(chart.changepoints)}",
        "评级：1 低 — 5 高",
    ]
    pills_html = "".join(f'<span class="pill">{_esc(p)}</span>' for p in pills)
    source_note = f"数据：dataset {chart.data_ref}（溯源见 Evidence 面板）"

    html = template_path.read_text(encoding="utf-8")
    replacements = {
        "__TITLE__": _esc(spec.title),
        "__SUBTITLE__": _esc(spec.subtitle),
        "__PILLS_HTML__": pills_html,
        "__GENERATED_AT__": _esc(payload["meta"]["generated_at"]),
        "__SECTIONS_HTML__": sections,
        "__EVIDENCE_HTML__": _render_evidence(evidence),
        "__SOURCE_NOTE__": _esc(source_note),
        "__PAYLOAD_JSON__": _payload_json(payload),
        "__RENDER_JS__": render_js_path.read_text(encoding="utf-8"),
        "__PLOTLY_JS__": plotly_path.read_text(encoding="utf-8"),
    }
    for token, value in replacements.items():
        if token not in html:
            raise RenderError(f"模板缺少占位符：{token}")
        html = html.replace(token, value)
    return html
