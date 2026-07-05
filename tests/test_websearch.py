"""Tavily 确定性搜索后端单测：解析、evidence 登记、错误传播、工具分派。"""

import asyncio
import json

import httpx
import pytest

from finance_agent.config import Settings
from finance_agent.context import AppContext
from finance_agent.provenance import EvidenceLog
from finance_agent.tools.websearch import parse_tavily_results, tavily_search
from finance_agent.workspace import Workspace

_PAYLOAD = {
    "query": "贵州茅台 2023年10月 股价",
    "results": [
        {"title": "茅台单日大跌5.67%", "url": "https://finance.example/moutai-drop",
         "content": "2023年10月19日……", "published_date": "2023-10-20", "score": 0.92},
        {"title": "白酒板块回调", "url": "https://news.example/baijiu",
         "content": "板块资金流出……"},
        {"title": "无链接条目被跳过", "url": None},
    ],
}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_tavily_results_maps_fields_and_skips_broken():
    items = parse_tavily_results(_PAYLOAD)
    assert [i.url for i in items] == [
        "https://finance.example/moutai-drop", "https://news.example/baijiu",
    ]
    assert items[0].published == "2023-10-20" and items[1].published == ""


def test_tavily_search_records_evidence_with_all_urls():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_PAYLOAD)

    log = EvidenceLog("s1")
    result = asyncio.run(tavily_search(
        "茅台 2023年10月", api_key="tvly-x", max_results=7,
        client=_client(handler), evidence_log=log,
    ))
    assert captured["auth"] == "Bearer tvly-x"
    assert captured["body"]["max_results"] == 7
    assert captured["body"]["include_answer"] is False  # 只要原始结果，不要 LLM 答案
    assert result.evidence is not None and result.evidence.kind == "search"
    assert result.evidence.source_url == "https://finance.example/moutai-drop"
    assert set(result.evidence.urls) == {
        "https://finance.example/moutai-drop", "https://news.example/baijiu",
    }


def test_tavily_search_empty_results_records_placeholder():
    log = EvidenceLog("s1")
    result = asyncio.run(tavily_search(
        "无结果查询", api_key="tvly-x",
        client=_client(lambda r: httpx.Response(200, json={"results": []})),
        evidence_log=log,
    ))
    assert result.items == []
    assert result.evidence.source_url == "tavily:no-results"


def test_tavily_search_http_error_propagates():
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(tavily_search(
            "q", api_key="bad",
            client=_client(lambda r: httpx.Response(401, json={"error": "bad key"})),
        ))


def test_tavily_impl_returns_structured_results_and_note(tmp_path):
    from finance_agent.tools.agent_tools import tavily_web_search_impl

    settings = Settings(api_key="k", search_backend="tavily",
                        tavily_api_key="tvly-x", web_max_results=4)
    app = AppContext(settings=settings, workspace=Workspace.create(tmp_path / "o"))
    out = asyncio.run(tavily_web_search_impl(
        app, "茅台", client=_client(lambda r: httpx.Response(200, json=_PAYLOAD)),
    ))
    assert out["results"][0]["title"] == "茅台单日大跌5.67%"
    assert out["evidence_id"].startswith("ev-")
    assert out["note"] == ""
    assert "summary" not in out  # 无任何 LLM 生成内容
