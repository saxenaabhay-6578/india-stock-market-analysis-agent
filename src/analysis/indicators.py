from __future__ import annotations

import pandas as pd

from src.analysis.technical_score import compute_technical_score


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    result = result.mask(avg_loss == 0, 100.0)
    result = result.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    return result


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, window)
    std = series.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def support_resistance(df: pd.DataFrame, window: int = 20) -> tuple[float, float]:
    recent = df.tail(window)
    return float(recent["low"].min()), float(recent["high"].max())


def rolling_volatility(series: pd.Series, window: int = 20) -> float:
    returns = series.pct_change()
    return float(returns.rolling(window=window).std().iloc[-1] * 100)


def momentum_roc(series: pd.Series, window: int = 10) -> float:
    if len(series) <= window:
        return 0.0
    return float((series.iloc[-1] - series.iloc[-window - 1]) / series.iloc[-window - 1] * 100)


def volume_trend(volume: pd.Series, window: int = 20) -> float:
    if len(volume) < window * 2:
        return 0.0
    recent_avg = volume.tail(window).mean()
    prior_avg = volume.tail(window * 2).head(window).mean()
    if prior_avg == 0:
        return 0.0
    return float((recent_avg - prior_avg) / prior_avg * 100)


def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    latest_close = float(close.iloc[-1])

    sma5 = float(sma(close, 5).iloc[-1])
    sma10 = float(sma(close, 10).iloc[-1])
    sma20 = float(sma(close, 20).iloc[-1])
    sma50 = float(sma(close, 50).iloc[-1]) if len(close) >= 50 else None

    rsi14 = float(rsi(close, 14).iloc[-1])
    macd_line, signal_line, histogram = macd(close)
    macd_value = float(macd_line.iloc[-1])
    macd_signal_value = float(signal_line.iloc[-1])
    macd_histogram = float(histogram.iloc[-1])

    bb_upper, bb_mid, bb_lower = bollinger_bands(close)
    bb_upper_value = float(bb_upper.iloc[-1])
    bb_mid_value = float(bb_mid.iloc[-1])
    bb_lower_value = float(bb_lower.iloc[-1])

    support, resistance = support_resistance(df)
    volatility = rolling_volatility(close)
    momentum = momentum_roc(close)
    vol_trend = volume_trend(df["volume"])

    trend_direction = "UP" if latest_close > sma20 else "DOWN"

    technical_score = compute_technical_score(
        latest_close, sma20, sma50, rsi14, macd_value, macd_signal_value,
        bb_upper_value, bb_lower_value, momentum, vol_trend,
    )

    return {
        "sma5": sma5, "sma10": sma10, "sma20": sma20, "sma50": sma50,
        "rsi14": rsi14, "macd": macd_value, "macd_signal": macd_signal_value,
        "macd_histogram": macd_histogram, "bb_upper": bb_upper_value,
        "bb_mid": bb_mid_value, "bb_lower": bb_lower_value,
        "support": support, "resistance": resistance,
        "volatility_percent": volatility, "momentum_roc_percent": momentum,
        "volume_trend_percent": vol_trend, "trend_direction": trend_direction,
        "technical_score": technical_score,
    }
