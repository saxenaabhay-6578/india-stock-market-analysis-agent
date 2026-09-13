from datetime import datetime, timezone

import pandas as pd
import pytest

from src.analysis.intraday_indicators import compute_intraday_indicators, ema, select_bars_up_to_checkpoint


def _bars(timestamps: list[str], closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "close": closes, "volume": [1000 + i for i in range(n)],
    })


def test_select_bars_up_to_checkpoint_excludes_bar_starting_at_checkpoint():
    bars = _bars(
        ["2026-09-15T09:15:00+05:30", "2026-09-15T10:15:00+05:30"],
        [100.0, 999999.0],  # 10:15 bar has a deliberately distinctive value
    )
    checkpoint = pd.Timestamp("2026-09-15T10:15:00+05:30")
    result = select_bars_up_to_checkpoint(bars, checkpoint)
    assert len(result) == 1
    assert 999999.0 not in result["close"].values


def test_select_bars_up_to_checkpoint_includes_bar_that_just_closed():
    bars = _bars(
        ["2026-09-15T09:15:00+05:30", "2026-09-15T10:15:00+05:30"],
        [100.0, 105.0],
    )
    checkpoint = pd.Timestamp("2026-09-15T10:15:00+05:30")
    result = select_bars_up_to_checkpoint(bars, checkpoint)
    assert list(result["close"]) == [100.0]  # only the 09:15 bar, which closed at 10:15


def test_select_bars_up_to_checkpoint_at_market_open_returns_only_prior_days():
    bars = _bars(
        ["2026-09-14T14:15:00+05:30", "2026-09-15T09:15:00+05:30"],
        [200.0, 300.0],
    )
    checkpoint = pd.Timestamp("2026-09-15T09:15:00+05:30")
    result = select_bars_up_to_checkpoint(bars, checkpoint)
    assert list(result["close"]) == [200.0]


def test_ema_matches_manual_ewm():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(series, span=3)
    assert result.iloc[-1] == pytest.approx(series.ewm(span=3, adjust=False).mean().iloc[-1])


def test_compute_intraday_indicators_omits_sma50_with_insufficient_bars():
    closes = [100.0 + i * 0.5 for i in range(30)]
    timestamps = [f"2026-09-{10 + i // 7:02d}T{9 + i % 7:02d}:15:00+05:30" for i in range(30)]
    bars = _bars(timestamps, closes)
    result = compute_intraday_indicators(bars)
    assert result["sma50"] is None
    assert result["ema9"] is not None
    assert result["ema21"] is not None


def test_compute_intraday_indicators_technical_score_within_bounds():
    closes = [100.0 + i * 0.3 for i in range(60)]
    timestamps = [f"2026-09-{10 + i // 7:02d}T{9 + i % 7:02d}:15:00+05:30" for i in range(60)]
    bars = _bars(timestamps, closes)
    result = compute_intraday_indicators(bars)
    assert -100.0 <= result["technical_score"] <= 100.0
