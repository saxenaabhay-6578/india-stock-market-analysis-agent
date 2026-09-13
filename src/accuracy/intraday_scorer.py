from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.storage import db


def find_intraday_predictions_due_for_evaluation(conn: sqlite3.Connection, as_of: datetime) -> list[sqlite3.Row]:
    query = """
        SELECT p.* FROM intraday_predictions p
        LEFT JOIN intraday_accuracy_evaluations a ON a.prediction_id = p.id
        WHERE a.id IS NULL AND p.evaluation_timestamp <= ?
    """
    return conn.execute(query, (as_of.isoformat(),)).fetchall()


def evaluate_intraday_prediction(conn: sqlite3.Connection, prediction: sqlite3.Row) -> dict | None:
    symbol = prediction["symbol"]
    prediction_ts = prediction["prediction_timestamp"]
    evaluation_ts = prediction["evaluation_timestamp"]

    hourly = db.get_intraday_price_bars(
        conn, symbol, interval="60m", start=prediction_ts, end=evaluation_ts, start_exclusive=True,
    )
    if hourly.empty or hourly.iloc[-1]["timestamp"] != evaluation_ts:
        return None

    actual_price = float(hourly.iloc[-1]["close"])
    predicted_price = prediction["predicted_price"]
    raw_current_price = prediction["raw_current_price"]
    direction = prediction["direction"]

    window_high = float(hourly["high"].max())
    window_low = float(hourly["low"].min())

    move = actual_price - raw_current_price
    if direction == "BULLISH":
        direction_correct = move > 0
        target_hit = window_high >= predicted_price
    elif direction == "BEARISH":
        direction_correct = move < 0
        target_hit = window_low <= predicted_price
    else:
        direction_correct = abs(move / raw_current_price) < 0.002
        target_hit = window_low <= predicted_price <= window_high

    abs_error = abs(actual_price - predicted_price)
    pct_error = abs_error / raw_current_price * 100

    return {
        "prediction_id": prediction["id"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_timestamp": evaluation_ts,
        "actual_price": actual_price,
        "predicted_price": predicted_price,
        "abs_error": abs_error,
        "pct_error": pct_error,
        "direction_correct": int(direction_correct),
        "target_hit": int(target_hit),
    }


def insert_intraday_accuracy_evaluation(conn: sqlite3.Connection, evaluation: dict) -> int:
    return db.insert_intraday_accuracy_evaluation(conn, evaluation)


def run_intraday_accuracy_evaluation(conn: sqlite3.Connection, as_of: datetime) -> int:
    due = find_intraday_predictions_due_for_evaluation(conn, as_of)
    evaluated_count = 0
    for prediction in due:
        evaluation = evaluate_intraday_prediction(conn, prediction)
        if evaluation is not None:
            evaluated_count += insert_intraday_accuracy_evaluation(conn, evaluation)
    return evaluated_count
