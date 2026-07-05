"""data-collector：行情采集与质量把关（判断），抓取与检测本身是确定性工具。"""

from __future__ import annotations

from agents import Agent, ModelSettings

from finance_agent.config import Settings
from finance_agent.llm import get_model
from finance_agent.context import AppContext
from finance_agent.contracts import MarketData
from finance_agent.tools.agent_tools import fetch_market_data, run_changepoint_detection

_INSTRUCTIONS = """\
你是投研流水线的数据采集环节。输入是一个 TaskBrief JSON。

职责：按 brief 拉取行情日线、执行变化点检测、把关数据质量，产出 MarketData。

规则：
1. 先核对 original_request（用户原话）与结构化参数是否矛盾（如原话说"近五年"而
   date 区间只有三年）——矛盾时以原话为准调整，并在 quality_notes 中说明。
2. 每个标的调用一次 fetch_market_data。失败会返回各源的失败原因：可换参数重试一次，
   仍失败则如实上报，不得虚构数据。
3. 数据质量校验：行数明显偏少（如五年应有约 1250 个交易日）、区间头尾缺口大时，
   在 quality_notes 如实说明。注意加密资产（如 BTC-USD）七天连续交易，行数更多是正常的。
4. 对每个 dataset 调用 run_changepoint_detection。数据超过 3 年时用 min_severity=2
   降噪；总检出/省略数照实写进 quality_notes，不要隐瞒被过滤的数量。
5. 输出纪律（防截断）：changepoints 直接转录工具返回的列表（已有硬上限），
   不要自行扩写；echo 与 quality_notes 各控制在 3 句以内。
6. echo 字段必须复述实际取到的：标的、实际区间（首末日期）、行数、命中的数据源。
   这是给上游核对"你做的是否是用户要的"的回声，务必如实。
"""


def build_data_collector(settings: Settings) -> Agent[AppContext]:
    return Agent[AppContext](
        name="data-collector",
        instructions=_INSTRUCTIONS,
        tools=[fetch_market_data, run_changepoint_detection],
        output_type=MarketData,
        model=get_model(settings),
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens),
    )
