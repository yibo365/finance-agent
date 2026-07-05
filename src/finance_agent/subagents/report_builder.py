"""report-builder：产出与修改 ArtifactSpec，触发确定性渲染。

唯一有"写产物"能力的 agent；写盘只能经 render/update_artifact 间接发生，
spec 不过校验就没有产物。
"""

from __future__ import annotations

from datetime import date

from agents import Agent, ModelSettings

from finance_agent.config import Settings
from finance_agent.llm import get_model
from finance_agent.context import AppContext
from finance_agent.contracts import ArtifactRefs
from finance_agent.tools.agent_tools import (
    list_artifacts,
    list_skills,
    load_skill,
    read_artifact,
    render_artifact,
    update_artifact,
)

_INSTRUCTIONS = """\
今天是 {today}。你是投研流水线的报告构建环节。输入是一个 TaskBrief JSON，context_data 中包含
成文所需的全部材料（dataset_id、变化点、事件、对齐结论等）。

新建产物流程：
1. list_skills → load_skill 读入对应产物类型的方法论，按方法论组织结构；
2. 设计 ArtifactSpec：产物有几章、放哪些 block 由任务内容决定，不套固定模板；
3. render_artifact 渲染。校验失败会返回具体原因——修正 spec 后重试，不要放弃。

修改产物流程：
1. read_artifact 读回当前 spec；
2. 最小变更：只改用户要求涉及的 block，其余原样保留（这是硬性要求，
   工作区会保留新旧版本供 diff 审计）；
3. update_artifact 提交，change_summary 一句话说清改了什么。

spec 纪律：
- artifact_id 用小写连字符（如 nvda-kline-report），一经创建不可变更；
- data_ref 必须用材料中给出的 dataset_id，没有文件路径这种东西；
- kline_chart 类 HTML 产物恰好一个 kline_chart block；事件标注（events）与
  变化点（changepoints）都挂在该 block 上；
- 溯源：narrative 中含数字结论的段落、每个事件、每个表格都挂 evidence_refs；
  材料里没有 evidence 支撑的话就不要写进产物；
- 叙事用"时间吻合 + 影响逻辑"措辞，不做因果断言；材料缺口如实写"局限性"段落。

输出 ArtifactRefs：echo 复述用了哪个 skill、结构如何组织、（修改时）动了哪些 block。
"""


def build_report_builder(settings: Settings) -> Agent[AppContext]:
    return Agent[AppContext](
        name="report-builder",
        instructions=_INSTRUCTIONS.format(today=date.today().isoformat()),
        tools=[list_skills, load_skill, list_artifacts, read_artifact, render_artifact, update_artifact],
        output_type=ArtifactRefs,
        model=get_model(settings),
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens),
    )
