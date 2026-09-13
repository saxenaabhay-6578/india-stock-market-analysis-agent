from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from config.settings import MARKET_DATA_BACKOFF_SECONDS, MARKET_DATA_MAX_RETRIES

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

_COLUMN_RENAME = {
    "Date": "date", "Open": "open", "High": "high", "Low": "low",
    "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
}

INTRADAY_REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

_INTRADAY_COLUMN_RENAME = {
    "Datetime": "timestamp", "Date": "timestamp", "Open": "open", "High": "high",
    "Low": "low", "Close": "close", "Volume": "volume",
}


class MarketDataProvider(ABC):
    @abstractmethod
    def get_history(self, symbol: str, start: date, end: date) -> pd.DataFrame | None: ...

    @abstractmethod
    def get_latest_close(self, symbol: str) -> tuple[date, float] | None: ...

    @abstractmethod
    def get_intraday_history(self, symbol: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame | None: ...

    @abstractmethod
    def get_latest_intraday_price(self, symbol: str, as_of: datetime) -> tuple[datetime, float] | None: ...


class YFinanceProvider(MarketDataProvider):
    def __init__(
        self, max_retries: int = MARKET_DATA_MAX_RETRIES, backoff_seconds: float = MARKET_DATA_BACKOFF_SECONDS,
    ):
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def get_history(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        for attempt in range(self._max_retries + 1):
            try:
                raw = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
                if raw is None or raw.empty:
                    logger.warning("No data returned for %s", symbol)
                    return None
                df = raw.reset_index().rename(columns=_COLUMN_RENAME)
                df["date"] = pd.to_datetime(df["date"]).dt.date
                missing = set(REQUIRED_COLUMNS) - set(df.columns)
                if missing:
                    logger.warning("Symbol %s missing columns %s, skipping", symbol, missing)
                    return None
                return df[REQUIRED_COLUMNS]
            except Exception as exc:
                logger.warning("Attempt %d failed for %s: %s", attempt + 1, symbol, exc)
                if attempt < self._max_retries:
                    time.sleep(self._backoff_seconds * (attempt + 1))
        logger.error("All retries exhausted for %s, skipping", symbol)
        return None

    def get_latest_close(self, symbol: str) -> tuple[date, float] | None:
        history = self.get_history(symbol, start=date.today() - timedelta(days=10), end=date.today())
        if history is None or history.empty:
            return None
        last_row = history.iloc[-1]
        return last_row["date"], float(last_row["close"])

    def get_intraday_history(self, symbol: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame | None:
        """Fetch intraday OHLCV data. Parameters start/end must be timezone-aware (e.g., Asia/Kolkata for NSE data)."""
        for attempt in range(self._max_retries + 1):
            try:
                raw = yf.Ticker(symbol).history(start=start, end=end, interval=interval, auto_adjust=False)
                if raw is None or raw.empty:
                    logger.warning("No intraday data returned for %s", symbol)
                    return None
                df = raw.reset_index().rename(columns=_INTRADAY_COLUMN_RENAME)
                missing = set(INTRADAY_REQUIRED_COLUMNS) - set(df.columns)
                if missing:
                    logger.warning("Symbol %s missing intraday columns %s, skipping", symbol, missing)
                    return None
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                return df[INTRADAY_REQUIRED_COLUMNS]
            except Exception as exc:
                logger.warning("Intraday attempt %d failed for %s: %s", attempt + 1, symbol, exc)
                if attempt < self._max_retries:
                    time.sleep(self._backoff_seconds * (attempt + 1))
        logger.error("All intraday retries exhausted for %s, skipping", symbol)
        return None

    def get_latest_intraday_price(self, symbol: str, as_of: datetime) -> tuple[datetime, float] | None:
        """Get the latest intraday price at or before as_of. Parameter as_of must be timezone-aware (e.g., Asia/Kolkata for NSE data)."""
        start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        history = self.get_intraday_history(symbol, start=start, end=as_of + timedelta(minutes=1), interval="1m")
        if history is None or history.empty:
            return None
        filtered = history[history["timestamp"] <= as_of]
        if filtered.empty:
            return None
        last_row = filtered.iloc[-1]
        return last_row["timestamp"].to_pydatetime(), float(last_row["close"])
