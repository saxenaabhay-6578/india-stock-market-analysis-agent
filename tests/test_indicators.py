import pandas as pd
import pytest

from src.analysis.indicators import compute_indicators, momentum_roc, rsi, sma


def _frame(closes: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    n = len(closes)
    volumes = volumes or [1000] * n
    idx = pd.bdate_range("2026-06-01", periods=n)
    return pd.DataFrame(
        {
            "date": idx.date,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "adj_close": closes,
            "volume": volumes,
        }
    )


def test_sma_matches_manual_average():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(series, 3)
    assert result.iloc[-1] == pytest.approx((3 + 4 + 5) / 3)


def test_rsi_is_100_for_strictly_increasing_series():
    series = pd.Series([float(i) for i in range(1, 30)])
    result = rsi(series, period=14)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_50_for_flat_series():
    series = pd.Series([100.0] * 30)
    result = rsi(series, period=14)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_momentum_roc_positive_for_rising_series():
    series = pd.Series([float(i) for i in range(1, 30)])
    assert momentum_roc(series, window=10) > 0


def test_compute_indicators_omits_sma50_with_insufficient_history():
    closes = [100.0 + i * 0.5 for i in range(40)]
    result = compute_indicators(_frame(closes))
    assert result["sma50"] is None
    assert result["sma20"] is not None


def test_compute_indicators_includes_sma50_with_enough_history():
    closes = [100.0 + i * 0.5 for i in range(60)]
    result = compute_indicators(_frame(closes))
    assert result["sma50"] is not None


def test_compute_indicators_technical_score_within_bounds():
    closes = [100.0 + i * 0.5 for i in range(60)]
    result = compute_indicators(_frame(closes))
    assert -100.0 <= result["technical_score"] <= 100.0


def test_compute_indicators_bullish_trend_yields_positive_score():
    rising = [100.0 + i * 1.5 for i in range(60)]
    result = compute_indicators(_frame(rising))
    assert result["technical_score"] > 0
    assert result["trend_direction"] == "UP"


def test_compute_indicators_bearish_trend_yields_negative_score():
    falling = [200.0 - i * 1.5 for i in range(60)]
    result = compute_indicators(_frame(falling))
    assert result["technical_score"] < 0
    assert result["trend_direction"] == "DOWN"
