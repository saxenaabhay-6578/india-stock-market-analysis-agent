import json
from datetime import date

import pandas as pd
import pytest

from src.run_daily import run_pipeline
from src.storage.db import get_connection
from src.universe.trading_calendar import NseStaticHolidayCalendar


def _fake_history(n=60, start_price=1000.0):
    idx = pd.bdate_range("2026-06-01", periods=n)
    closes = [start_price + i for i in range(n)]
    return pd.DataFrame({
        "date": idx.date, "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes, "adj_close": closes,
        "volume": [1000 + i for i in range(n)],
    })


class _FakeProvider:
    def __init__(self, frame):
        self._frame = frame

    def get_history(self, symbol, start, end):
        return self._frame

    def get_latest_close(self, symbol):
        return date.today(), float(self._frame.iloc[-1]["close"])


def _valid_payload(current_price):
    horizon = {
        "direction": "BULLISH", "target_price": current_price * 1.01, "range_low": current_price * 0.99,
        "range_high": current_price * 1.02, "expected_move_percent": 1.0, "risk_level": current_price * 0.97,
        "confidence": 65, "reasoning": "uptrend", "key_risks": ["macro risk"],
    }
    return {"current_price": current_price, "horizons": {k: dict(horizon) for k in ["1d", "5d", "10d", "20d"]}}


class _FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"type": "text", "text": text})()]


class _FakeMessages:
    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        price_line = [line for line in prompt.splitlines() if line.startswith("Current price:")][0]
        current_price = float(price_line.split(":")[1].strip())
        return _FakeMessage(json.dumps(_valid_payload(current_price)))


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_dry_run_writes_nothing_to_db_or_reports(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    provider = _FakeProvider(_fake_history())
    client = _FakeClient()
    calendar = NseStaticHolidayCalendar()

    result = run_pipeline(
        ["TCS.NS"], date(2026, 9, 14), dry_run=True, conn=conn, provider=provider,
        client=client, calendar=calendar, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["report_path"] is None
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 0
    assert not (tmp_path / "reports").exists()
    conn.close()


def test_full_run_stores_predictions_and_writes_report(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    provider = _FakeProvider(_fake_history())
    client = _FakeClient()
    calendar = NseStaticHolidayCalendar()

    result = run_pipeline(
        ["TCS.NS"], date(2026, 9, 14), dry_run=False, conn=conn, provider=provider,
        client=client, calendar=calendar, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 0
    assert result["report_path"].exists()
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0] == 60
    assert conn.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 1
    conn.close()


def test_symbol_with_no_market_data_is_skipped_without_stopping_others(tmp_path):
    class _PartialProvider:
        def get_history(self, symbol, start, end):
            return None if symbol == "BADSYM.NS" else _fake_history()

        def get_latest_close(self, symbol):
            return date.today(), 1000.0

    conn = get_connection(tmp_path / "test.db")
    result = run_pipeline(
        ["BADSYM.NS", "TCS.NS"], date(2026, 9, 14), dry_run=False, conn=conn,
        provider=_PartialProvider(), client=_FakeClient(), calendar=NseStaticHolidayCalendar(),
        output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 1
    conn.close()


def test_symbol_with_claude_validation_failure_is_skipped_without_stopping_others(tmp_path):
    class _MixedValidityMessages:
        def create(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            stock_line = [line for line in prompt.splitlines() if line.startswith("Stock:")][0]
            symbol = stock_line.split(":")[1].strip()
            if symbol == "BADPRED.NS":
                return _FakeMessage("not valid json")
            price_line = [line for line in prompt.splitlines() if line.startswith("Current price:")][0]
            current_price = float(price_line.split(":")[1].strip())
            return _FakeMessage(json.dumps(_valid_payload(current_price)))

    class _MixedValidityClient:
        def __init__(self):
            self.messages = _MixedValidityMessages()

    conn = get_connection(tmp_path / "test.db")
    result = run_pipeline(
        ["BADPRED.NS", "TCS.NS"], date(2026, 9, 14), dry_run=False, conn=conn,
        provider=_FakeProvider(_fake_history()), client=_MixedValidityClient(),
        calendar=NseStaticHolidayCalendar(), output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 1
    conn.close()
