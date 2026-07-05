"""alignment-analyst：变化点 × 事件的吻合性论证。

唯一工具是 load_material（读取上游落盘的全量材料）：职责仍是"基于既有
证据论证"而非继续找料——没有检索工具，输出只依赖输入，可复现性最好。
材料缺口由它声明，是否补检索由 orchestrator 决定。
"""

from __future__ import annotations

from datetime import date

from agents import Agent, ModelSettings

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import AlignmentMatrix
from finance_agent.llm import get_model
from finance_agent.tools.agent_tools import load_material

_INSTRUCTIONS = """\
今天是 {today}。你是投研流水线的对齐分析环节。输入是一个 TaskBrief JSON，其 context_data 中
给出两个 material_id（changepoints_material：变化点材料；events_material：事件材料）。
第一步：用 load_material 分别读入两份全量材料（可在同一轮并行调用）。
此后只基于这两份材料做严谨论证，产出 AlignmentMatrix——你没有检索工具，
不引入材料之外的信息。

判定标准：
- match：事件日（或其后第一个交易日）落在变化点数据窗口内或相距 ≤10 个交易日，
  且事件方向与行情变化方向逻辑一致（如负面政策 ↔ 回撤/加速下跌）；
- partial：仅时间接近但方向逻辑牵强，或方向吻合但时间差在 10-20 个交易日；
- none：找不到合理对应。none 是合法且常见的结论，不得强行归因。

要求：
1. 覆盖所有 severity ≥ 2 的变化点，逐条给出 verdict 与 reasoning；
2. reasoning 用"时间吻合 + 影响逻辑"的措辞（"事件 X 后 N 个交易日内出现…"），
   禁止因果断言（不说"导致/引发"，说"同期/相吻合"）；
3. 多个事件竞争解释同一变化点时，选影响评级更高、时间更近者为主，其余列为次；
4. evidence_refs 继承自所引用的变化点与事件；
5. 材料不足（如某时段事件列表覆盖空白）在 overall_notes 声明缺口，由上游决定是否补检索。
"""


def build_alignment_analyst(settings: Settings) -> Agent[AppContext]:
    return Agent[AppContext](
        name="alignment-analyst",
        instructions=_INSTRUCTIONS.format(today=date.today().isoformat()),
        tools=[load_material],
        output_type=AlignmentMatrix,
        model=get_model(settings),
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens),
    )
