"""行情工具单测：三源解析、清洗、降级链（mock HTTP）。"""

import json

import httpx
import pytest

from finance_agent.provenance import EvidenceLog
from finance_agent.tools.market import (
    COLUMNS,
    FetchError,
    LocalCacheSource,
    StooqSource,
    YahooChartSource,
    fetch_ohlcv,
    parse_nasdaq_payload,
    parse_stooq_csv,
    parse_yahoo_chart,
    stooq_symbol,
)

YAHOO_PAYLOAD = {
    "chart": {
        "result": [
            {
                "timestamp": [1704153600, 1704240000, 1704326400],  # 2024-01-02/03/04 UTC
                "indicators": {
                    "quote": [
                        {
                            "open": [48.1, 47.5, None],
                            "high": [49.0, 48.2, 47.0],
                            "low": [47.8, 47.0, 46.0],
                            "close": [48.8, 47.6, 46.5],
                            "volume": [1000, 1100, 1200],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}

STOOQ_CSV = "Date,Open,High,Low,Close,Volume\n2024-01-02,48.1,49.0,47.8,48.8,1000\n2024-01-03,47.5,48.2,47.0,47.6,1100\n"

NASDAQ_PAYLOAD = {
    "data": {
        "symbol": "NVDA",
        "tradesTable": {
            "rows": [
                {"date": "01/03/2024", "close": "$47.60", "volume": "1,100",
                 "open": "$47.50", "high": "$48.20", "low": "$47.00"},
                {"date": "01/02/2024", "close": "$48.80", "volume": "1,000",
                 "open": "$48.10", "high": "$49.00", "low": "$47.80"},
            ]
        },
    }
}


def test_parse_yahoo_chart():
    df = parse_yahoo_chart(YAHOO_PAYLOAD)
    assert list(df.columns) == COLUMNS
    assert df["date"].tolist() == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_parse_yahoo_error_payload():
    with pytest.raises(ValueError, match="Not Found"):
        parse_yahoo_chart({"chart": {"result": None, "error": {"description": "Not Found"}}})


def test_parse_stooq_csv():
    df = parse_stooq_csv(STOOQ_CSV)
    assert len(df) == 2
    assert df["close"].tolist() == [48.8, 47.6]


def test_parse_stooq_empty():
    with pytest.raises(ValueError):
        parse_stooq_csv("No data")


def test_parse_nasdaq_payload_strips_currency_and_commas():
    df = parse_nasdaq_payload(NASDAQ_PAYLOAD)
    assert df["date"].tolist() == ["2024-01-03", "2024-01-02"]  # 清洗前保持原序
    assert df["close"].tolist() == ["47.60", "48.80"]


def test_stooq_symbol_mapping():
    assert stooq_symbol("NVDA") == "nvda.us"
    assert stooq_symbol("BTC-USD") == "btcusd"
    assert stooq_symbol("GC=F") == "gc.f"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_ohlcv_cleans_filters_and_records_evidence():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "query1.finance.yahoo.com" in request.url.host
        return httpx.Response(200, json=YAHOO_PAYLOAD)

    log = EvidenceLog("t")
    data = fetch_ohlcv(
        "NVDA", "2024-01-02", "2024-01-03",
        sources=[YahooChartSource()], client=_client(handler), evidence_log=log,
    )
    # 2024-01-04 的 open 为 None 应被清洗；2024-01-04 也超出 end 区间
    assert data.df["date"].tolist() == ["2024-01-02", "2024-01-03"]
    assert data.source == "Yahoo Finance (query1)"
    assert data.evidence is not None and data.evidence.kind == "market_data"
    assert log.get(data.evidence.id).query["ticker"] == "NVDA"


def test_fetch_ohlcv_falls_back_to_stooq():
    def handler(request: httpx.Request) -> httpx.Response:
        if "yahoo" in request.url.host:
            return httpx.Response(500)
        return httpx.Response(200, text=STOOQ_CSV)

    data = fetch_ohlcv(
        "NVDA", "2024-01-01", "2024-12-31",
        sources=[YahooChartSource(), StooqSource()], client=_client(handler),
    )
    assert data.source == "Stooq"
    assert len(data.df) == 2


def test_fetch_ohlcv_all_sources_failed_reports_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(FetchError) as exc_info:
        fetch_ohlcv(
            "NVDA", "2024-01-01", "2024-12-31",
            sources=[YahooChartSource(), StooqSource()], client=_client(handler),
        )
    assert len(exc_info.value.attempts) == 2
    assert "Yahoo Finance" in str(exc_info.value)


def test_local_cache_source_nasdaq_format(tmp_path):
    cache = tmp_path / "nvda.json"
    cache.write_text(json.dumps(NASDAQ_PAYLOAD), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    data = fetch_ohlcv(
        "NVDA", "2024-01-01", "2024-12-31",
        sources=[YahooChartSource(), LocalCacheSource(cache)], client=_client(handler),
    )
    assert data.source == "Local Cache"
    assert data.df["date"].tolist() == ["2024-01-02", "2024-01-03"]  # 清洗后升序
    assert data.df["close"].tolist() == [48.8, 47.6]


def test_local_cache_rejects_ticker_mismatch(tmp_path):
    cache = tmp_path / "nvda.json"
    cache.write_text(json.dumps(NASDAQ_PAYLOAD), encoding="utf-8")
    with pytest.raises(FetchError, match="缓存标的不匹配"):
        fetch_ohlcv("AAPL", "2024-01-01", "2024-12-31",
                    sources=[LocalCacheSource(cache)], client=_client(lambda r: httpx.Response(500)))
