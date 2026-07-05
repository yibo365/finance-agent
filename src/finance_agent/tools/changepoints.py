"""拐点检测：确定性启发式，纯 pandas/numpy 实现。

设计原则（docs/architecture.md §1）：LLM 不参与"哪里是拐点"的判定——每个变化点
必须能说清被哪条规则、哪段数据窗口触发，可复现、可单测、可溯源。

四条规则 + 合并：
1. 趋势拐头（trend_up/trend_down）：滚动线性回归斜率方向翻转；
2. 加速异动（accel_up/accel_down）：当日收益相对前窗收益分布的 z-score 超阈值；
3. 回撤/反弹确认（drawdown/rally）：自局部极值的累计变动超阈值（zigzag）；
4. 量能异常（volume_spike）：成交量超前窗均量数倍——邻近变化点升级 severity，
   孤立出现则作为独立的辅助信号；
5. 邻近合并：同类型变化点在窗口内合并，保留触发强度最高者。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from finance_agent.provenance import Evidence, EvidenceLog

ChangepointKind = Literal[
    "trend_up", "trend_down", "accel_up", "accel_down", "drawdown", "rally", "volume_spike"
]


@dataclass(frozen=True)
class ChangepointParams:
    """全部阈值集中于此，可配置、入单测。"""

    trend_window: int = 20          # 滚动回归窗口（交易日）
    trend_min_slope: float = 0.001  # 相对斜率下限（日均变动占价格比例），滤掉横盘噪声
    zscore_window: int = 21         # 收益 z-score 的统计窗口
    zscore_threshold: float = 2.0
    swing_threshold: float = 0.15   # 回撤/反弹确认幅度
    volume_window: int = 60         # 均量窗口
    volume_multiple: float = 3.0
    merge_window_days: int = 10     # 邻近合并/量能升级的日历日窗口


class Changepoint(BaseModel):
    date: str
    kind: ChangepointKind
    rule: str                     # 人话版触发说明（进产物标注与溯源）
    severity: int                 # 1 弱 / 2 中 / 3 强
    window: tuple[str, str]       # 触发数据窗口（起止日期，回指原始行）
    details: dict[str, float]     # 触发时的关键中间值


@dataclass
class DetectionResult:
    points: list[Changepoint]
    evidence: Evidence | None


def _rolling_slope(values: np.ndarray, window: int) -> np.ndarray:
    """窗口内最小二乘斜率（每日价格变化量）。前 window-1 位为 NaN。"""
    x = np.arange(window, dtype=float)
    x -= x.mean()
    denom = float((x**2).sum())
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        y = values[i - window + 1 : i + 1]
        out[i] = float(((y - y.mean()) * x).sum()) / denom
    return out


def _detect_trend_turns(df: pd.DataFrame, p: ChangepointParams) -> list[Changepoint]:
    closes = df["close"].to_numpy(dtype=float)
    dates = df["date"].tolist()
    rel_slopes = _rolling_slope(closes, p.trend_window) / closes
    points: list[Changepoint] = []
    last_sign = 0
    for i, rel in enumerate(rel_slopes):
        if np.isnan(rel) or abs(rel) < p.trend_min_slope:
            continue
        sign = 1 if rel > 0 else -1
        if last_sign and sign != last_sign:
            kind: ChangepointKind = "trend_up" if sign > 0 else "trend_down"
            points.append(
                Changepoint(
                    date=dates[i],
                    kind=kind,
                    rule=(
                        f"{p.trend_window}日回归斜率转{'正' if sign > 0 else '负'}"
                        f"（相对斜率 {rel:+.4f}/日）"
                    ),
                    severity=1,
                    window=(dates[i - p.trend_window + 1], dates[i]),
                    details={"relative_slope": round(float(rel), 6)},
                )
            )
        last_sign = sign
    return points


def _detect_accelerations(df: pd.DataFrame, p: ChangepointParams) -> list[Changepoint]:
    closes = df["close"]
    dates = df["date"].tolist()
    returns = closes.pct_change()
    mean = returns.rolling(p.zscore_window, min_periods=p.zscore_window).mean().shift(1)
    std = returns.rolling(p.zscore_window, min_periods=p.zscore_window).std().shift(1)
    points: list[Changepoint] = []
    for i in range(len(df)):
        sd = std.iloc[i]
        if pd.isna(sd) or sd < 1e-12:
            continue
        z = float((returns.iloc[i] - mean.iloc[i]) / sd)
        if abs(z) < p.zscore_threshold:
            continue
        kind: ChangepointKind = "accel_up" if z > 0 else "accel_down"
        points.append(
            Changepoint(
                date=dates[i],
                kind=kind,
                rule=(
                    f"单日收益 {returns.iloc[i]:+.2%}，为前{p.zscore_window}日分布的 "
                    f"{z:+.1f}σ"
                ),
                severity=2,
                window=(dates[max(0, i - p.zscore_window)], dates[i]),
                details={"zscore": round(z, 2), "return": round(float(returns.iloc[i]), 4)},
            )
        )
    return points


def _detect_swings(df: pd.DataFrame, p: ChangepointParams) -> list[Changepoint]:
    closes = df["close"].to_numpy(dtype=float)
    dates = df["date"].tolist()
    points: list[Changepoint] = []
    peak = trough = closes[0]
    peak_i = trough_i = 0
    direction: str | None = None
    for i, close in enumerate(closes):
        if close > peak:
            peak, peak_i = close, i
        if close < trough:
            trough, trough_i = close, i
        drop = close / peak - 1
        rise = close / trough - 1
        if direction != "down" and drop <= -p.swing_threshold:
            points.append(
                Changepoint(
                    date=dates[i],
                    kind="drawdown",
                    rule=f"自 {dates[peak_i]} 高点回撤 {drop:.1%}（确认级）",
                    severity=3,
                    window=(dates[peak_i], dates[i]),
                    details={"drawdown": round(float(drop), 4), "peak": round(float(peak), 4)},
                )
            )
            direction = "down"
            trough, trough_i = close, i
        elif direction != "up" and rise >= p.swing_threshold:
            points.append(
                Changepoint(
                    date=dates[i],
                    kind="rally",
                    rule=f"自 {dates[trough_i]} 低点反弹 {rise:+.1%}（确认级）",
                    severity=3,
                    window=(dates[trough_i], dates[i]),
                    details={"rally": round(float(rise), 4), "trough": round(float(trough), 4)},
                )
            )
            direction = "up"
            peak, peak_i = close, i
    return points


def _volume_anomalies(df: pd.DataFrame, p: ChangepointParams) -> dict[str, float]:
    """量能异常日 → 倍数。均量窗口不足时不产生信号（min_periods 保守取 1/3 窗口）。"""
    volume = df["volume"]
    baseline = volume.rolling(
        p.volume_window, min_periods=max(10, p.volume_window // 3)
    ).mean().shift(1)
    ratio = volume / baseline
    return {
        df["date"].iloc[i]: round(float(ratio.iloc[i]), 2)
        for i in range(len(df))
        if not pd.isna(ratio.iloc[i]) and ratio.iloc[i] >= p.volume_multiple
    }


def _days_between(a: str, b: str) -> int:
    return abs((datetime.strptime(a, "%Y-%m-%d") - datetime.strptime(b, "%Y-%m-%d")).days)


def _apply_volume_signals(
    points: list[Changepoint], anomalies: dict[str, float], p: ChangepointParams
) -> list[Changepoint]:
    """量能异常邻近变化点则升级其 severity；孤立异常独立成点。"""
    used: set[str] = set()
    upgraded: list[Changepoint] = []
    for point in points:
        near = {
            day: mult
            for day, mult in anomalies.items()
            if _days_between(day, point.date) <= p.merge_window_days
        }
        if near:
            used.update(near)
            best = max(near.values())
            point = point.model_copy(
                update={
                    "severity": min(3, point.severity + 1),
                    "details": {**point.details, "volume_ratio": best},
                    "rule": f"{point.rule}；伴随量能异常（{best:.1f}×均量）",
                }
            )
        upgraded.append(point)
    for day, mult in anomalies.items():
        if day in used:
            continue
        upgraded.append(
            Changepoint(
                date=day,
                kind="volume_spike",
                rule=f"成交量达前{p.volume_window}日均量的 {mult:.1f} 倍",
                severity=1,
                window=(day, day),
                details={"volume_ratio": mult},
            )
        )
    return upgraded


def _merge_nearby(points: list[Changepoint], p: ChangepointParams) -> list[Changepoint]:
    """同类型且日期相近的点合并，保留 severity 最高者（并列取最早）。"""
    merged: list[Changepoint] = []
    for point in sorted(points, key=lambda cp: (cp.kind, cp.date)):
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.kind == point.kind
            and _days_between(last.date, point.date) <= p.merge_window_days
        ):
            if point.severity > last.severity:
                merged[-1] = point
            continue
        merged.append(point)
    return sorted(merged, key=lambda cp: cp.date)


def detect_changepoints(
    df: pd.DataFrame,
    params: ChangepointParams | None = None,
    *,
    evidence_log: EvidenceLog | None = None,
    source_evidence_id: str | None = None,
) -> DetectionResult:
    """对清洗后的 OHLCV 日线执行全部规则。df 需含 date/close/volume 列且按日期升序。"""
    p = params or ChangepointParams()
    if len(df) < p.trend_window + 1:
        raise ValueError(f"数据不足：至少需要 {p.trend_window + 1} 行，实际 {len(df)}")
    points = (
        _detect_trend_turns(df, p)
        + _detect_accelerations(df, p)
        + _detect_swings(df, p)
    )
    points = _apply_volume_signals(points, _volume_anomalies(df, p), p)
    points = _merge_nearby(points, p)
    evidence = None
    if evidence_log is not None:
        evidence = evidence_log.record(
            "computation",
            source_url=f"evidence:{source_evidence_id or 'unknown'}",
            query={"params": asdict(p), "rows": len(df)},
            excerpt=f"{len(points)} 个变化点：" + "；".join(
                f"{cp.date} {cp.kind}(s{cp.severity})" for cp in points[:8]
            ),
        )
    return DetectionResult(points=points, evidence=evidence)
