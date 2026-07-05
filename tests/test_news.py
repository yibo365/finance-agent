"""资讯工具单测：HN Algolia / Yahoo 解析与请求参数构造。"""

import httpx

from finance_agent.provenance import EvidenceLog
from finance_agent.tools.news import (
    fetch_yahoo_news,
    parse_hn_hits,
    parse_yahoo_news,
    search_hn_news,
)

HN_PAYLOAD = {
    "hits": [
        {
            "title": "ChatGPT: Optimizing Language Models for Dialogue",
            "url": "https://openai.com/blog/chatgpt/",
            "points": 1414,
            "created_at_i": 1669838444,  # 2022-11-30
            "objectID": "33780890",
        },
        {
            "title": None,  # 无标题的评论类命中应被跳过
            "url": None,
            "points": 3,
            "created_at_i": 1669838500,
            "objectID": "33780999",
        },
        {
            "title": "Show HN: something",
            "url": None,  # 无外链 → 回落到 HN item 页
            "points": 55,
            "created_at_i": 1669840000,
            "objectID": "33781000",
        },
    ]
}

YAHOO_PAYLOAD = {
    "news": [
        {
            "title": "Nvidia unveils B100",
            "link": "https://finance.yahoo.com/news/nvidia-b100.html",
            "publisher": "Reuters",
            "providerPublishTime": 1710720000,
        },
        {"title": "no link, skipped", "link": None},
    ]
}


def test_parse_hn_hits_skips_untitled_and_falls_back_to_item_page():
    items = parse_hn_hits(HN_PAYLOAD)
    assert len(items) == 2
    assert items[0].title.startswith("ChatGPT")
    assert items[0].score == 1414
    assert items[0].published_at.startswith("2022-11-30")
    assert items[1].url == "https://news.ycombinator.com/item?id=33781000"


def test_parse_yahoo_news_skips_missing_link():
    items = parse_yahoo_news(YAHOO_PAYLOAD)
    assert len(items) == 1
    assert items[0].source == "Reuters"
    assert items[0].published_at.startswith("2024-03-18")


def test_search_hn_news_builds_date_range_filter_and_evidence():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=HN_PAYLOAD)

    log = EvidenceLog("t")
    result = search_hn_news(
        "chatgpt", "2022-11-01", "2022-12-15",
        client=httpx.Client(transport=httpx.MockTransport(handler)), evidence_log=log,
    )
    filters = captured["params"]["numericFilters"]
    assert "created_at_i>=1667260800" in filters   # 2022-11-01T00:00Z
    assert "created_at_i<=1671148799" in filters   # 2022-12-15T23:59:59Z
    assert captured["params"]["tags"] == "story"
    assert len(result.items) == 2
    assert result.evidence is not None and "ChatGPT" in result.evidence.excerpt


def test_fetch_yahoo_news_records_evidence():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params)["q"] == "NVDA"
        return httpx.Response(200, json=YAHOO_PAYLOAD)

    log = EvidenceLog("t")
    result = fetch_yahoo_news(
        "NVDA", client=httpx.Client(transport=httpx.MockTransport(handler)), evidence_log=log,
    )
    assert len(result.items) == 1
    assert result.evidence is not None
    assert result.evidence.query["source"] == "yahoo-finance"
