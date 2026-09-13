from src.analysis.technical_score import compute_technical_score


def test_score_is_within_bounds_for_extreme_bullish_inputs():
    score = compute_technical_score(
        close=120.0, sma20=100.0, sma50=90.0, rsi_value=10.0, macd_value=5.0,
        macd_signal_value=1.0, bb_upper=110.0, bb_lower=90.0, momentum=20.0,
        volume_trend_value=50.0,
    )
    assert -100.0 <= score <= 100.0
    assert score > 0


def test_score_is_within_bounds_for_extreme_bearish_inputs():
    score = compute_technical_score(
        close=80.0, sma20=100.0, sma50=110.0, rsi_value=90.0, macd_value=-5.0,
        macd_signal_value=-1.0, bb_upper=110.0, bb_lower=90.0, momentum=-20.0,
        volume_trend_value=-50.0,
    )
    assert -100.0 <= score <= 100.0
    assert score < 0


def test_score_handles_zero_band_width_without_error():
    score = compute_technical_score(
        close=100.0, sma20=100.0, sma50=None, rsi_value=50.0, macd_value=0.0,
        macd_signal_value=0.0, bb_upper=100.0, bb_lower=100.0, momentum=0.0,
        volume_trend_value=0.0,
    )
    assert -100.0 <= score <= 100.0
