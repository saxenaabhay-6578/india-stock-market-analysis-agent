from datetime import datetime, timezone

import pytest

from src.accuracy.intraday_scorer import run_intraday_accuracy_evaluation
from src.storage.db import get_connection


def _insert_prediction(conn, **overrides):
    defaults = dict(
        created_at="2026-09-15T09:27:00+00:00", symbol="RELIANCE.NS",
        prediction_timestamp="2026-09-15T09:15:00+05:30", evaluation_timestamp="2026-09-15T10:15:00+05:30",
        prediction_type="next_hour", raw_current_price=1450.0,
        open=1450.0, high=1450.0, low=1450.0, close=1450.0, volume=0,
        direction="BULLISH", predicted_price=1465.0, expected_move_percent=1.03, confidence=65.0,
        reasoning="uptrend", key_risks_json="[]", technical_score=42.5, indicators_json="{}",
        data_provider="yfinance", interval="60m", claude_model="claude-sonnet-5",
        prompt_version="intraday-v1", raw_claude_response="{}", input_tokens=650, output_tokens=400,
    )
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO intraday_predictions (created_at, symbol, prediction_timestamp, "
        "evaluation_timestamp, prediction_type, raw_current_price, open, high, low, close, "
        "volume, direction, predicted_price, expected_move_percent, confidence, reasoning, "
        "key_risks_json, technical_score, indicators_json, data_provider, interval, "
        "claude_model, prompt_version, raw_claude_response, input_tokens, output_tokens) VALUES "
        "(:created_at, :symbol, :prediction_timestamp, :evaluation_timestamp, :prediction_type, "
        ":raw_current_price, :open, :high, :low, :close, :volume, :direction, :predicted_price, "
        ":expected_move_percent, :confidence, :reasoning, :key_risks_json, :technical_score, "
        ":indicators_json, :data_provider, :interval, :claude_model, :prompt_version, "
        ":raw_claude_response, :input_tokens, :output_tokens)",
        defaults,
    )
    conn.commit()


def _insert_hourly_bar(conn, symbol, timestamp, high, low, close):
    conn.execute(
        "INSERT INTO intraday_price_bars (symbol, timestamp, interval, open, high, low, close, "
        "volume, source, fetched_at) VALUES (?, ?, '60m', ?, ?, ?, ?, 1000, 'yfinance', ?)",
        (symbol, timestamp, close, high, low, close, timestamp),
    )
    conn.commit()


def test_bullish_correct_direction_and_target_hit(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T09:15:00+05:30", 1452.0, 1448.0, 1450.0)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T10:15:00+05:30", 1470.0, 1455.0, 1466.0)

    evaluated = run_intraday_accuracy_evaluation(conn, datetime.fromisoformat("2026-09-15T10:15:00+05:30"))
    assert evaluated == 1

    row = conn.execute("SELECT * FROM intraday_accuracy_evaluations WHERE prediction_id = 1").fetchone()
    assert row["direction_correct"] == 1
    assert row["target_hit"] == 1  # window high 1470 >= predicted 1465
    assert row["actual_price"] == 1466.0
    conn.close()


def test_not_evaluated_when_target_bar_missing(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T09:15:00+05:30", 1452.0, 1448.0, 1450.0)
    # no 10:15 bar cached yet

    evaluated = run_intraday_accuracy_evaluation(conn, datetime.fromisoformat("2026-09-15T10:15:00+05:30"))
    assert evaluated == 0
    conn.close()


def test_bearish_direction_correct_when_price_falls(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn, direction="BEARISH", predicted_price=1430.0)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T09:15:00+05:30", 1452.0, 1448.0, 1450.0)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T10:15:00+05:30", 1450.0, 1425.0, 1428.0)

    run_intraday_accuracy_evaluation(conn, datetime.fromisoformat("2026-09-15T10:15:00+05:30"))
    row = conn.execute("SELECT * FROM intraday_accuracy_evaluations WHERE prediction_id = 1").fetchone()
    assert row["direction_correct"] == 1
    assert row["target_hit"] == 1  # window low 1425 <= predicted 1430
    conn.close()


def test_error_metrics_are_computed(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T09:15:00+05:30", 1452.0, 1448.0, 1450.0)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T10:15:00+05:30", 1460.0, 1455.0, 1458.0)

    run_intraday_accuracy_evaluation(conn, datetime.fromisoformat("2026-09-15T10:15:00+05:30"))
    row = conn.execute("SELECT * FROM intraday_accuracy_evaluations WHERE prediction_id = 1").fetchone()
    assert row["abs_error"] == pytest.approx(7.0)   # |1458 - 1465|
    assert row["pct_error"] == pytest.approx(7.0 / 1450.0 * 100)
    conn.close()


def test_never_evaluates_the_same_prediction_twice(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T09:15:00+05:30", 1452.0, 1448.0, 1450.0)
    _insert_hourly_bar(conn, "RELIANCE.NS", "2026-09-15T10:15:00+05:30", 1460.0, 1455.0, 1458.0)

    first_run = run_intraday_accuracy_evaluation(conn, datetime.fromisoformat("2026-09-15T10:15:00+05:30"))
    second_run = run_intraday_accuracy_evaluation(conn, datetime.fromisoformat("2026-09-15T10:15:00+05:30"))
    assert first_run == 1
    assert second_run == 0
    count = conn.execute("SELECT COUNT(*) FROM intraday_accuracy_evaluations").fetchone()[0]
    assert count == 1
    conn.close()
