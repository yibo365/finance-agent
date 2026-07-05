"""orchestrator：主 agent——意图路由、任务拆解、subagent 调度、终检与回复。

编排模式为 agents-as-tools（tech-design §4），但不用裸 as_tool：
每个 subagent 经自定义 function_tool 包装（内部嵌套 Runner.run），
参数 schema 即 TaskBrief——强制携带用户原话，治理传话失真。
"""

from __future__ import annotations

from datetime import date

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import (
    AlignmentMatrix,
    ArtifactRefs,
    EventList,
    MarketData,
    TaskBrief,
)
from finance_agent.subagents.alignment_analyst import build_alignment_analyst
from finance_agent.subagents.data_collector import build_data_collector
from finance_agent.subagents.event_researcher import build_event_researcher
from finance_agent.subagents.report_builder import build_report_builder
from finance_agent.tools.agent_tools import list_artifacts, list_skills, read_artifact

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
- subagent 需要的材料（变化点、事件、dataset_id、当前 spec 等）打包成 JSON
  放进 context_data——subagent 没有对话记忆，你不传它就不知道。

## 数据流细则（新建研究）

1. run_data_collector：返回 dataset_id 与变化点列表；
2. run_event_researcher：把 severity≥2 的变化点日期**聚合成不超过 10 个时间段**
   放进 focus_windows（相邻的点合并成区间，覆盖全部年份，不要只传前几个）；
   keywords 给出任务点名的具名事件（如 ChatGPT、B100、DeepSeek）+ 主题词
   （AI、芯片、出口管制）——检索是带着问题去的；
3. run_alignment_analyst：把变化点列表 + 事件列表原样打包进 context_data；
4. run_report_builder：把 dataset_id、变化点、事件、对齐矩阵全部打包进
   context_data，并说明期望的产物类型与 artifact_id 建议。

## 终检清单（report-builder 返回后逐条核对）

- 产物文件已生成（ArtifactRefs 里有路径）；
- subagent 的 echo 与用户原话对得上（标的、区间、产物类型）；
- 事件与变化点时间窗吻合性有对齐结论支撑；无对应事件的变化点被如实标注；
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
    return _PROMPT.format(
        today=date.today().isoformat(),
        session_id=app.workspace.session_id,
        skills="\n".join(index_lines(scan_skills())),
        artifacts="\n".join(artifact_lines),
        datasets="\n".join(dataset_lines),
    )


def build_orchestrator(settings: Settings) -> Agent[AppContext]:
    collector = build_data_collector(settings)
    researcher = build_event_researcher(settings)
    analyst = build_alignment_analyst(settings)
    builder = build_report_builder(settings)

    @function_tool
    async def run_data_collector(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用数据采集 subagent：拉取行情、缓存 dataset、执行变化点检测。

        Args:
            brief: TaskBrief。original_request 必须逐字引用用户原话。
        """
        result = await Runner.run(
            collector, brief.model_dump_json(), context=ctx.context, max_turns=12
        )
        return result.final_output_as(MarketData).model_dump_json()

    @function_tool
    async def run_event_researcher(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用事件研究 subagent：围绕 focus_windows 定向检索、去重、影响评级。

        Args:
            brief: TaskBrief。focus_windows 放入需要解释的变化点日期窗口。
        """
        result = await Runner.run(
            researcher, brief.model_dump_json(), context=ctx.context, max_turns=20
        )
        return result.final_output_as(EventList).model_dump_json()

    @function_tool
    async def run_alignment_analyst(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用对齐分析 subagent：变化点 × 事件吻合性论证（无工具，纯推理）。

        Args:
            brief: TaskBrief。context_data 必须包含变化点列表与事件列表 JSON。
        """
        result = await Runner.run(
            analyst, brief.model_dump_json(), context=ctx.context, max_turns=4
        )
        return result.final_output_as(AlignmentMatrix).model_dump_json()

    @function_tool
    async def run_report_builder(ctx: RunContextWrapper[AppContext], brief: TaskBrief) -> str:
        """调用报告构建 subagent：组织/修改 ArtifactSpec 并渲染产物。

        Args:
            brief: TaskBrief。context_data 必须包含成文所需全部材料
                （dataset_id、变化点、事件、对齐结论；修改场景给出修改指令与目标 artifact_id）。
        """
        result = await Runner.run(
            builder, brief.model_dump_json(), context=ctx.context, max_turns=16
        )
        return result.final_output_as(ArtifactRefs).model_dump_json()

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
        model=settings.model,
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens),
    )
