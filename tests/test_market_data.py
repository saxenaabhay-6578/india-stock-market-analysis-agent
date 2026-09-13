from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from src.providers.market_data import YFinanceProvider


class FakeHistoryTicker:
    def __init__(self, frame: pd.DataFrame | None = None, raise_times: int = 0):
        self._frame = frame
        self._raise_times = raise_times
        self.calls = 0

    def history(self, start, end, auto_adjust=False, **kwargs):
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


def _intraday_sample_frame():
    idx = pd.date_range("2026-09-14 09:15", periods=3, freq="60min", tz="Asia/Kolkata")
    idx.name = "Datetime"
    return pd.DataFrame(
        {
            "Open": [1450.0, 1452.0, 1458.0], "High": [1455.0, 1460.0, 1462.0],
            "Low": [1448.0, 1450.0, 1455.0], "Close": [1452.0, 1458.0, 1460.0],
            "Volume": [10000, 12000, 9000],
        },
        index=idx,
    )


def test_get_intraday_history_returns_normalized_dataframe(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_intraday_sample_frame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_intraday_history(
        "RELIANCE.NS", datetime(2026, 9, 14, 9, 0), datetime(2026, 9, 14, 12, 0), interval="60m",
    )

    assert list(result.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(result) == 3


def test_get_intraday_history_returns_none_for_empty_data(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=pd.DataFrame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_intraday_history(
        "BADSYM.NS", datetime(2026, 9, 14, 9, 0), datetime(2026, 9, 14, 12, 0), interval="60m",
    )
    assert result is None


def test_get_latest_intraday_price_filters_to_ticks_at_or_before_as_of(monkeypatch):
    idx = pd.date_range("2026-09-14 09:10", periods=10, freq="1min", tz="Asia/Kolkata")
    idx.name = "Datetime"
    frame = pd.DataFrame({
        "Open": [100.0] * 10, "High": [100.0] * 10, "Low": [100.0] * 10,
        "Close": [100.0 + i for i in range(10)], "Volume": [1000] * 10,
    }, index=idx)
    fake_ticker = FakeHistoryTicker(frame=frame)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    as_of = datetime(2026, 9, 14, 9, 15, tzinfo=idx.tz)
    result = provider.get_latest_intraday_price("RELIANCE.NS", as_of)

    assert result is not None
    ts, price = result
    assert ts <= as_of
    assert price == 105.0  # the 09:15 row is index 5 (09:10 + 5 min), Close = 100+5


def test_get_latest_intraday_price_returns_none_when_no_ticks_available(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=pd.DataFrame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_latest_intraday_price("RELIANCE.NS", datetime(2026, 9, 14, 9, 15))
    assert result is None


def test_get_latest_intraday_price_raises_on_naive_as_of(monkeypatch):
    idx = pd.date_range("2026-09-14 09:10", periods=10, freq="1min", tz="Asia/Kolkata")
    idx.name = "Datetime"
    frame = pd.DataFrame({
        "Open": [100.0] * 10, "High": [100.0] * 10, "Low": [100.0] * 10,
        "Close": [100.0 + i for i in range(10)], "Volume": [1000] * 10,
    }, index=idx)
    fake_ticker = FakeHistoryTicker(frame=frame)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    naive_as_of = datetime(2026, 9, 14, 9, 15)  # No tzinfo
    with pytest.raises(TypeError):
        provider.get_latest_intraday_price("RELIANCE.NS", naive_as_of)
