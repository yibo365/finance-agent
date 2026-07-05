"""行情工具：多源降级链拉取 OHLCV 日线。

解析函数为纯函数（移植自原型 nvda_data_loader.js），与网络访问分离，便于单测。
降级链语义与 JS 版一致：逐源尝试，任一源成功即返回；全失败抛 FetchError，
携带每个源的失败原因。所有源统一免 key。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import pandas as pd

from finance_agent.provenance import Evidence, EvidenceLog

COLUMNS = ["date", "open", "high", "low", "close", "volume"]
USER_AGENT = "Mozilla/5.0 (finance-agent; research tool)"

# Stooq 符号映射：美股默认 <ticker>.us，特殊标的显式覆盖
_STOOQ_OVERRIDES = {
    "BTC-USD": "btcusd",
    "GC=F": "gc.f",
}


class FetchError(RuntimeError):
    """降级链全部失败。attempts 保留每个源的失败原因，供上层决策与溯源。"""

    def __init__(self, ticker: str, attempts: list[str]) -> None:
        self.attempts = attempts
        super().__init__(f"{ticker} 行情获取失败：" + "；".join(attempts))


def _clean(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    """统一清洗：数值化、去空行、区间过滤、按日期升序、同日去重。"""
    df = df.copy()
    for col in COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=COLUMNS)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return (
        df.sort_values("date")
        .drop_duplicates(subset="date", keep="first")
        .reset_index(drop=True)[COLUMNS]
    )


def parse_yahoo_chart(payload: dict[str, Any]) -> pd.DataFrame:
    """Yahoo v8 chart API：chart.result[0] 的 timestamp + indicators.quote。"""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        error = (payload.get("chart") or {}).get("error") or {}
        raise ValueError(error.get("description") or "Yahoo 返回为空")
    timestamps = result[0].get("timestamp") or []
    quotes = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    if not quotes:
        raise ValueError("Yahoo 返回缺少 OHLCV 字段")
    return pd.DataFrame(
        {
            "date": [
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                for ts in timestamps
            ],
            "open": quotes.get("open"),
            "high": quotes.get("high"),
            "low": quotes.get("low"),
            "close": quotes.get("close"),
            "volume": quotes.get("volume"),
        }
    )


def parse_stooq_csv(text: str) -> pd.DataFrame:
    """Stooq 日线 CSV：Date,Open,High,Low,Close,Volume。"""
    stripped = text.strip()
    if not stripped or stripped.lower().startswith("no data"):
        raise ValueError("Stooq 返回为空")
    df = pd.read_csv(io.StringIO(stripped))
    df.columns = [col.strip().lower() for col in df.columns]
    missing = [col for col in COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Stooq CSV 缺少列：{missing}")
    return df[COLUMNS]


def parse_nasdaq_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Nasdaq 历史行情导出格式（本地缓存种子用），如 nvda_ohlcv_nasdaq.json。"""
    table = ((payload.get("data") or {}).get("tradesTable") or {})
    rows = table.get("rows") or []
    if not rows:
        raise ValueError("Nasdaq 缓存载荷为空")

    def _date(value: str) -> str | None:
        try:
            return datetime.strptime(value, "%m/%d/%Y").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return None

    def _num(value: Any) -> Any:
        if value is None:
            return None
        return str(value).replace("$", "").replace(",", "").strip() or None

    return pd.DataFrame(
        [
            {
                "date": _date(row.get("date")),
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "close": _num(row.get("close")),
                "volume": _num(row.get("volume")),
            }
            for row in rows
        ]
    ).dropna(subset=["date"])


def parse_cache_rows(payload: dict[str, Any]) -> pd.DataFrame:
    """本工具自己的缓存格式：{"rows": [{date, open, high, low, close, volume}]}。"""
    rows = payload.get("rows") or []
    if not rows:
        raise ValueError("本地缓存为空")
    return pd.DataFrame(rows)


@dataclass
class SourceResult:
    df: pd.DataFrame
    url: str


class MarketSource(Protocol):
    label: str

    def fetch(self, client: httpx.Client, ticker: str, start: str, end: str) -> SourceResult: ...


class YahooChartSource:
    """Yahoo v8 chart API。query1/query2 是两台独立前端主机，互为降级。"""

    def __init__(self, host: str = "query1") -> None:
        self.host = host
        self.label = f"Yahoo Finance ({host})"

    def fetch(self, client: httpx.Client, ticker: str, start: str, end: str) -> SourceResult:
        period1 = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_next = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        url = f"https://{self.host}.finance.yahoo.com/v8/finance/chart/{ticker}"
        response = client.get(
            url,
            params={
                "period1": period1,
                "period2": int(end_next.timestamp()),
                "interval": "1d",
                "events": "history",
            },
        )
        response.raise_for_status()
        return SourceResult(df=parse_yahoo_chart(response.json()), url=str(response.url))


def stooq_symbol(ticker: str) -> str:
    if ticker in _STOOQ_OVERRIDES:
        return _STOOQ_OVERRIDES[ticker]
    return f"{ticker.lower()}.us" if ticker.isalpha() else ticker.lower()


class StooqSource:
    """Stooq 日线 CSV。2026-07 实测其接口已加 JS 工作量证明反爬（返回验证页而非
    CSV），故不在默认降级链中；保留实现供反爬解除或其他网络环境下启用。"""

    label = "Stooq"

    def fetch(self, client: httpx.Client, ticker: str, start: str, end: str) -> SourceResult:
        response = client.get(
            "https://stooq.com/q/d/l/",
            params={
                "s": stooq_symbol(ticker),
                "d1": start.replace("-", ""),
                "d2": end.replace("-", ""),
                "i": "d",
            },
        )
        response.raise_for_status()
        return SourceResult(df=parse_stooq_csv(response.text), url=str(response.url))


class LocalCacheSource:
    """本地缓存/种子文件，兼容 Nasdaq 导出与本工具缓存两种格式（按结构自动识别）。"""

    label = "Local Cache"

    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()  # as_uri 与溯源记录都需要绝对路径

    def fetch(self, client: httpx.Client, ticker: str, start: str, end: str) -> SourceResult:
        import json

        payload = json.loads(self._path.read_text(encoding="utf-8"))
        cached_ticker = payload.get("symbol") or ((payload.get("data") or {}).get("symbol"))
        if cached_ticker and cached_ticker.upper() != ticker.upper():
            raise ValueError(f"缓存标的不匹配：{cached_ticker} != {ticker}")
        if "rows" in payload:
            df = parse_cache_rows(payload)
        else:
            df = parse_nasdaq_payload(payload)
        return SourceResult(df=df, url=self._path.as_uri())


@dataclass
class MarketData:
    ticker: str
    df: pd.DataFrame
    source: str
    url: str
    evidence: Evidence | None


def default_sources(cache_path: Path | None = None) -> list[MarketSource]:
    """默认降级链：Yahoo 双前端主机 → 本地缓存（如提供）。"""
    sources: list[MarketSource] = [YahooChartSource("query1"), YahooChartSource("query2")]
    if cache_path is not None:
        sources.append(LocalCacheSource(cache_path))
    return sources


def fetch_ohlcv(
    ticker: str,
    start: str,
    end: str,
    *,
    sources: list[MarketSource] | None = None,
    client: httpx.Client | None = None,
    evidence_log: EvidenceLog | None = None,
    cache_path: Path | None = None,
) -> MarketData:
    """按降级链拉取日线 OHLCV。任一源成功即返回；全失败抛 FetchError。"""
    chain = sources if sources is not None else default_sources(cache_path)
    own_client = client is None
    http = client or httpx.Client(
        timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    attempts: list[str] = []
    try:
        for source in chain:
            try:
                result = source.fetch(http, ticker, start, end)
                df = _clean(result.df, start, end)
                if df.empty:
                    raise ValueError("未取得有效 OHLCV 数据")
            except Exception as exc:  # noqa: BLE001 —— 汇总为降级链错误
                attempts.append(f"{source.label}: {exc}")
                continue
            evidence = None
            if evidence_log is not None:
                evidence = evidence_log.record(
                    "market_data",
                    source_url=result.url,
                    query={"ticker": ticker, "start": start, "end": end, "source": source.label},
                    excerpt=(
                        f"{len(df)} 行日线，{df['date'].iloc[0]} 至 {df['date'].iloc[-1]}，"
                        f"来源 {source.label}"
                    ),
                )
            return MarketData(
                ticker=ticker, df=df, source=source.label, url=result.url, evidence=evidence
            )
        raise FetchError(ticker, attempts)
    finally:
        if own_client:
            http.close()
