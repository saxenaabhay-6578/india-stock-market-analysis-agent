from datetime import date

import pandas as pd

from src.storage import db


def test_get_connection_creates_all_tables(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"universe_snapshots", "price_bars", "predictions", "accuracy_evaluations"} <= tables
    conn.close()


def test_get_connection_is_idempotent(tmp_path):
    path = tmp_path / "test.db"
    conn1 = db.get_connection(path)
    conn1.execute(
        "INSERT INTO universe_snapshots (snapshot_date, symbol, index_weight, rank, source, created_at) "
        "VALUES ('2026-01-01', 'TCS.NS', 3.9, 1, 'test', '2026-01-01T00:00:00')"
    )
    conn1.commit()
    conn1.close()

    conn2 = db.get_connection(path)
    row = conn2.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()
    assert row[0] == 1
    conn2.close()


def test_insert_universe_snapshot_rows_inserts_one_row_per_dict(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    rows = [
        {"snapshot_date": "2026-09-14", "symbol": "TCS.NS", "index_weight": 3.9, "rank": 1,
         "source": "test", "created_at": "2026-09-14T00:00:00"},
        {"snapshot_date": "2026-09-14", "symbol": "INFY.NS", "index_weight": 5.6, "rank": 2,
         "source": "test", "created_at": "2026-09-14T00:00:00"},
    ]
    db.insert_universe_snapshot_rows(conn, rows)
    count = conn.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0]
    assert count == 2
    conn.close()


def test_cache_price_bars_inserts_dataframe_rows(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    df = pd.DataFrame({
        "date": [date(2026, 9, 10), date(2026, 9, 11)],
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.5, 102.5],
        "adj_close": [101.5, 102.5],
        "volume": [1000000, 1100000],
    })
    db.cache_price_bars(conn, "TCS.NS", df, "yfinance")
    count = conn.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0]
    assert count == 2
    conn.close()


def test_cache_price_bars_idempotent_with_insert_or_ignore(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    df = pd.DataFrame({
        "date": [date(2026, 9, 10), date(2026, 9, 11)],
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.5, 102.5],
        "adj_close": [101.5, 102.5],
        "volume": [1000000, 1100000],
    })
    db.cache_price_bars(conn, "TCS.NS", df, "yfinance")
    db.cache_price_bars(conn, "TCS.NS", df, "yfinance")
    count = conn.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0]
    assert count == 2
    conn.close()


def test_get_price_bars_returns_all_rows_ordered_by_date(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    df = pd.DataFrame({
        "date": [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)],
        "open": [100.0, 101.0, 102.0],
        "high": [102.0, 103.0, 104.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.5, 102.5, 103.5],
        "adj_close": [101.5, 102.5, 103.5],
        "volume": [1000000, 1100000, 1200000],
    })
    db.cache_price_bars(conn, "TCS.NS", df, "yfinance")
    result = db.get_price_bars(conn, "TCS.NS")
    assert len(result) == 3
    assert list(result["date"]) == ["2026-09-10", "2026-09-11", "2026-09-12"]
    conn.close()


def test_get_price_bars_with_start_end_filters(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    df = pd.DataFrame({
        "date": [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)],
        "open": [100.0, 101.0, 102.0],
        "high": [102.0, 103.0, 104.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.5, 102.5, 103.5],
        "adj_close": [101.5, 102.5, 103.5],
        "volume": [1000000, 1100000, 1200000],
    })
    db.cache_price_bars(conn, "TCS.NS", df, "yfinance")
    result = db.get_price_bars(conn, "TCS.NS", start="2026-09-11", end="2026-09-12")
    assert len(result) == 2
    assert list(result["date"]) == ["2026-09-11", "2026-09-12"]
    conn.close()


def test_get_price_bars_with_start_exclusive(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    df = pd.DataFrame({
        "date": [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)],
        "open": [100.0, 101.0, 102.0],
        "high": [102.0, 103.0, 104.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.5, 102.5, 103.5],
        "adj_close": [101.5, 102.5, 103.5],
        "volume": [1000000, 1100000, 1200000],
    })
    db.cache_price_bars(conn, "TCS.NS", df, "yfinance")
    result = db.get_price_bars(conn, "TCS.NS", start="2026-09-10", start_exclusive=True)
    assert len(result) == 2
    assert list(result["date"]) == ["2026-09-11", "2026-09-12"]
    conn.close()


def test_insert_prediction_rows_inserts_and_rounds_trip(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    rows = [
        {
            "created_at": "2026-09-14T10:00:00",
            "prediction_date": "2026-09-14",
            "symbol": "TCS.NS",
            "horizon_days": 5,
            "target_evaluation_date": "2026-09-19",
            "current_price": 3500.0,
            "direction": "up",
            "target_price": 3600.0,
            "range_low": 3450.0,
            "range_high": 3700.0,
            "expected_move_percent": 2.86,
            "risk_level": 1.0,
            "confidence": 0.75,
            "reasoning": "Strong technical setup",
            "key_risks_json": "{}",
            "technical_score": 0.8,
            "indicators_json": "{}",
            "data_timestamp": "2026-09-14T09:30:00",
            "data_provider": "yfinance",
            "claude_model": "claude-sonnet-5",
            "prompt_version": "v1",
            "raw_claude_response": "test response",
        },
    ]
    db.insert_prediction_rows(conn, rows)
    result = conn.execute("SELECT symbol, direction, target_price FROM predictions").fetchone()
    assert result["symbol"] == "TCS.NS"
    assert result["direction"] == "up"
    assert result["target_price"] == 3600.0
    conn.close()


def test_insert_accuracy_evaluation_inserts_and_rounds_trip(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    # First insert a prediction to get an ID
    prediction_rows = [
        {
            "created_at": "2026-09-14T10:00:00",
            "prediction_date": "2026-09-14",
            "symbol": "TCS.NS",
            "horizon_days": 5,
            "target_evaluation_date": "2026-09-19",
            "current_price": 3500.0,
            "direction": "up",
            "target_price": 3600.0,
            "range_low": 3450.0,
            "range_high": 3700.0,
            "expected_move_percent": 2.86,
            "risk_level": 1.0,
            "confidence": 0.75,
            "reasoning": "Strong technical setup",
            "key_risks_json": "{}",
            "technical_score": 0.8,
            "indicators_json": "{}",
            "data_timestamp": "2026-09-14T09:30:00",
            "data_provider": "yfinance",
            "claude_model": "claude-sonnet-5",
            "prompt_version": "v1",
            "raw_claude_response": "test response",
        },
    ]
    db.insert_prediction_rows(conn, prediction_rows)
    prediction_id = conn.execute("SELECT id FROM predictions LIMIT 1").fetchone()[0]

    evaluation = {
        "prediction_id": prediction_id,
        "evaluated_at": "2026-09-19T15:00:00",
        "evaluation_date": "2026-09-19",
        "actual_close": 3620.0,
        "actual_high": 3650.0,
        "actual_low": 3580.0,
        "window_high": 3650.0,
        "window_low": 3500.0,
        "direction_correct": 1,
        "target_hit": 1,
        "within_range": 1,
        "prediction_error": 20.0,
        "abs_error": 20.0,
        "pct_error": 0.556,
        "return_after_prediction_percent": 3.43,
        "max_favorable_excursion": 50.0,
        "max_adverse_excursion": -20.0,
    }
    db.insert_accuracy_evaluation(conn, evaluation)
    result = conn.execute(
        "SELECT actual_close, direction_correct, target_hit FROM accuracy_evaluations"
    ).fetchone()
    assert result["actual_close"] == 3620.0
    assert result["direction_correct"] == 1
    assert result["target_hit"] == 1
    conn.close()


def test_insert_intraday_price_bar_rows_and_idempotent_reinsert(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    rows = [
        {"symbol": "RELIANCE.NS", "timestamp": "2026-09-14T09:15:00+05:30", "interval": "60m",
         "open": 1450.0, "high": 1455.0, "low": 1448.0, "close": 1452.0, "volume": 10000,
         "source": "yfinance", "fetched_at": "2026-09-14T09:27:00+00:00"},
        {"symbol": "RELIANCE.NS", "timestamp": "2026-09-14T10:15:00+05:30", "interval": "60m",
         "open": 1452.0, "high": 1460.0, "low": 1450.0, "close": 1458.0, "volume": 12000,
         "source": "yfinance", "fetched_at": "2026-09-14T10:27:00+00:00"},
    ]
    db.insert_intraday_price_bar_rows(conn, rows)
    db.insert_intraday_price_bar_rows(conn, rows)  # duplicate attempt
    count = conn.execute("SELECT COUNT(*) FROM intraday_price_bars").fetchone()[0]
    assert count == 2

    fetched = db.get_intraday_price_bars(conn, "RELIANCE.NS", "60m")
    assert len(fetched) == 2
    assert list(fetched["timestamp"]) == ["2026-09-14T09:15:00+05:30", "2026-09-14T10:15:00+05:30"]
    conn.close()


def test_get_intraday_price_bars_start_exclusive(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    rows = [
        {"symbol": "TCS.NS", "timestamp": f"2026-09-14T{h:02d}:15:00+05:30", "interval": "60m",
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
         "source": "yfinance", "fetched_at": "2026-09-14T00:00:00+00:00"}
        for h in [9, 10, 11]
    ]
    db.insert_intraday_price_bar_rows(conn, rows)
    result = db.get_intraday_price_bars(
        conn, "TCS.NS", "60m", start="2026-09-14T09:15:00+05:30", start_exclusive=True,
    )
    assert list(result["timestamp"]) == ["2026-09-14T10:15:00+05:30", "2026-09-14T11:15:00+05:30"]
    conn.close()


def _sample_intraday_prediction_row(**overrides):
    row = dict(
        created_at="2026-09-14T09:27:00+00:00", symbol="RELIANCE.NS",
        prediction_timestamp="2026-09-14T09:15:00+05:30", evaluation_timestamp="2026-09-14T10:15:00+05:30",
        prediction_type="next_hour", raw_current_price=1450.0,
        open=1450.0, high=1450.0, low=1450.0, close=1450.0, volume=0,
        direction="BULLISH", predicted_price=1465.0, expected_move_percent=1.03, confidence=65.0,
        reasoning="uptrend", key_risks_json="[\"macro risk\"]", technical_score=42.5,
        indicators_json="{}", data_provider="yfinance", interval="60m",
        claude_model="claude-sonnet-5", prompt_version="intraday-v1", raw_claude_response="{}",
        input_tokens=650, output_tokens=400,
    )
    row.update(overrides)
    return row


def test_insert_intraday_prediction_row_returns_rowcount_and_enforces_uniqueness(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    row = _sample_intraday_prediction_row()
    inserted = db.insert_intraday_prediction_row(conn, row)
    assert inserted == 1
    duplicate_attempt = db.insert_intraday_prediction_row(conn, row)
    assert duplicate_attempt == 0
    count = conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0]
    assert count == 1
    conn.close()


def test_insert_intraday_accuracy_evaluation_returns_rowcount_and_enforces_uniqueness(tmp_path):
    conn = db.get_connection(tmp_path / "test.db")
    db.insert_intraday_prediction_row(conn, _sample_intraday_prediction_row())
    prediction_id = conn.execute("SELECT id FROM intraday_predictions").fetchone()[0]
    evaluation = dict(
        prediction_id=prediction_id, evaluated_at="2026-09-14T10:27:00+00:00",
        evaluation_timestamp="2026-09-14T10:15:00+05:30", actual_price=1458.0, predicted_price=1465.0,
        abs_error=7.0, pct_error=0.48, direction_correct=1, target_hit=0,
    )
    first = db.insert_intraday_accuracy_evaluation(conn, evaluation)
    assert first == 1
    second = db.insert_intraday_accuracy_evaluation(conn, evaluation)
    assert second == 0
    conn.close()
