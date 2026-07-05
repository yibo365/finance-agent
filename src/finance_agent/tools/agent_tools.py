"""SDK function_tool 层：把 M1 工具与工作区操作暴露给各 subagent。

结构约定：每个工具 = 纯逻辑 impl 函数（接 AppContext，可直接单测）
+ 薄 @function_tool 包装（只做 ctx 解包与 JSON 序列化）。
LLM 侧参数一律逻辑标识（ticker / dataset_id / artifact_id / skill name），
没有任何文件路径参数——WorkspaceFS 三原则的工具层落地。
"""

from __future__ import annotations

import json
import re
from typing import Any

from agents import RunContextWrapper, function_tool

from finance_agent.artifacts.spec import ArtifactSpec
from finance_agent.config import PACKAGE_ROOT
from finance_agent.context import AppContext
from finance_agent.contracts import ChangepointOut
from finance_agent.events import TOOL_ERROR_PREFIX
from finance_agent.tools import changepoints as cp_mod
from finance_agent.tools import market as market_mod
from finance_agent.tools import news as news_mod

NVDA_SEED = PACKAGE_ROOT / "seeds" / "nvda_ohlcv_nasdaq.json"

# mock 模式下的离线资讯（供无网/无检索环境跑通场景 A 流水线）
_MOCK_NEWS = [
    {"title": "ChatGPT: Optimizing Language Models for Dialogue", "url": "https://openai.com/index/chatgpt/",
     "source": "hn", "published_at": "2022-11-30T18:00:00+00:00", "score": 1414},
    {"title": "US restricts Nvidia chip exports to China", "url": "https://www.reuters.com/technology/us-restricts-exports-some-nvidia-chips-china-nvidia-says-2022-08-31/",
     "source": "hn", "published_at": "2022-08-31T12:00:00+00:00", "score": 890},
    {"title": "NVIDIA Blackwell Platform Arrives (B200/GB200/B100)", "url": "https://nvidianews.nvidia.com/news/nvidia-blackwell-platform-arrives-to-power-a-new-era-of-computing",
     "source": "hn", "published_at": "2024-03-18T20:00:00+00:00", "score": 640},
    {"title": "DeepSeek-R1 release sets off AI market rout", "url": "https://www.reuters.com/technology/chinas-deepseek-sets-off-ai-market-rout-2025-01-27/",
     "source": "hn", "published_at": "2025-01-27T14:00:00+00:00", "score": 2100},
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_TOOL_ERROR_LIMIT = 1500


def truncated_tool_error(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """工具失败消息进上下文前截断。

    SDK/pydantic 的默认错误会回显完整参数或完整输出（真实事故：report-builder
    的失败尝试里，几十 KB 的 spec 参数 + 错误回显成对驻留运行内上下文，
    单次请求滚到 7.8M tokens 超 8MB 上限）。前缀保持与 SDK 默认一致，
    事件流/历史回放的 ok 判定不受影响。
    """
    message = str(error)
    if len(message) > _TOOL_ERROR_LIMIT:
        message = message[:_TOOL_ERROR_LIMIT] + f"…（错误消息已截断，原长 {len(message)} 字符）"
    if "Invalid JSON input for tool" in message:
        # 该错误的最常见原因是参数超出输出 token 上限被截断——盲目原样重试
        # 只会确定性复现（真实事故：3 次各烧 2 分钟）。给模型可行动的出路。
        message += (
            "\n参数 JSON 非法通常是因为参数太长、超出输出 token 上限被截断——"
            "不要原样重试。改用引用而非内联：kline_chart 的事件/变化点请填 "
            "events_material / changepoints_material（材料 id 见任务材料），"
            "渲染器会从工作区注入全量；仅需覆盖个别条目时才内联。"
        )
    return f"{TOOL_ERROR_PREFIX}. Please try again. Error: {message}"


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ---------- data-collector 工具 ----------

def fetch_market_data_impl(app: AppContext, ticker: str, start: str, end: str) -> dict[str, Any]:
    seed = NVDA_SEED if ticker.upper() == "NVDA" and NVDA_SEED.is_file() else None
    if app.settings.mock_mode:
        if seed is None:
            raise market_mod.FetchError(ticker, [f"mock 模式仅有 NVDA 种子数据，无法获取 {ticker}"])
        sources: list[market_mod.MarketSource] = [market_mod.LocalCacheSource(seed)]
    else:
        sources = market_mod.default_sources(cache_path=seed)
    data = market_mod.fetch_ohlcv(
        ticker, start, end, sources=sources, evidence_log=app.workspace.evidence
    )
    dataset_id = f"ds-{_slug(ticker)}-{start.replace('-', '')}-{end.replace('-', '')}"
    app.workspace.store_dataset(
        dataset_id, data.df, ticker=ticker, source=data.source,
        evidence_id=data.evidence.id if data.evidence else "",
    )
    app.workspace.save_evidence()
    return {
        "dataset_id": dataset_id,
        "ticker": ticker,
        "rows": len(data.df),
        "start": str(data.df["date"].iloc[0]),
        "end": str(data.df["date"].iloc[-1]),
        "source": data.source,
        "evidence_id": data.evidence.id if data.evidence else "",
    }


def detect_changepoints_impl(
    app: AppContext, dataset_id: str, min_severity: int = 1, max_points: int = 40
) -> dict[str, Any]:
    df = app.workspace.load_dataset(dataset_id)
    entry = app.workspace.dataset_index()[dataset_id]
    result = cp_mod.detect_changepoints(
        df, evidence_log=app.workspace.evidence,
        source_evidence_id=entry.get("evidence_id") or None,
    )
    app.workspace.save_evidence()
    evidence_id = result.evidence.id if result.evidence else ""
    filtered = [p for p in result.points if p.severity >= min_severity]
    # 确定性硬上限：severity 降序、同级按时间——大列表原样穿过 LLM 输出会撞
    # max_tokens 截断（真实事故：五年 150 个变化点截断 JSON）
    capped = sorted(filtered, key=lambda p: (-p.severity, p.date))[:max_points]
    points = [
        ChangepointOut(
            date=p.date, kind=p.kind, rule=p.rule, severity=p.severity,
            window=list(p.window), evidence_refs=[evidence_id] if evidence_id else [],
        ).model_dump()
        for p in sorted(capped, key=lambda p: p.date)
    ]
    return {
        "dataset_id": dataset_id,
        "evidence_id": evidence_id,
        "total_detected": len(result.points),
        "after_min_severity": len(filtered),
        "returned": len(points),
        "omitted": len(filtered) - len(points),
        "min_severity": min_severity,
        "changepoints": points,
    }


@function_tool
def fetch_market_data(ctx: RunContextWrapper[AppContext], ticker: str, start: str, end: str) -> str:
    """拉取标的的日线 OHLCV（多源降级链），缓存进工作区并登记 dataset_id。

    Args:
        ticker: 标的代码，如 NVDA、GC=F（COMEX 黄金期货）、BTC-USD。
        start: 起始日期 YYYY-MM-DD。
        end: 结束日期 YYYY-MM-DD。
    """
    return _json(fetch_market_data_impl(ctx.context, ticker, start, end))


@function_tool
def run_changepoint_detection(
    ctx: RunContextWrapper[AppContext], dataset_id: str,
    min_severity: int = 1, max_points: int = 40,
) -> str:
    """对已缓存的 dataset 执行确定性变化点检测（趋势拐头/加速/回撤反弹/量能异常）。

    返回列表有硬上限（severity 降序截取），总检出/过滤/省略数照实报告——
    长区间请用 min_severity=2 起步。

    Args:
        dataset_id: fetch_market_data 返回的 dataset_id。
        min_severity: 仅返回不低于该严重度（1-3）的变化点。
        max_points: 返回条数上限（默认 40）。
    """
    return _json(detect_changepoints_impl(ctx.context, dataset_id, min_severity, max_points))


# ---------- event-researcher 工具 ----------

def _consume_search_budget(app: AppContext) -> dict[str, Any] | None:
    """检索预算的确定性收敛闸：超预算不再检索，指令模型立即汇总。

    真实事故：event-researcher 无预算连搜 98 次、20 轮打满后 Max turns
    exceeded 整体作废——7 分钟成果全丢。预算耗尽是软着陆：已获材料仍在
    运行上下文里，立即收敛还能产出完整结果。
    """
    if app.search_calls >= app.settings.search_budget:
        return {
            "items": [], "results": [], "evidence_id": "",
            "note": f"本次运行的检索预算（{app.settings.search_budget} 次）已用尽，"
                    "禁止继续任何检索：立即基于已获材料去重、评级并输出最终结果；"
                    "未覆盖的窗口在 coverage_notes 中如实说明。",
        }
    app.search_calls += 1
    return None


def search_hn_impl(
    app: AppContext, query: str, start: str, end: str, max_hits: int = 30
) -> dict[str, Any]:
    exhausted = _consume_search_budget(app)
    if exhausted is not None:
        return exhausted
    # 确定性纠错：Algolia 不支持布尔语法，长串组合词必然零命中（真实事故：
    # "NVIDIA OR NVDA OR ChatGPT OR…" 12 连败）。直接拒绝并告知正确用法。
    if " OR " in query.upper() or len(query.split()) > 4:
        raise ValueError(
            f"HN 检索不支持 OR/长组合查询（收到：{query!r}）。"
            "请改为 1-2 个词的单个关键词（如 chatgpt / nvidia export / deepseek），"
            "多个关键词分多次调用。"
        )
    if app.settings.mock_mode:
        items = [n for n in _MOCK_NEWS if start <= n["published_at"][:10] <= end]
        # mock 也登记 evidence：产物事件的 sources.url 要过溯源校验（对照集
        # 来自 evidence.urls），离线路径不登记就会把场景 A 的渲染整体拒掉
        evidence = app.workspace.evidence.record(
            "news",
            source_url="mock://hn-offline",
            urls=[n["url"] for n in items],
            query={"query": query, "start": start, "end": end, "source": "hn-mock"},
            excerpt="；".join(n["title"] for n in items[:5]) or "（无结果）",
        )
        app.workspace.save_evidence()
        return {"query": query, "items": items, "evidence_id": evidence.id, "mock": True}
    result = news_mod.search_hn_news(
        query, start, end, evidence_log=app.workspace.evidence, max_hits=max_hits
    )
    app.workspace.save_evidence()
    return {
        "query": query,
        "items": [item.model_dump() for item in result.items],
        "evidence_id": result.evidence.id if result.evidence else "",
    }


def search_yahoo_news_impl(app: AppContext, query: str, max_items: int = 20) -> dict[str, Any]:
    exhausted = _consume_search_budget(app)
    if exhausted is not None:
        return exhausted
    if app.settings.mock_mode:
        return {"query": query, "items": [], "evidence_id": "", "mock": True,
                "note": "mock 模式下 Yahoo 资讯不可用"}
    result = news_mod.fetch_yahoo_news(
        query, evidence_log=app.workspace.evidence, max_items=max_items
    )
    app.workspace.save_evidence()
    return {
        "query": query,
        "items": [item.model_dump() for item in result.items],
        "evidence_id": result.evidence.id if result.evidence else "",
    }


@function_tool
def search_hn_news(
    ctx: RunContextWrapper[AppContext], query: str, start: str, end: str, max_hits: int = 30
) -> str:
    """按关键词 + 日期范围检索 Hacker News 历史（适合围绕变化点时间窗做定向回溯）。

    Args:
        query: 检索关键词（英文效果更好，如 chatgpt / nvidia export / deepseek）。
        start: 范围起点 YYYY-MM-DD。
        end: 范围终点 YYYY-MM-DD。
        max_hits: 最多返回条数。
    """
    return _json(search_hn_impl(ctx.context, query, start, end, max_hits))


@function_tool
def search_yahoo_finance_news(
    ctx: RunContextWrapper[AppContext], query: str, max_items: int = 20
) -> str:
    """检索 Yahoo Finance 近期资讯（财经媒体视角；无历史范围能力，适合补充近况）。

    Args:
        query: 标的代码或关键词。
        max_items: 最多返回条数。
    """
    return _json(search_yahoo_news_impl(ctx.context, query, max_items))


def submit_events_impl(app: AppContext, events: list[Any]) -> dict[str, Any]:
    """增量提交事件到运行内累积器（按 日期+标题 去重）。

    大列表攒到最终输出一把序列化是最脆的路径（JSON 一坏全部作废）；
    小批量经工具提交，坏一批只损失一批，且 Max turns/最终输出损坏时
    已提交的事件仍可由包装层合并返回。
    """
    from finance_agent.contracts import EventItem

    accepted = 0
    duplicates = 0
    seen = {(e.date, e.title.strip()) for e in app.collected_events}
    for event in events:
        item = event if isinstance(event, EventItem) else EventItem.model_validate(event)
        key = (item.date, item.title.strip())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        app.collected_events.append(item)
        accepted += 1
    return {
        "accepted": accepted,
        "duplicates_skipped": duplicates,
        "total_collected": len(app.collected_events),
        "note": "已落盘。最终输出时 events 留空数组，只写 coverage_notes——不要重复输出已提交的事件。",
    }


@function_tool(failure_error_function=truncated_tool_error)
def submit_events(ctx: RunContextWrapper[AppContext], events: list[Any]) -> str:
    """增量提交研究到的事件（每完成一个窗口/一批就提交，单次 ≤5 条）。

    事件字段同 EventItem：date/title/category/direction/move/impact/notes/
    sources/evidence_refs。提交过的事件**不要**再写进最终输出（events 留空）。

    Args:
        events: 本批事件对象列表（≤5 条；notes 精简、字符串内不要用未转义引号）。
    """
    from finance_agent.contracts import EventItem

    items = [EventItem.model_validate(e) for e in events]
    return _json(submit_events_impl(ctx.context, items))


# ---------- 联网搜索（Tavily 确定性 API，唯一后端） ----------

async def tavily_web_search_impl(
    app: AppContext, query: str, max_results: int | None = None, client: Any = None
) -> dict[str, Any]:
    """确定性检索：直连 Tavily API，返回结构化结果列表，不经 LLM 转述。

    换 LLM 供应方不改变检索数据（用户明确要求：web search 不能依赖 LLM）。
    """
    from finance_agent.tools.websearch import tavily_search

    exhausted = _consume_search_budget(app)
    if exhausted is not None:
        return exhausted
    if not app.settings.has_tavily_api_key():
        raise ValueError(
            "联网搜索未配置：缺少 TAVILY_API_KEY（Web 界面左下角\"设置\"或 .env 中填写）。"
            "在配置好之前请改用 search_hn_news / search_yahoo_finance_news，"
            "并在 coverage_notes 中如实说明联网搜索不可用。"
        )
    result = await tavily_search(
        query,
        api_key=app.settings.tavily_api_key,
        max_results=max_results or app.settings.web_max_results,
        client=client,
        evidence_log=app.workspace.evidence,
    )
    app.workspace.save_evidence()
    return {
        "query": query,
        "results": [item.model_dump() for item in result.items],
        "evidence_id": result.evidence.id if result.evidence else "",
        "note": "" if result.items else "无结果：请换更短/更通用的关键词重试",
    }


@function_tool
async def web_search(
    ctx: RunContextWrapper[AppContext], query: str, max_results: int | None = None
) -> str:
    """联网检索（Tavily），返回结构化结果列表与 evidence_id。

    results:[{title,url,snippet,published}]——事件的日期/标题/URL 只能
    逐字取自这些结果。

    Args:
        query: 检索问题（关键词或自然语言，含日期上下文更准）。
        max_results: 检索结果条数；缺省用全局配置。
    """
    return _json(await tavily_web_search_impl(ctx.context, query, max_results))


# ---------- skill 工具（orchestrator / report-builder） ----------

def list_skills_impl(app: AppContext) -> dict[str, Any]:
    from finance_agent.skills.loader import index_lines, scan_skills

    return {"skills": index_lines(scan_skills())}


def load_skill_impl(app: AppContext, name: str) -> str:
    from finance_agent.skills.loader import load_skill, scan_skills

    return load_skill(name, scan_skills())


@function_tool
def list_skills(ctx: RunContextWrapper[AppContext]) -> str:
    """列出可用的产物 skill（名称、产物类型、一句话说明）。"""
    return _json(list_skills_impl(ctx.context))


@function_tool
def load_skill(ctx: RunContextWrapper[AppContext], name: str) -> str:
    """读入指定 skill 的完整方法论（组织建议、标注要求、写作规范）。

    Args:
        name: skill 名称，见 list_skills。
    """
    return load_skill_impl(ctx.context, name)


# ---------- 材料（alignment-analyst / report-builder） ----------

@function_tool
def load_material(ctx: RunContextWrapper[AppContext], material_id: str) -> str:
    """读取上游环节落盘的全量材料（变化点列表 / 事件列表 / 对齐矩阵）。

    任务材料 context_data 里给出的是 material_id（形如 mat-events-1）——
    材料按引用传递，全量内容用本工具按需读取。

    Args:
        material_id: 材料标识，见 context_data。
    """
    return _json(ctx.context.workspace.load_material(material_id))


# ---------- 工作区/产物工具 ----------

def list_artifacts_impl(app: AppContext) -> dict[str, Any]:
    return {
        "session_id": app.workspace.session_id,
        "artifacts": app.workspace.list_artifacts(),
        "datasets": app.workspace.dataset_index(),
    }


@function_tool
def list_artifacts(ctx: RunContextWrapper[AppContext]) -> str:
    """列出当前会话工作区的全部产物（版本历史）与已缓存 dataset。"""
    return _json(list_artifacts_impl(ctx.context))


@function_tool
def read_artifact(
    ctx: RunContextWrapper[AppContext], artifact_id: str, version: int | None = None
) -> str:
    """读回产物的 ArtifactSpec（默认当前版本），用于定点修改。

    Args:
        artifact_id: 产物标识。
        version: 指定历史版本号；缺省为当前版本。
    """
    spec = ctx.context.workspace.read_artifact_spec(artifact_id, version)
    return spec.model_dump_json()


@function_tool(failure_error_function=truncated_tool_error)
def render_artifact(
    ctx: RunContextWrapper[AppContext], spec: ArtifactSpec, change_summary: str = "初版"
) -> str:
    """按 ArtifactSpec 渲染新产物（v1）并登记 manifest。spec 校验失败会报错，请修正后重试。

    Args:
        spec: 完整的 ArtifactSpec（block 树；data_ref 用 dataset_id）。
        change_summary: 一句话版本说明。
    """
    version = ctx.context.workspace.render_artifact(spec, change_summary=change_summary)
    return _json({
        "artifact_id": spec.artifact_id, "version": version.v, "kind": spec.kind,
        "file": str(ctx.context.workspace.dir / version.file),
        "change_summary": version.change_summary,
    })


@function_tool(failure_error_function=truncated_tool_error)
def update_artifact(
    ctx: RunContextWrapper[AppContext], artifact_id: str, spec: ArtifactSpec, change_summary: str
) -> str:
    """对既有产物做定点修改后的重渲染：版本 +1，旧版全部保留。只改动需要变的 block。

    Args:
        artifact_id: 要修改的产物标识（不可变更）。
        spec: 修改后的完整 ArtifactSpec（未涉及的 block 原样保留）。
        change_summary: 一句话说明本次改了什么。
    """
    version = ctx.context.workspace.update_artifact(artifact_id, spec, change_summary=change_summary)
    return _json({
        "artifact_id": artifact_id, "version": version.v, "kind": spec.kind,
        "file": str(ctx.context.workspace.dir / version.file),
        "change_summary": version.change_summary,
    })
