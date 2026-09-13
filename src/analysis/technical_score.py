from __future__ import annotations

SCORE_WEIGHTS = {
    "trend": 0.30,
    "rsi": 0.20,
    "macd": 0.20,
    "bollinger": 0.15,
    "momentum": 0.10,
    "volume": 0.05,
}


def compute_technical_score(
    close: float, sma20: float, sma50: float | None, rsi_value: float,
    macd_value: float, macd_signal_value: float, bb_upper: float, bb_lower: float,
    momentum: float, volume_trend_value: float,
) -> float:
    if sma50 is not None and close > sma20 > sma50:
        trend_signal = 100.0
    elif sma50 is not None and close < sma20 < sma50:
        trend_signal = -100.0
    else:
        trend_signal = max(-100.0, min(100.0, 100.0 * (close - sma20) / sma20))

    rsi_signal = max(-100.0, min(100.0, (50.0 - rsi_value) * 2))
    macd_signal = 100.0 if macd_value > macd_signal_value else -100.0

    band_width = bb_upper - bb_lower
    if band_width > 0:
        position = (close - bb_lower) / band_width
        bollinger_signal = max(-100.0, min(100.0, (0.5 - position) * 200))
    else:
        bollinger_signal = 0.0

    momentum_signal = max(-100.0, min(100.0, momentum * 10))
    volume_signal = max(-100.0, min(100.0, volume_trend_value))

    score = (
        SCORE_WEIGHTS["trend"] * trend_signal
        + SCORE_WEIGHTS["rsi"] * rsi_signal
        + SCORE_WEIGHTS["macd"] * macd_signal
        + SCORE_WEIGHTS["bollinger"] * bollinger_signal
        + SCORE_WEIGHTS["momentum"] * momentum_signal
        + SCORE_WEIGHTS["volume"] * volume_signal
    )
    return round(score, 2)
