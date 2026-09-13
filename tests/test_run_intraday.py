import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.run_intraday import run_intraday_pipeline
from src.storage.db import get_connection

IST = ZoneInfo("Asia/Kolkata")


def _hourly_frame(checkpoint: datetime, n_hours_back=60):
    timestamps = pd.date_range(end=checkpoint - timedelta(hours=1), periods=n_hours_back, freq="60min", tz=IST)
    closes = [1440.0 + i * 0.2 for i in range(n_hours_back)]
    return pd.DataFrame({
        "timestamp": timestamps, "open": closes, "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes], "close": closes, "volume": [1000 + i for i in range(n_hours_back)],
    })


class _FakeProvider:
    def __init__(self, hourly_frame, latest_tick=None, fail_symbols=None):
        self._hourly_frame = hourly_frame
        self._latest_tick = latest_tick
        self._fail_symbols = fail_symbols or set()

    def get_intraday_history(self, symbol, start, end, interval):
        if symbol in self._fail_symbols:
            return None
        return self._hourly_frame

    def get_latest_intraday_price(self, symbol, as_of):
        if symbol in self._fail_symbols:
            return None
        return self._latest_tick or (as_of, 1450.0)


def _valid_payload(current_price):
    return {
        "direction": "BULLISH", "current_price": current_price, "predicted_price": current_price * 1.01,
        "expected_move_percent": 1.0, "confidence": 65, "reasoning": "uptrend", "key_risks": ["macro risk"],
    }


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    input_tokens = 650
    output_tokens = 400


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        price_line = [line for line in prompt.splitlines() if line.startswith("Current price:")][0]
        current_price = float(price_line.split(":")[1].strip())
        return _FakeMessage(json.dumps(_valid_payload(current_price)))


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_dry_run_writes_nothing_to_db_or_reports(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    result = run_intraday_pipeline(
        ["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports", dry_run=True,
    )

    assert result["made"] == 1
    assert result["report_path"] is None
    assert conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM intraday_price_bars").fetchone()[0] == 0
    assert not (tmp_path / "reports").exists()
    conn.close()


def test_full_run_at_10_15_stores_prediction_and_writes_report(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    result = run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    assert result["made"] == 1
    assert result["skipped"] == 0
    assert result["report_path"].exists()
    assert conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0] == 1
    row = conn.execute("SELECT * FROM intraday_predictions").fetchone()
    assert row["prediction_timestamp"] == checkpoint.isoformat()
    conn.close()


def test_15_15_checkpoint_never_generates_a_new_prediction(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 15, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    result = run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    assert client.messages.calls == 0
    assert conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0] == 0
    conn.close()


def test_09_15_checkpoint_uses_latest_tick_not_hourly_bar_series(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint), latest_tick=(checkpoint, 1449.75))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    row = conn.execute("SELECT raw_current_price FROM intraday_predictions").fetchone()
    assert row["raw_current_price"] == 1449.75
    conn.close()


def test_symbol_with_no_market_data_is_skipped_without_stopping_others(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint), fail_symbols={"BADSYM.NS"})
    client = _FakeClient()

    result = run_intraday_pipeline(
        ["BADSYM.NS", "RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 1
    conn.close()


def test_duplicate_checkpoint_run_does_not_create_a_second_prediction_row(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")
    client.messages.calls = 0  # isolate the assertion below to calls made during the second run
    result = run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    count = conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0]
    assert count == 1
    assert result["made"] == 0  # the duplicate insert was ignored, not counted as a new prediction
    assert client.messages.calls == 0  # Claude was never called on the rerun - caught by the pre-check
    conn.close()


class _SparseHistoryProvider:
    """Returns a full 60-bar hourly history for most symbols, but a truncated
    (<20 bar) history for symbols listed in sparse_symbols -- simulating a
    recently-listed stock or a data gap that leaves too little history for
    reliable sma20/bollinger/volatility/momentum indicators."""

    def __init__(self, full_frame, sparse_symbols):
        self._full_frame = full_frame
        self._sparse_symbols = sparse_symbols

    def get_intraday_history(self, symbol, start, end, interval):
        if symbol in self._sparse_symbols:
            return self._full_frame.tail(10).reset_index(drop=True)
        return self._full_frame

    def get_latest_intraday_price(self, symbol, as_of):
        return (as_of, 1450.0)


def test_symbol_with_insufficient_bar_history_is_skipped_without_stopping_others(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _SparseHistoryProvider(_hourly_frame(checkpoint), sparse_symbols={"THINSYM.NS"})
    client = _FakeClient()

    result = run_intraday_pipeline(
        ["THINSYM.NS", "RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 1
    # THINSYM.NS must never reach the Claude call with NaN-poisoned indicators;
    # only RELIANCE.NS (sufficient history) should generate a request.
    assert client.messages.calls == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM intraday_predictions WHERE symbol = 'THINSYM.NS'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM intraday_predictions WHERE symbol = 'RELIANCE.NS'"
    ).fetchone()[0] == 1
    conn.close()


class _NaNVolumeProvider:
    """Returns a valid hourly history for most symbols, but injects a NaN
    volume into the most recent bar for symbols listed in nan_volume_symbols
    -- reproducing yfinance's known behavior of occasionally returning NaN
    volume for the most recent/incomplete intraday bar."""

    def __init__(self, good_frame, nan_volume_symbols):
        self._good_frame = good_frame
        self._nan_volume_symbols = nan_volume_symbols

    def get_intraday_history(self, symbol, start, end, interval):
        if symbol in self._nan_volume_symbols:
            frame = self._good_frame.copy()
            frame.loc[frame.index[-1], "volume"] = float("nan")
            return frame
        return self._good_frame

    def get_latest_intraday_price(self, symbol, as_of):
        return (as_of, 1450.0)


def test_symbol_with_nan_volume_bar_is_skipped_without_crashing_the_run(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _NaNVolumeProvider(_hourly_frame(checkpoint), nan_volume_symbols={"BADVOL.NS"})
    client = _FakeClient()

    # int(NaN) raises ValueError deep inside the per-symbol body (building the
    # cached price-bar rows / bar_snapshot). Per-symbol failure isolation must
    # hold for this unexpected exception too, not just the two anticipated
    # None/.empty data-missing cases -- so BADVOL.NS is skipped, not a crash
    # that kills the run for RELIANCE.NS as well.
    result = run_intraday_pipeline(
        ["BADVOL.NS", "RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports",
    )

    assert result["skipped"] >= 1
    assert result["made"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM intraday_predictions WHERE symbol = 'BADVOL.NS'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM intraday_predictions WHERE symbol = 'RELIANCE.NS'"
    ).fetchone()[0] == 1
    conn.close()


from src.run_intraday import find_missed_checkpoints


def test_find_missed_checkpoints_empty_on_first_run_of_the_day(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    assert find_missed_checkpoints(conn, checkpoint) == []
    conn.close()


def test_find_missed_checkpoints_detects_a_gap(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    provider = _FakeProvider(_hourly_frame(datetime(2026, 9, 15, 9, 15, tzinfo=IST)))
    client = _FakeClient()

    # simulate a successful 09:15 run, then jump straight to 12:15 (10:15 and 11:15 were missed)
    run_intraday_pipeline(["RELIANCE.NS"], datetime(2026, 9, 15, 9, 15, tzinfo=IST), conn, provider, client, output_dir=tmp_path / "reports")

    missed = find_missed_checkpoints(conn, datetime(2026, 9, 15, 12, 15, tzinfo=IST))
    assert missed == ["10:15", "11:15"]
    conn.close()


def test_09_15_checkpoint_caches_intraday_price_bars(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint), latest_tick=(checkpoint, 1449.75))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    count = conn.execute(
        "SELECT COUNT(*) FROM intraday_price_bars WHERE symbol = 'RELIANCE.NS' AND interval = '60m'"
    ).fetchone()[0]
    assert count > 0
    conn.close()


def test_recovery_run_never_fabricates_predictions_for_missed_checkpoints(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint_0915 = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    checkpoint_1215 = datetime(2026, 9, 15, 12, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint_1215))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint_0915, conn, provider, client, output_dir=tmp_path / "reports")
    run_intraday_pipeline(["RELIANCE.NS"], checkpoint_1215, conn, provider, client, output_dir=tmp_path / "reports")

    recorded_checkpoints = {
        row["prediction_timestamp"][11:16]
        for row in conn.execute("SELECT prediction_timestamp FROM intraday_predictions WHERE symbol = 'RELIANCE.NS'")
    }
    assert recorded_checkpoints == {"09:15", "12:15"}  # never 10:15 or 11:15
    conn.close()
