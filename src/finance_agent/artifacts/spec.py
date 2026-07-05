"""ArtifactSpec：产物的结构化中间表示（tech-design §6）。

report-builder（LLM 判断）产出 spec，渲染器（确定性代码）把 spec 变成文件。
产物有几章、哪些图表、几个 sheet 完全由 spec 的 block 树决定——skill 只提供
方法论与渲染骨架，不预设内容结构。

关键约束：
- spec 里没有文件路径：data_ref 是 dataset_id（工作区注册表解析）；
- 每个 block 可挂 evidence_refs，渲染时生成回链锚点；
- 未知 block 类型在 pydantic 校验层即被拒绝（discriminated union）。
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Direction = Literal["up", "down", "mixed", "neutral"]


class EventSource(BaseModel):
    name: str
    url: str


class EventAnnotation(BaseModel):
    """K 线上的事件标注（同时驱动图上标记与事件明细表）。"""

    date: str                      # 事件日 YYYY-MM-DD（非交易日由前端映射到下一交易日）
    title: str
    category: str = "事件"
    direction: Direction = "neutral"
    move: str = ""                 # 行情变化描述：拐点/加速/回撤/上涨等
    impact: int = Field(ge=1, le=5)  # 影响评级 1 低 — 5 高
    notes: str = ""
    sources: list[EventSource] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class ChangepointMarker(BaseModel):
    """确定性算法产出的变化点（tools/changepoints.py），进图表与明细表。"""

    date: str
    kind: Literal[
        "trend_up", "trend_down", "accel_up", "accel_down",
        "drawdown", "rally", "volume_spike",
    ]
    rule: str
    severity: int = Field(ge=1, le=3)
    window: tuple[str, str]
    evidence_refs: list[str] = Field(default_factory=list)


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    text: str
    level: int = Field(default=2, ge=2, le=4)


class NarrativeBlock(BaseModel):
    """叙事段落。text 按空行分段渲染；全文转义，不解析 HTML。"""

    type: Literal["narrative"] = "narrative"
    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    caption: str = ""
    headers: list[str]
    rows: list[list[str]]
    evidence_refs: list[str] = Field(default_factory=list)


class KlineChartBlock(BaseModel):
    """K线主图：OHLCV + MA + 事件标注 + 变化点标记。data_ref 为 dataset_id。"""

    type: Literal["kline_chart"] = "kline_chart"
    data_ref: str
    ticker: str
    events: list[EventAnnotation] = Field(default_factory=list)
    changepoints: list[ChangepointMarker] = Field(default_factory=list)


class ChangepointTableBlock(BaseModel):
    type: Literal["changepoint_table"] = "changepoint_table"
    caption: str = "行情变化点明细（规则触发）"
    changepoints: list[ChangepointMarker]


Block = Annotated[
    Union[HeadingBlock, NarrativeBlock, TableBlock, KlineChartBlock, ChangepointTableBlock],
    Field(discriminator="type"),
]

ArtifactKind = Literal["html", "xlsx", "pptx", "docx"]


class ArtifactSpec(BaseModel):
    artifact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    kind: ArtifactKind
    title: str
    subtitle: str = ""
    skill: str | None = None
    blocks: list[Block] = Field(min_length=1)
