from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.analysis.indicators import (
    bollinger_bands, macd, momentum_roc, rolling_volatility, rsi, sma,
    support_resistance, volume_trend,
)
from src.analysis.technical_score import compute_technical_score


def select_bars_up_to_checkpoint(bars: pd.DataFrame, checkpoint) -> pd.DataFrame:
    checkpoint_ts = pd.Timestamp(checkpoint)
    timestamps = pd.to_datetime(bars["timestamp"])
    return bars[timestamps < checkpoint_ts].reset_index(drop=True)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_intraday_indicators(bars: pd.DataFrame) -> dict:
    close = bars["close"]
    latest_close = float(close.iloc[-1])

    sma5 = float(sma(close, 5).iloc[-1])
    sma10 = float(sma(close, 10).iloc[-1])
    sma20 = float(sma(close, 20).iloc[-1])
    sma50 = float(sma(close, 50).iloc[-1]) if len(close) >= 50 else None

    ema9 = float(ema(close, 9).iloc[-1])
    ema21 = float(ema(close, 21).iloc[-1])

    rsi14 = float(rsi(close, 14).iloc[-1])
    macd_line, signal_line, histogram = macd(close)
    macd_value = float(macd_line.iloc[-1])
    macd_signal_value = float(signal_line.iloc[-1])
    macd_histogram = float(histogram.iloc[-1])

    bb_upper, bb_mid, bb_lower = bollinger_bands(close)
    bb_upper_value = float(bb_upper.iloc[-1])
    bb_mid_value = float(bb_mid.iloc[-1])
    bb_lower_value = float(bb_lower.iloc[-1])

    support, resistance = support_resistance(bars, window=20)
    volatility = rolling_volatility(close, window=20)
    momentum = momentum_roc(close, window=10)
    vol_trend = volume_trend(bars["volume"], window=20)

    trend_direction = "UP" if latest_close > sma20 else "DOWN"

    technical_score = compute_technical_score(
        latest_close, sma20, sma50, rsi14, macd_value, macd_signal_value,
        bb_upper_value, bb_lower_value, momentum, vol_trend,
    )

    return {
        "sma5": sma5, "sma10": sma10, "sma20": sma20, "sma50": sma50,
        "ema9": ema9, "ema21": ema21,
        "rsi14": rsi14, "macd": macd_value, "macd_signal": macd_signal_value,
        "macd_histogram": macd_histogram, "bb_upper": bb_upper_value,
        "bb_mid": bb_mid_value, "bb_lower": bb_lower_value,
        "support": support, "resistance": resistance,
        "volatility_percent": volatility, "momentum_roc_percent": momentum,
        "volume_trend_percent": vol_trend, "trend_direction": trend_direction,
        "technical_score": technical_score,
    }
