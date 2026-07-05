"""主/子 agent 间的结构化契约（architecture-and-flow §6）。

环节间不传自由文本：
- 下行：orchestrator → subagent 一律 TaskBrief（强制携带用户原话，治理传话失真）；
- 上行：subagent → orchestrator 一律本文件中的 output_type，且带"实际做了什么"
  的回声字段（echo），供 orchestrator 终检对照与向用户复述。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from finance_agent.artifacts.spec import Direction


class TaskBrief(BaseModel):
    """orchestrator 调用任何 subagent 的统一入参。"""

    original_request: str = Field(description="用户原话逐字引用（不得改写），供核对提取是否失真")
    objective: str = Field(description="本次调用要完成的具体目标")
    tickers: list[str] = Field(default_factory=list, description="涉及标的，如 NVDA / GC=F / BTC-USD")
    date_start: str = Field(default="", description="研究区间起点 YYYY-MM-DD")
    date_end: str = Field(default="", description="研究区间终点 YYYY-MM-DD")
    keywords: list[str] = Field(default_factory=list, description="检索关键词（事件研究用）")
    focus_windows: list[str] = Field(
        default_factory=list,
        description="定向检索/分析的日期或区间，如 2025-01-27 或 2025-01-20/2025-02-05",
    )
    assumptions: str = Field(default="", description="orchestrator 做出的默认假设声明（如时间范围推断）")
    context_data: str = Field(default="", description="附加材料 JSON（变化点列表、事件列表、当前 spec 等）")


class ChangepointOut(BaseModel):
    date: str
    kind: str
    rule: str
    severity: int
    # 注意：不能用 tuple[str, str]——pydantic 会生成 prefixItems schema，
    # OpenAI 结构化输出不支持（"array schema missing items"）
    window: list[str] = Field(description="触发窗口 [起始日, 结束日]")
    evidence_refs: list[str] = Field(default_factory=list)


class MarketDatasetSummary(BaseModel):
    dataset_id: str
    ticker: str
    rows: int
    start: str
    end: str
    source: str = Field(description="实际命中的行情源（回声）")
    evidence_id: str
    changepoints: list[ChangepointOut] = Field(default_factory=list)
    quality_notes: str = Field(default="", description="数据质量说明：缺口、重试、与请求区间的差异（回声）")


class MarketData(BaseModel):
    """data-collector 的 output_type（支持多标的）。"""

    datasets: list[MarketDatasetSummary]
    echo: str = Field(description="实际执行摘要：取了什么标的、什么区间、命中哪个源")


class SourceLink(BaseModel):
    name: str
    url: str


class EventItem(BaseModel):
    date: str
    title: str
    category: str = "事件"
    direction: Direction = "neutral"
    move: str = Field(default="", description="与该事件吻合的行情变化描述")
    impact: int = Field(ge=1, le=5)
    notes: str = ""
    sources: list[SourceLink] = Field(default_factory=list, description="至少一个可点击原文链接")
    evidence_refs: list[str] = Field(default_factory=list)


class EventList(BaseModel):
    """event-researcher 的 output_type。"""

    events: list[EventItem]
    coverage_notes: str = Field(
        description="回声：检索了哪些窗口/关键词，哪些窗口无果（如实说明，不得虚构事件）"
    )


class AlignmentEntry(BaseModel):
    changepoint_date: str
    changepoint_kind: str
    verdict: Literal["match", "partial", "none"]
    matched_event_titles: list[str] = Field(default_factory=list)
    reasoning: str = Field(description="时间吻合 + 影响逻辑的论证；none 时说明为何无对应事件")
    evidence_refs: list[str] = Field(default_factory=list)


class AlignmentMatrix(BaseModel):
    """alignment-analyst 的 output_type。"""

    entries: list[AlignmentEntry]
    overall_notes: str = Field(default="", description="整体判断与材料缺口声明（缺口由 orchestrator 决定是否补检索）")


class ArtifactRef(BaseModel):
    artifact_id: str
    version: int
    kind: str
    file: str
    change_summary: str


class ArtifactRefs(BaseModel):
    """report-builder 的 output_type（场景 B 一次产出多件）。"""

    artifacts: list[ArtifactRef]
    echo: str = Field(default="", description="实际产出摘要：用了哪个 skill、结构如何组织、修改了哪些 block")
