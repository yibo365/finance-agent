"""资讯工具：HN Algolia 历史检索 + Yahoo Finance 资讯。

两路均免 key。HN Algolia 支持按时间范围查历史（event-researcher 拿拐点
时间窗做定向检索的主力）；Yahoo 资讯补充财经媒体视角。第三路 web_search
是 SDK 托管工具，在 M4 由 event-researcher 直接挂载，不在此文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from finance_agent.provenance import Evidence, EvidenceLog

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
USER_AGENT = "Mozilla/5.0 (finance-agent; research tool)"


class NewsItem(BaseModel):
    title: str
    url: str
    source: str          # "hn" 或 yahoo 的 publisher 名
    published_at: str    # ISO 日期时间（UTC）
    score: int | None = None  # HN points；yahoo 无此概念为 None


@dataclass
class NewsResult:
    items: list[NewsItem]
    evidence: Evidence | None


def _to_iso(unix_seconds: int | float) -> str:
    return datetime.fromtimestamp(int(unix_seconds), tz=UTC).isoformat(
        timespec="seconds"
    )


def _epoch(day: str, *, end_of_day: bool = False) -> int:
    moment = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    if end_of_day:
        moment = moment.replace(hour=23, minute=59, second=59)
    return int(moment.timestamp())


def parse_hn_hits(payload: dict[str, Any]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for hit in payload.get("hits") or []:
        title = hit.get("title") or hit.get("story_title")
        if not title:
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        items.append(
            NewsItem(
                title=title,
                url=url,
                source="hn",
                published_at=_to_iso(hit.get("created_at_i") or 0),
                score=hit.get("points"),
            )
        )
    return items


def parse_yahoo_news(payload: dict[str, Any]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in payload.get("news") or []:
        title = entry.get("title")
        url = entry.get("link")
        if not title or not url:
            continue
        items.append(
            NewsItem(
                title=title,
                url=url,
                source=entry.get("publisher") or "yahoo",
                published_at=_to_iso(entry.get("providerPublishTime") or 0),
            )
        )
    return items


def search_hn_news(
    query: str,
    start: str,
    end: str,
    *,
    client: httpx.Client | None = None,
    evidence_log: EvidenceLog | None = None,
    max_hits: int = 50,
) -> NewsResult:
    """按关键词 + 时间范围检索 HN 历史（story），按时间倒序。"""
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": max_hits,
        "numericFilters": (
            f"created_at_i>={_epoch(start)},created_at_i<={_epoch(end, end_of_day=True)}"
        ),
    }
    payload, url = _get_json(HN_SEARCH_URL, params, client)
    items = parse_hn_hits(payload)
    evidence = None
    if evidence_log is not None:
        evidence = evidence_log.record(
            "news",
            source_url=url,
            urls=[item.url for item in items],
            query={"query": query, "start": start, "end": end, "source": "hn-algolia"},
            excerpt="；".join(item.title for item in items[:5]) or "（无结果）",
        )
    return NewsResult(items=items, evidence=evidence)


def fetch_yahoo_news(
    query: str,
    *,
    client: httpx.Client | None = None,
    evidence_log: EvidenceLog | None = None,
    max_items: int = 20,
) -> NewsResult:
    """Yahoo Finance 资讯检索（仅近期资讯，无历史范围参数）。"""
    params = {"q": query, "quotesCount": 0, "newsCount": max_items}
    payload, url = _get_json(YAHOO_SEARCH_URL, params, client)
    items = parse_yahoo_news(payload)
    evidence = None
    if evidence_log is not None:
        evidence = evidence_log.record(
            "news",
            source_url=url,
            urls=[item.url for item in items],
            query={"query": query, "source": "yahoo-finance"},
            excerpt="；".join(item.title for item in items[:5]) or "（无结果）",
        )
    return NewsResult(items=items, evidence=evidence)


def _get_json(
    url: str, params: dict[str, Any], client: httpx.Client | None
) -> tuple[dict[str, Any], str]:
    own_client = client is None
    http = client or httpx.Client(
        timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        response = http.get(url, params=params)
        response.raise_for_status()
        return response.json(), str(response.url)
    finally:
        if own_client:
            http.close()
