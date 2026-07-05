"""orchestrator：主 agent——意图路由、任务拆解、subagent 调度、终检与回复。

编排模式为 agents-as-tools（tech-design §4），但不用裸 as_tool：
每个 subagent 经自定义 function_tool 包装（内部嵌套 Runner.run），
参数 schema 即 TaskBrief——强制携带用户原话，治理传话失真。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, date, datetime

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from pydantic import BaseModel

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import (
    AlignmentMatrix,
    ArtifactRefs,
    EventList,
    MarketData,
    TaskBrief,
)
from finance_agent.events import RunItemTranslator
from finance_agent.json_repair import salvage_output
from finance_agent.llm import get_model
from finance_agent.subagents.alignment_analyst import build_alignment_analyst
from finance_agent.subagents.data_collector import build_data_collector
from finance_agent.subagents.event_researcher import build_event_researcher
from finance_agent.subagents.report_builder import build_report_builder
from finance_agent.tools.agent_tools import (
    list_artifacts,
    list_skills,
    read_artifact,
    truncated_tool_error,
)

_PROMPT = """\
你是一个投研工作台的主 agent（orchestrator）。今天是 {today}。
用户会用自然语言提出研究任务，你负责：解析意图 → 调度专职 subagent → 汇总把关 →
回复。你自己不做检索、不写产物——判断与调度是你的全部工作。

## 意图路由（先分类，再行动；不需要的链路一步都不要调）

1. 新建研究（"回顾X行情…生成HTML/报告"）→ 完整流水线：
   run_data_collector → run_event_researcher → run_alignment_analyst → run_report_builder
2. 修改产物（"把X评级改成高"/"补一段分析"）→ read_artifact 定位当前 spec →
   判断是否缺新材料（仅缺料时补相应环节）→ run_report_builder 做定点变更
3. 咨询既有产物（"为什么X评级是高？"）→ list_artifacts / read_artifact
   直接基于 spec 与 evidence 回答，不调任何 subagent
4. 无关话题/超出能力（闲聊、写代码等）→ 直接回应或说明能力边界，不调任何工具

防误触发：数据采集与检索是昂贵动作。意图不明时先向用户澄清（标的？时间范围？
产物形态？），不凭猜测启动流水线；采用默认假设（如"近五年"=今天往前推5年）
必须在回复中声明。

工具/subagent 失败处置：读错误原因，调整参数自行重试一次（如缩小时间范围、
提高 min_severity、减少返回条数）；重试仍失败才向用户报告具体原因。
**不要把你自己能决定的事抛给用户选**（如"用缓存还是重抓"——有可用缓存就用，
在回复中声明即可）。若输入标注了"一次性执行模式"，用户无法回答任何追问：
基于合理默认假设直接完成全流程，所有假设在最终回复中声明。

## TaskBrief 契约（调用任何 subagent 时的硬性要求）

- original_request 必须逐字引用用户原话，不得改写；
- 你的推断（时间范围、标的解析）写进结构化字段，推断依据写进 assumptions；
- **相对时间必须以上方的"今天"为基准计算**（"近五年"→ start=今天-5年、end=今天），
  先写出具体日期再调用，禁止凭直觉写年份（你的直觉可能停留在训练数据的年代）；
- subagent 没有对话记忆，它需要的输入你必须经 brief 传达；
- **材料按引用传递**：每个 subagent 完成后返回 material_id + 摘要（全量内容
  已落盘）。给下游 subagent 传材料时，context_data 里只放 material_id 与
  dataset_id 等标识，**禁止把变化点/事件/对齐的全量 JSON 复制进 context_data**
  ——下游会用 load_material 自行读取全量（真实事故：51KB 的 brief 驻留
  对话历史，最终单次请求超 8MB 上限）。

## 数据流细则（新建研究）

1. run_data_collector：返回 dataset_id、变化点摘要与 material_id；
2. run_event_researcher：把 severity≥2 的变化点日期**聚合成不超过 10 个时间段**
   放进 focus_windows（相邻的点合并成区间，覆盖全部年份，不要只传前几个）；
   keywords 给出任务点名的具名事件（如 ChatGPT、B100、DeepSeek）+ 主题词
   （AI、芯片、出口管制）——检索是带着问题去的；返回事件摘要与 material_id；
3. run_alignment_analyst：context_data 只放
   {{"changepoints_material": "<市场材料id>", "events_material": "<事件材料id>"}}；
4. run_report_builder：context_data 只放 dataset_id、三个 material_id
   （市场/事件/对齐）、期望产物类型与 artifact_id 建议。事件的 sources URL 与
   evidence_refs 在事件材料里，subagent 会自行读取并逐字复制。

## 终检清单（report-builder 返回后逐条核对）

- 产物文件已生成（ArtifactRefs 里有路径）；
- subagent 的 echo 与用户原话对得上（标的、区间、产物类型）；
- 事件与变化点时间窗吻合性有对齐结论支撑；无对应事件的变化点被如实标注；
- **修改产物场景：用 list_artifacts 核对 current_version 已递增**——
  版本没 +1 就是修改从未落盘，无论 subagent 怎么声称成功，都必须如实
  报告失败与原因，禁止向用户宣称已修改；
- 有不符 → 让 report-builder 修正，而不是自己糊弄过去。

## 回复规范

- 先给结论/结果，再给细节；
- 复述实际执行情况（回声）："已取 NVDA 2021-07-04 至 2026-07-02 日线（Yahoo）"；
- 产物用 [artifact_id vN] 指代并给出文件路径；
- 诚实原则：数据缺口、检索无果、评级依据不足要明说；不做投资建议。

## 当前工作区

会话：{session_id}
可用 skill：
{skills}
已有产物：
{artifacts}
已缓存数据：
{datasets}
已落盘材料（给下游 subagent 传其 material_id，用 load_material 可读全量）：
{materials}
"""


def _instructions(ctx: RunContextWrapper[AppContext], _agent: Agent[AppContext]) -> str:
    from finance_agent.skills.loader import index_lines, scan_skills

    app = ctx.context
    artifacts = app.workspace.list_artifacts()
    artifact_lines = [
        f"- [{a['artifact_id']} v{a['current_version']}] {a['title']}（{a['kind']}）"
        f" 最近变更：{a['change_summary']}"
        for a in artifacts
    ] or ["（暂无）"]
    datasets = app.workspace.dataset_index()
    dataset_lines = [
        f"- {ds_id}：{meta.get('ticker', '')} {meta.get('start', '')}~{meta.get('end', '')}"
        f"（{meta.get('rows', '?')} 行，{meta.get('source', '')}）"
        for ds_id, meta in datasets.items()
    ] or ["（暂无）"]
    materials = app.workspace.material_index()
    return _PROMPT.format(
        today=date.today().isoformat(),
        session_id=app.workspace.session_id,
        skills="\n".join(index_lines(scan_skills())),
        artifacts="\n".join(artifact_lines),
        datasets="\n".join(dataset_lines),
        materials="、".join(materials) if materials else "（暂无）",
    )


def _digest(output: BaseModel) -> dict:
    """子代理全量输出 → 给 orchestrator 的确定性紧凑摘要。

    摘要只保留 orchestrator 决策需要的字段（聚合 focus_windows、终检对照、
    向用户复述）；全量内容经 material 落盘，下游 subagent 用 load_material 取。
    """
    if isinstance(output, MarketData):
        return {
            "datasets": [
                {
                    "dataset_id": d.dataset_id, "ticker": d.ticker, "rows": d.rows,
                    "start": d.start, "end": d.end, "source": d.source,
                    "quality_notes": d.quality_notes,
                    "changepoints": [
                        f"{p.date} {p.kind} sev{p.severity}" for p in d.changepoints
                    ],
                }
                for d in output.datasets
            ],
            "echo": output.echo,
        }
    if isinstance(output, EventList):
        return {
            "events": [
                f"{e.date} [{e.category}] {e.title}（impact {e.impact}，{e.direction}）"
                for e in output.events
            ],
            "coverage_notes": output.coverage_notes,
        }
    if isinstance(output, AlignmentMatrix):
        verdicts: dict[str, int] = {}
        for entry in output.entries:
            verdicts[entry.verdict] = verdicts.get(entry.verdict, 0) + 1
        return {
            "verdicts": verdicts,
            "entries": [
                f"{e.changepoint_date} {e.changepoint_kind} → {e.verdict}"
                + (f"（{'、'.join(e.matched_event_titles)}）" if e.matched_event_titles else "")
                for e in output.entries
            ],
            "overall_notes": output.overall_notes,
        }
    return output.model_dump()


def _finalize_events(app: AppContext, output: BaseModel | None) -> BaseModel | None:
    """researcher 收尾：合并增量提交的事件与最终输出（后者可能已修复或为 None）。

    即使最终输出完全损坏、甚至 Max turns 打满，已经 submit_events 落盘的
    事件也能合并成 EventList 正常返回——研究成果不再因收尾失败整体作废。
    """
    collected: list = list(app.collected_events)
    final_events = list(output.events) if isinstance(output, EventList) else []
    seen = {(e.date, e.title.strip()) for e in collected}
    merged = collected + [
        e for e in final_events if (e.date, e.title.strip()) not in seen
    ]
    if not merged:
        return output  # 没有任何事件可救：维持原结果（可能为 None → 照常报错）
    if isinstance(output, EventList):
        notes = output.coverage_notes
    else:
        notes = "（最终输出解析失败，事件来自运行中的增量提交，覆盖说明缺失——如需窗口级核对请复核检索日志）"
    return EventList(events=merged, coverage_notes=notes)


async def _run_subagent(
    agent: Agent[AppContext],
    brief: TaskBrief,
    ctx: RunContextWrapper[AppContext],
    output_type: type[BaseModel],
    max_turns: int,
    material_kind: str | None = None,
    finalize: Callable[[AppContext, BaseModel | None], BaseModel | None] | None = None,
) -> str:
    """嵌套运行 subagent 并把内部动作经 ctx.emit 上报（FR-18）。

    嵌套 run 的流事件不会出现在外层 orchestrator 的流里——不转发，
    前端/CLI 就只能看到"正在调用 run_xxx…"的黑盒（真实事故：界面停在
    run event 十分钟，用户只能翻文件 mtime 判断是否卡死）。

    material_kind 非空时，全量输出落盘为工作区材料，只把 material_id + 摘要
    返回给 orchestrator——大 JSON 不进主对话历史（真实事故：51KB brief
    全程驻留历史，每轮 LLM 调用陪跑）。
    """
    app = ctx.context
    app.begin_subagent_run()

    def _report(event: dict) -> None:
        # 同一份事件：推给前端/CLI（emit）+ 落审计日志（嵌套运行不进
        # session.db，出事时这是唯一的现场记录）
        try:
            app.workspace.append_run_log(
                {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **event}
            )
        except OSError:
            pass  # 审计日志是旁路，不阻断主流程
        app.emit(event)

    _report({"type": "agent_start", "agent": agent.name})
    translator = RunItemTranslator(agent.name)
    result = Runner.run_streamed(
        agent, brief.model_dump_json(), context=app, max_turns=max_turns
    )
    output: BaseModel | None = None
    failure: BaseException | None = None
    try:
        try:
            async for event in result.stream_events():
                if event.type == "run_item_stream_event":
                    translated = translator.translate(event.item)
                    if translated is not None:
                        _report(translated)
            output = result.final_output_as(output_type)
        except asyncio.CancelledError:
            raise  # 用户停止：不做任何打捞，保持取消语义
        except Exception as exc:  # noqa: BLE001 —— 修复层兜底，救不回再抛
            failure = exc
            output = salvage_output(str(exc), output_type)
            if output is not None:
                _report({
                    "type": "tool_result", "agent": agent.name, "tool": "最终输出修复",
                    "ok": True,
                    "detail": "原始输出含格式错误（围栏/坏引号/截断），已确定性修复解析。",
                })
    finally:
        _report({"type": "agent_end", "agent": agent.name})
    if finalize is not None:
        output = finalize(app, output)
    if output is None:
        assert failure is not None
        raise failure
    if material_kind is None:
        return output.model_dump_json()
    material_id = app.workspace.store_material(material_kind, output.model_dump())
    envelope = {
        "material_id": material_id,
        **_digest(output),
        "note": "全量材料已落盘；给下游 subagent 传这个 material_id（context_data），不要复制全量内容。",
    }
    return json.dumps(envelope, ensure_ascii=False)


def build_orchestrator(settings: Settings) -> Agent[AppContext]:
    collector = build_data_collector(settings)
    researcher = build_event_researcher(settings)
    analyst = build_alignment_analyst(settings)
    builder = build_report_builder(settings)

    @function_tool(failure_error_function=truncated_tool_error)
    async def run_data_collector(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用数据采集 subagent：拉取行情、缓存 dataset、执行变化点检测。

        Args:
            brief: TaskBrief。original_request 必须逐字引用用户原话。
        """
        return await _run_subagent(
            collector, brief, ctx, MarketData, max_turns=12, material_kind="market"
        )

    @function_tool(failure_error_function=truncated_tool_error)
    async def run_event_researcher(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用事件研究 subagent：围绕 focus_windows 定向检索、去重、影响评级。

        Args:
            brief: TaskBrief。focus_windows 放入需要解释的变化点日期窗口。
        """
        return await _run_subagent(
            researcher, brief, ctx, EventList, max_turns=20, material_kind="events",
            finalize=_finalize_events,
        )

    @function_tool(failure_error_function=truncated_tool_error)
    async def run_alignment_analyst(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用对齐分析 subagent：变化点 × 事件吻合性论证（无工具，纯推理）。

        Args:
            brief: TaskBrief。context_data 必须包含变化点列表与事件列表 JSON。
        """
        return await _run_subagent(
            analyst, brief, ctx, AlignmentMatrix, max_turns=8, material_kind="alignment"
        )

    @function_tool(failure_error_function=truncated_tool_error)
    async def run_report_builder(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用报告构建 subagent：组织/修改 ArtifactSpec 并渲染产物。

        Args:
            brief: TaskBrief。context_data 必须包含成文所需全部材料
                （dataset_id、变化点、事件、对齐结论；修改场景给出修改指令与目标 artifact_id）。
        """
        # 24 轮：spec 是最大的工具参数 schema，弱工具调用模型（如 deepseek）常需
        # 多次格式重试 + 校验修正循环；16 轮曾被重试烧光（Max turns exceeded）
        return await _run_subagent(builder, brief, ctx, ArtifactRefs, max_turns=24)

    return Agent[AppContext](
        name="orchestrator",
        instructions=_instructions,
        tools=[
            run_data_collector,
            run_event_researcher,
            run_alignment_analyst,
            run_report_builder,
            list_skills,
            list_artifacts,
            read_artifact,
        ],
        model=get_model(settings),
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens or None),
    )
