"""pptx 渲染器：ArtifactSpec（slide blocks）→ 决策框架演示文稿。

页数与内容完全由 spec 决定；渲染器负责版式、页脚溯源标记与末页溯源清单。
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Mapping

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from finance_agent.artifacts.spec import ArtifactSpec, SlideBlock
from finance_agent.provenance import Evidence
from finance_agent.skills.loader import SkillInfo

SUPPORTED_BLOCKS = {"slide"}

_MUTED = RGBColor(0x6B, 0x77, 0x8C)


class RenderError(RuntimeError):
    pass


def _footer(slide, text: str) -> None:
    box = slide.shapes.add_textbox(Inches(0.4), Inches(7.05), Inches(9.2), Inches(0.35))
    para = box.text_frame.paragraphs[0]
    para.text = text
    para.font.size = Pt(9)
    para.font.color.rgb = _MUTED


def _add_title_slide(prs: Presentation, block: SlideBlock) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = block.title
    if len(slide.placeholders) > 1:
        slide.placeholders[1].text = block.subtitle


def _add_section_slide(prs: Presentation, block: SlideBlock) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    slide.shapes.title.text = block.title
    if block.subtitle:
        _footer(slide, block.subtitle)


def _add_bullets_slide(prs: Presentation, block: SlideBlock) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    slide.shapes.title.text = block.title
    body = slide.placeholders[1].text_frame
    body.clear()
    for i, bullet in enumerate(block.bullets or [""]):
        para = body.paragraphs[0] if i == 0 else body.add_paragraph()
        para.text = bullet
        para.level = 0
    _slide_footer(slide, block)


def _add_table_slide(prs: Presentation, block: SlideBlock) -> None:
    if not block.table_headers:
        raise RenderError(f"table 版式的 slide（{block.title}）缺少 table_headers")
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = block.title
    rows, cols = len(block.table_rows) + 1, len(block.table_headers)
    shape = slide.shapes.add_table(
        rows, cols, Inches(0.4), Inches(1.6), Inches(9.2), Inches(0.4 + 0.35 * rows)
    )
    table = shape.table
    for c, header in enumerate(block.table_headers):
        table.cell(0, c).text = header
    for r, row in enumerate(block.table_rows, start=1):
        for c in range(cols):
            table.cell(r, c).text = row[c] if c < len(row) else ""
    _slide_footer(slide, block)


def _slide_footer(slide, block: SlideBlock) -> None:
    parts = []
    if block.evidence_refs:
        parts.append(f"溯源：{'、'.join(block.evidence_refs)}")
    if block.notes:
        parts.append(block.notes)
    if parts:
        _footer(slide, "  ｜  ".join(parts))


def _evidence_slide(prs: Presentation, evidence: Mapping[str, Evidence]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "数据来源与溯源清单"
    body = slide.placeholders[1].text_frame
    body.clear()
    if not evidence:
        body.paragraphs[0].text = "（本演示未登记 evidence）"
        return
    for i, ev in enumerate(evidence.values()):
        para = body.paragraphs[0] if i == 0 else body.add_paragraph()
        para.text = f"{ev.id}［{ev.kind}］{ev.source_url}（{ev.fetched_at}）"
        para.font.size = Pt(11)


_LAYOUT_DISPATCH = {
    "title": _add_title_slide,
    "section": _add_section_slide,
    "bullets": _add_bullets_slide,
    "table": _add_table_slide,
}


def render_pptx(
    spec: ArtifactSpec,
    *,
    datasets: Mapping = {},
    evidence: Mapping[str, Evidence],
    skill: SkillInfo,
) -> bytes:
    if spec.kind != "pptx":
        raise RenderError(f"kind 不匹配：期望 pptx，得到 {spec.kind}")
    unsupported = {b.type for b in spec.blocks} - SUPPORTED_BLOCKS
    if unsupported:
        raise RenderError(f"pptx 渲染器不支持 block 类型：{sorted(unsupported)}")

    prs = Presentation()
    slides = [b for b in spec.blocks if isinstance(b, SlideBlock)]
    if slides[0].layout != "title":
        # 自动补齐封面，保证任何 spec 都有标题页
        _add_title_slide(prs, SlideBlock(
            layout="title", title=spec.title,
            subtitle=spec.subtitle or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        ))
    for block in slides:
        _LAYOUT_DISPATCH[block.layout](prs, block)
    _evidence_slide(prs, evidence)

    prs.core_properties.title = spec.title
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
