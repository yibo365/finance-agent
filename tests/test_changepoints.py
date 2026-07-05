"""拐点检测单测：用确定性合成序列逐条验证规则触发与合并逻辑。"""

from datetime import date, timedelta

import pandas as pd
import pytest

from finance_agent.provenance import EvidenceLog
from finance_agent.tools.changepoints import ChangepointParams, detect_changepoints


def make_df(closes, volumes=None, start=date(2024, 1, 1)) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "date": [(start + timedelta(days=i)).isoformat() for i in range(len(closes))],
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes if volumes is not None else [1_000_000] * len(closes),
        }
    )


def alternating(n, base=100.0, amp=0.002):
    """±amp 交替的小幅震荡序列：斜率与收益分布近零但非退化（std>0）。"""
    closes = [base]
    for i in range(1, n):
        closes.append(closes[-1] * (1 + amp * (1 if i % 2 else -1)))
    return closes


def kinds(points):
    return [cp.kind for cp in points]


def test_trend_turn_detected_after_peak():
    closes = [100 + i for i in range(60)] + [159 - (i + 1) for i in range(60)]
    df = make_df(closes)
    points = detect_changepoints(df).points
    downs = [cp for cp in points if cp.kind == "trend_down"]
    assert len(downs) == 1
    # 拐头确认发生在峰值（第60天）之后、但不应晚太多
    assert df["date"].iloc[62] <= downs[0].date <= df["date"].iloc[85]
    assert "trend_up" not in kinds(points)  # 起始上升段只建立基准方向，不算事件
    assert downs[0].details["relative_slope"] < 0


def test_acceleration_zscore_triggers_on_spike_day():
    closes = alternating(40)
    closes.append(closes[-1] * 1.10)  # 第40个索引：单日 +10%
    closes.extend(alternating(10, base=closes[-1])[1:])
    df = make_df(closes)
    points = detect_changepoints(df).points
    accels = [cp for cp in points if cp.kind == "accel_up"]
    assert len(accels) == 1
    assert accels[0].date == df["date"].iloc[40]
    assert accels[0].severity == 2  # 无量能异常，不升级
    assert accels[0].details["zscore"] > 2


def test_drawdown_then_rally_confirmations():
    closes = (
        [100 + i for i in range(61)]            # 100 → 160
        + [159 - i for i in range(40)]          # → 120
        + [121 + i for i in range(45)]          # → 165
    )
    df = make_df(closes)
    points = detect_changepoints(df).points
    drawdowns = [cp for cp in points if cp.kind == "drawdown"]
    rallies = [cp for cp in points if cp.kind == "rally"]
    # 两次 rally：起始上升段本身构成一次反弹确认（自序列起点低点），回撤后的修复是第二次
    assert len(drawdowns) == 1 and len(rallies) == 2
    assert drawdowns[0].severity == 3
    assert drawdowns[0].window[0] == df["date"].iloc[60]  # 回指峰值日
    assert drawdowns[0].details["drawdown"] <= -0.15
    recovery = rallies[-1]
    assert recovery.date > drawdowns[0].date
    assert recovery.window[0] == df["date"].iloc[100]  # 回指谷底日
    assert recovery.details["rally"] >= 0.15


def test_volume_anomaly_upgrades_nearby_changepoint():
    closes = alternating(40)
    closes.append(closes[-1] * 1.10)
    closes.extend(alternating(10, base=closes[-1])[1:])
    volumes = [1_000_000] * len(closes)
    volumes[40] = 5_000_000  # 与加速日同日放量
    df = make_df(closes, volumes=volumes)
    points = detect_changepoints(df).points
    accels = [cp for cp in points if cp.kind == "accel_up"]
    assert len(accels) == 1
    assert accels[0].severity == 3  # 2 + 量能升级
    assert accels[0].details["volume_ratio"] >= 3
    assert "volume_spike" not in kinds(points)  # 已被吸收，不独立成点


def test_isolated_volume_spike_becomes_standalone_signal():
    closes = alternating(60)
    volumes = [1_000_000] * len(closes)
    volumes[30] = 4_000_000
    df = make_df(closes, volumes=volumes)
    points = detect_changepoints(df).points
    assert kinds(points) == ["volume_spike"]
    assert points[0].date == df["date"].iloc[30]
    assert points[0].severity == 1


def test_nearby_same_kind_points_merged():
    closes = alternating(40)
    closes.append(closes[-1] * 1.10)          # 索引40：+10%
    closes.append(closes[-1] * 0.998)
    closes.append(closes[-1] * 1.002)
    closes.append(closes[-1] * 1.10)          # 索引43：再 +10%
    closes.extend(alternating(10, base=closes[-1])[1:])
    df = make_df(closes)
    points = detect_changepoints(df).points
    accels = [cp for cp in points if cp.kind == "accel_up"]
    assert len(accels) == 1                    # 3 天内同类合并
    assert accels[0].date == df["date"].iloc[40]  # 严重度并列时保留最早


def test_insufficient_data_raises():
    with pytest.raises(ValueError, match="数据不足"):
        detect_changepoints(make_df(alternating(10)))


def test_detection_records_computation_evidence():
    log = EvidenceLog("t")
    df = make_df(alternating(60))
    result = detect_changepoints(df, evidence_log=log, source_evidence_id="ev-t-1")
    assert result.evidence is not None
    assert result.evidence.kind == "computation"
    assert result.evidence.source_url == "evidence:ev-t-1"
    assert result.evidence.query["rows"] == 60


def test_thresholds_are_configurable():
    closes = alternating(40)
    closes.append(closes[-1] * 1.05)  # +5%：默认阈值下 z 远超 2，但收紧后仍触发；
    df = make_df(closes)
    strict = ChangepointParams(zscore_threshold=50.0)  # 极端阈值 → 不触发
    assert "accel_up" not in kinds(detect_changepoints(df, strict).points)
    assert "accel_up" in kinds(detect_changepoints(df).points)
