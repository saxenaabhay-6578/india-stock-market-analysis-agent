from datetime import date

import pytest

from src.accuracy.scorer import run_accuracy_evaluation
from src.storage.db import get_connection


def _insert_prediction(conn, **overrides):
    defaults = dict(
        created_at="2026-09-01T00:00:00", prediction_date="2026-09-01", symbol="TCS.NS",
        horizon_days=5, target_evaluation_date="2026-09-08", current_price=1000.0,
        direction="BULLISH", target_price=1050.0, range_low=1020.0, range_high=1060.0,
        expected_move_percent=5.0, risk_level=980.0, confidence=70.0, reasoning="test",
        key_risks_json="[]", technical_score=50.0, indicators_json="{}",
        data_timestamp="2026-09-01T00:00:00", data_provider="yfinance",
        claude_model="claude-sonnet-5", prompt_version="v1", raw_claude_response="{}",
    )
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO predictions (created_at, prediction_date, symbol, horizon_days, "
        "target_evaluation_date, current_price, direction, target_price, range_low, "
        "range_high, expected_move_percent, risk_level, confidence, reasoning, "
        "key_risks_json, technical_score, indicators_json, data_timestamp, "
        "data_provider, claude_model, prompt_version, raw_claude_response) "
        "VALUES (:created_at, :prediction_date, :symbol, :horizon_days, "
        ":target_evaluation_date, :current_price, :direction, :target_price, :range_low, "
        ":range_high, :expected_move_percent, :risk_level, :confidence, :reasoning, "
        ":key_risks_json, :technical_score, :indicators_json, :data_timestamp, "
        ":data_provider, :claude_model, :prompt_version, :raw_claude_response)",
        defaults,
    )
    conn.commit()


def _insert_bars(conn, symbol, bars):
    for d, high, low, close in bars:
        conn.execute(
            "INSERT INTO price_bars (symbol, date, open, high, low, close, adj_close, volume, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yfinance', '2026-09-08T00:00:00')",
            (symbol, d, close, high, low, close, close, 1000),
        )
    conn.commit()


def test_bullish_correct_direction_and_target_hit(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_bars(conn, "TCS.NS", [
        ("2026-09-02", 1010, 995, 1005),
        ("2026-09-03", 1020, 1000, 1015),
        ("2026-09-04", 1055, 1010, 1040),
        ("2026-09-05", 1045, 1020, 1030),
        ("2026-09-08", 1060, 1030, 1055),
    ])
    evaluated = run_accuracy_evaluation(conn, date(2026, 9, 8))
    assert evaluated == 1

    row = conn.execute("SELECT * FROM accuracy_evaluations WHERE prediction_id = 1").fetchone()
    assert row["direction_correct"] == 1
    assert row["target_hit"] == 1
    assert row["actual_close"] == 1055.0
    assert row["window_high"] == 1060.0
    conn.close()


def test_not_evaluated_when_target_bar_missing(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_bars(conn, "TCS.NS", [("2026-09-02", 1010, 995, 1005)])

    evaluated = run_accuracy_evaluation(conn, date(2026, 9, 8))
    assert evaluated == 0
    count = conn.execute("SELECT COUNT(*) FROM accuracy_evaluations").fetchone()[0]
    assert count == 0
    conn.close()


def test_bearish_direction_correct_when_price_falls(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn, direction="BEARISH", target_price=950.0, range_low=940.0, range_high=980.0)
    _insert_bars(conn, "TCS.NS", [
        ("2026-09-02", 995, 970, 980),
        ("2026-09-08", 970, 940, 945),
    ])
    run_accuracy_evaluation(conn, date(2026, 9, 8))
    row = conn.execute("SELECT * FROM accuracy_evaluations WHERE prediction_id = 1").fetchone()
    assert row["direction_correct"] == 1
    assert row["target_hit"] == 1
    conn.close()


def test_error_metrics_are_computed(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_prediction(conn)
    _insert_bars(conn, "TCS.NS", [("2026-09-08", 1060, 1030, 1040)])
    run_accuracy_evaluation(conn, date(2026, 9, 8))
    row = conn.execute("SELECT * FROM accuracy_evaluations WHERE prediction_id = 1").fetchone()
    assert row["prediction_error"] == 1040.0 - 1050.0
    assert row["abs_error"] == 10.0
    assert row["pct_error"] == pytest.approx(1.0)
    assert row["return_after_prediction_percent"] == pytest.approx(4.0)
    conn.close()
