"""event-researcher：围绕变化点时间窗的定向资讯检索、去重、影响评级。

上下文最脏的环节（吞几十条检索结果），因此独立成 agent 与主线程隔离。
"""

from __future__ import annotations

from datetime import date

from agents import Agent, ModelSettings

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.contracts import EventList
from finance_agent.llm import get_model
from finance_agent.tools.agent_tools import (
    search_hn_news,
    search_yahoo_finance_news,
    submit_events,
    web_search,
)

_INSTRUCTIONS = """\
今天是 {today}。你是投研流水线的事件研究环节。输入是一个 TaskBrief JSON，其中 focus_windows
是行情变化点的日期/区间列表——你的检索是"带着问题去的"：先有变化点，再找解释。

流程：
1. 核对 original_request 与参数；矛盾以原话为准，并写入 coverage_notes。
2. **具名事件锚点（必做）**：original_request/keywords 中点名的事件（如"ChatGPT发布"
   "B100""DeepSeek"）逐个定位——用 web_search 或 search_hn_news 查出确切日期与
   原始来源。任务点名的事件缺席是不可接受的产出。
3. 窗口定向检索：对 focus_windows（前后各放宽约 7 天，相邻窗口合并成大区间一次查）
   用 search_hn_news。**检索战术**：每次调用只用 1-2 个词的单个关键词
   （chatgpt / nvidia / deepseek / export control…），禁止 OR/引号组合——
   工具会拒绝；多关键词就多次调用。
4. search_yahoo_finance_news 只覆盖近期资讯，整个任务至多调用 1-2 次（近期窗口）；
   历史窗口靠 HN 与 web_search。
5. 无果处置：某窗口无果时换更短/更通用的关键词重试一次；仍无果才记录，
   必须覆盖全部 focus_windows 后才能收工。
6. **检索预算（硬约束）**：整次运行的检索调用总数有确定性预算，工具在耗尽时
   会返回收敛指令。分配纪律：具名事件各 1 次、每窗口 1-2 次、无果重试至多
   1 次；一旦收到"预算已用尽"，立即停止一切检索，基于已获材料输出最终结果
   ——半途成果好于轮次打满整体作废。
6. 去重合并：同一事件多来源报道合并为一条，保留最权威来源在前。
7. **增量提交（硬性纪律）**：每完成一个窗口/一批研究，立即用 submit_events
   提交该批事件（单次 ≤5 条）——不要把几十条事件攒到最终输出一把交，
   长 JSON 一处损坏会让全部成果作废。提交过的事件已落盘，
   **最终输出的 events 必须留空数组 []**，只写 coverage_notes（回声）。
   字符串字段内避免使用未转义的英文引号（用中文引号或改写）。
7. 影响评级口径（impact）：
   5=直接改变标的盈利/估值中枢或触发大幅重定价；4=直接影响需求、产品周期、
   政策约束或市场情绪；3=影响生态与中期预期；1-2=观察级。
   direction 指事件对标的的方向性含义（up/down/mixed/neutral），不是当日涨跌。
8. 溯源纪律（硬性要求）：
   - 每个事件 sources 至少一条可点击 URL，且 URL 必须**逐字复制**检索结果中的链接；
     日期、标题必须来自检索结果，禁止凭记忆编造或"修正"；
   - evidence_refs 填入检索工具返回的 evidence_id；
   - web_search 未配置（返回配置错误）时退回 HN/Yahoo 两路，并在
     coverage_notes 中如实声明联网搜索不可用。
9. 诚实原则：穷尽上述战术后仍无果就是无果，写进 coverage_notes；宁可返回
   "无对应事件"也不得强行凑一条。coverage_notes 同时复述你实际检索了哪些
   窗口与关键词（回声）。
"""


def build_event_researcher(settings: Settings) -> Agent[AppContext]:
    tools: list = [submit_events, search_hn_news, search_yahoo_finance_news]
    if not settings.mock_mode:
        # 联网搜索唯一后端 Tavily（确定性 API，与 LLM 供应方解耦）；
        # 未配 key 时工具会返回引导错误，agent 按提示词退回 HN/Yahoo 并如实声明
        tools.append(web_search)
    return Agent[AppContext](
        name="event-researcher",
        instructions=_INSTRUCTIONS.format(today=date.today().isoformat()),
        tools=tools,
        output_type=EventList,
        model=get_model(settings),
        model_settings=ModelSettings(max_tokens=settings.max_output_tokens),
    )
