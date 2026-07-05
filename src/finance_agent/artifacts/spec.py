"""ArtifactSpec：产物的结构化中间表示（docs/architecture.md §6）。

report-builder（LLM 判断）产出 spec，渲染器（确定性代码）把 spec 变成文件。
产物有几章、哪些图表、几个 sheet 完全由 spec 的 block 树决定——skill 只提供
方法论与渲染骨架，不预设内容结构。

关键约束：
- spec 里没有文件路径：data_ref 是 dataset_id（工作区注册表解析）；
- 每个 block 可挂 evidence_refs，渲染时生成回链锚点；
- 未知 block 类型在 pydantic 校验层即被拒绝（discriminated union）。
"""

from __future__ import annotations

from typing import Annotated, Literal

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
    # list 而非 tuple[str, str]：定长元组的 prefixItems schema 会被
    # OpenAI 结构化输出拒绝（ArtifactSpec 是 render_artifact 工具的参数 schema）
    window: list[str] = Field(description="触发窗口 [起始日, 结束日]")
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
    """K线主图：OHLCV + MA + 事件标注 + 变化点标记。data_ref 为 dataset_id。

    事件/变化点支持**按引用挂材料**（events_material / changepoints_material 填
    工作区材料 id）：渲染前由 Workspace 从材料注入全量，LLM 不必逐条抄写——
    真实事故：24 事件 + 40 变化点内联进 spec ≈33KB，超 12K token 输出上限
    被截断，"Invalid JSON input" 确定性死循环。内联 events/changepoints 仍可用，
    与材料合并时同键（日期+标题 / 日期+类型）以内联为准（定点覆盖）。
    """

    type: Literal["kline_chart"] = "kline_chart"
    data_ref: str
    ticker: str
    events_material: str = ""         # mat-events-N：从材料注入全量事件
    changepoints_material: str = ""   # mat-market-N：注入变化点（取匹配 data_ref 的 dataset）
    events: list[EventAnnotation] = Field(default_factory=list)
    changepoints: list[ChangepointMarker] = Field(default_factory=list)


class ChangepointTableBlock(BaseModel):
    type: Literal["changepoint_table"] = "changepoint_table"
    caption: str = "行情变化点明细（规则触发）"
    changepoints: list[ChangepointMarker]


class DataSheetBlock(BaseModel):
    """xlsx：一个原始数据 sheet（OHLCV 原样落表，供公式引用与人工复核）。"""

    type: Literal["data_sheet"] = "data_sheet"
    sheet_name: str
    data_ref: str
    ticker: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class MetricsSheetBlock(BaseModel):
    """xlsx：公式驱动的指标区（收益/回撤/滚动波动率/相关性）。

    对齐（多资产按日期内连接）由渲染器代码完成（确定性）；指标全部用
    Excel 公式表达并引用"参数"sheet 的单元格——修改窗口参数即联动重算。
    """

    type: Literal["metrics_sheet"] = "metrics_sheet"
    data_refs: list[str] = Field(min_length=1, max_length=2)
    labels: list[str] = Field(default_factory=list, description="资产显示名，与 data_refs 对应")
    rolling_window: int = Field(default=30, ge=5, le=250)
    annualization: int = Field(default=252, ge=1)
    evidence_refs: list[str] = Field(default_factory=list)


class SlideBlock(BaseModel):
    """pptx：一页幻灯片。页面构成完全由 spec 决定，渲染器不预设页数。"""

    type: Literal["slide"] = "slide"
    layout: Literal["title", "section", "bullets", "table"] = "bullets"
    title: str
    subtitle: str = ""
    bullets: list[str] = Field(default_factory=list)
    table_headers: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)
    notes: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


Block = Annotated[
    HeadingBlock | NarrativeBlock | TableBlock | KlineChartBlock | ChangepointTableBlock | DataSheetBlock | MetricsSheetBlock | SlideBlock,
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
