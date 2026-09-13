from datetime import date

import pandas as pd
import pytest

from src.providers.market_data import YFinanceProvider


class FakeHistoryTicker:
    def __init__(self, frame: pd.DataFrame | None = None, raise_times: int = 0):
        self._frame = frame
        self._raise_times = raise_times
        self.calls = 0

    def history(self, start, end, auto_adjust=False):
        self.calls += 1
        if self.calls <= self._raise_times:
            raise RuntimeError("transient network error")
        return self._frame


def _sample_frame():
    idx = pd.date_range("2026-08-01", periods=3, freq="B")
    idx.name = "Date"
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Adj Close": [100.5, 101.5, 102.5],
            "Volume": [1000, 1100, 1200],
        },
        index=idx,
    )


def test_get_history_returns_normalized_dataframe(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_sample_frame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_history("TCS.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert list(result.columns) == ["date", "open", "high", "low", "close", "adj_close", "volume"]
    assert len(result) == 3


def test_get_history_retries_then_succeeds(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_sample_frame(), raise_times=1)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)
    monkeypatch.setattr("src.providers.market_data.time.sleep", lambda seconds: None)

    provider = YFinanceProvider(max_retries=2, backoff_seconds=0)
    result = provider.get_history("TCS.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert fake_ticker.calls == 2
    assert result is not None


def test_get_history_returns_none_after_exhausting_retries(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_sample_frame(), raise_times=5)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)
    monkeypatch.setattr("src.providers.market_data.time.sleep", lambda seconds: None)

    provider = YFinanceProvider(max_retries=2, backoff_seconds=0)
    result = provider.get_history("TCS.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert result is None
    assert fake_ticker.calls == 3  # initial + 2 retries


def test_get_history_returns_none_for_empty_data(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=pd.DataFrame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_history("BADSYM.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert result is None


def test_default_retry_settings_come_from_config():
    from config.settings import MARKET_DATA_BACKOFF_SECONDS, MARKET_DATA_MAX_RETRIES

    provider = YFinanceProvider()
    assert provider._max_retries == MARKET_DATA_MAX_RETRIES
    assert provider._backoff_seconds == MARKET_DATA_BACKOFF_SECONDS
