"""event-researcher：围绕变化点时间窗的定向资讯检索、去重、影响评级。

上下文最脏的环节（吞几十条检索结果），因此独立成 agent 与主线程隔离。
"""

from __future__ import annotations

from agents import Agent, ModelSettings, WebSearchTool

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import EventList
from finance_agent.tools.agent_tools import (
    search_hn_news,
    search_yahoo_finance_news,
    web_search,
)

_INSTRUCTIONS = """\
你是投研流水线的事件研究环节。输入是一个 TaskBrief JSON，其中 focus_windows
是行情变化点的日期/区间列表——你的检索是"带着问题去的"：先有变化点，再找解释。

流程：
1. 核对 original_request 与参数；矛盾以原话为准，并写入 coverage_notes。
2. 对每个 focus_window（前后各放宽约 7 天）用 keywords 做 search_hn_news 定向检索；
   用 search_yahoo_finance_news 补充财经视角；必要时用 web_search 交叉验证与补漏。
3. 去重合并：同一事件多来源报道合并为一条，保留最权威来源在前。
4. 影响评级口径（impact）：
   5=直接改变标的盈利/估值中枢或触发大幅重定价；4=直接影响需求、产品周期、
   政策约束或市场情绪；3=影响生态与中期预期；1-2=观察级。
   direction 指事件对标的的方向性含义（up/down/mixed/neutral），不是当日涨跌。
5. 溯源纪律（硬性要求）：
   - 每个事件 sources 至少一条可点击 URL；日期、标题必须来自检索结果，禁止凭记忆编造；
   - evidence_refs 填入检索工具返回的 evidence_id；若联网搜索工具的返回不含
     evidence_id（托管搜索模式），其发现必须至少再经一路带 evidence 的检索确认，
     或在 notes 里注明"仅联网搜索来源"。
6. 诚实原则：某窗口检索无果就是无果，写进 coverage_notes；宁可返回"无对应事件"
   也不得强行凑一条。coverage_notes 同时复述你实际检索了哪些窗口与关键词（回声）。
"""


def build_event_researcher(settings: Settings) -> Agent[AppContext]:
    tools: list = [search_hn_news, search_yahoo_finance_news]
    if not settings.mock_mode:
        if settings.provider == "openrouter":
            # OpenRouter 无 Responses API 托管搜索 → 用其 web 插件（带 citations 与 evidence）
            tools.append(web_search)
        else:
            tools.append(WebSearchTool(search_context_size="medium"))
    return Agent[AppContext](
        name="event-researcher",
        instructions=_INSTRUCTIONS,
        tools=tools,
        output_type=EventList,
        model=settings.model,
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens),
    )
