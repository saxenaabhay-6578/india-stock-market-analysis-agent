from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from config.settings import MARKET_DATA_BACKOFF_SECONDS, MARKET_DATA_MAX_RETRIES

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

_COLUMN_RENAME = {
    "Date": "date", "Open": "open", "High": "high", "Low": "low",
    "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
}


class MarketDataProvider(ABC):
    @abstractmethod
    def get_history(self, symbol: str, start: date, end: date) -> pd.DataFrame | None: ...

    @abstractmethod
    def get_latest_close(self, symbol: str) -> tuple[date, float] | None: ...


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
