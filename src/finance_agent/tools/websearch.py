"""确定性联网搜索后端：Tavily Search API。

与 tools/news.py 的 HN/Yahoo 两路同构：直连 HTTP、返回结构化条目列表
（标题/URL/摘要/日期）、登记 evidence（含全部候选 URL）。不经任何 LLM
转述——检索结果与 LLM 供应方解耦，换模型不改变检索数据，可复现、可回归。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from finance_agent.provenance import Evidence, EvidenceLog

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_SNIPPET_LIMIT = 500  # 摘要截断：进 LLM 上下文的是条目列表，单条不允许无限长


class SearchItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    published: str = ""   # Tavily 部分结果带 published_date；缺失为空串


@dataclass
class WebSearchResult:
    items: list[SearchItem]
    evidence: Evidence | None


def parse_tavily_results(payload: dict) -> list[SearchItem]:
    items: list[SearchItem] = []
    for entry in payload.get("results") or []:
        title, url = entry.get("title"), entry.get("url")
        if not title or not url:
            continue
        items.append(
            SearchItem(
                title=title,
                url=url,
                snippet=(entry.get("content") or "")[:_SNIPPET_LIMIT],
                published=entry.get("published_date") or "",
            )
        )
    return items


async def tavily_search(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    client: httpx.AsyncClient | None = None,
    evidence_log: EvidenceLog | None = None,
) -> WebSearchResult:
    """Tavily 检索。搜索失败直接抛 httpx 异常（工具层如实回报给 agent 重试）。"""
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await http.post(
            TAVILY_SEARCH_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,  # 不要 LLM 生成的答案，只要原始结果
            },
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if own_client:
            await http.aclose()
    items = parse_tavily_results(payload)
    evidence = None
    if evidence_log is not None:
        evidence = evidence_log.record(
            "search",
            source_url=items[0].url if items else "tavily:no-results",
            urls=[item.url for item in items],
            query={"query": query, "backend": "tavily", "max_results": max_results},
            excerpt="；".join(item.title for item in items[:5]) or "（无结果）",
        )
    return WebSearchResult(items=items, evidence=evidence)
