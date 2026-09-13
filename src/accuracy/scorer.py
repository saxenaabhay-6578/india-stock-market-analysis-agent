from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from src.storage import db


def find_predictions_due_for_evaluation(conn: sqlite3.Connection, as_of: date) -> list[sqlite3.Row]:
    query = """
        SELECT p.* FROM predictions p
        LEFT JOIN accuracy_evaluations a ON a.prediction_id = p.id
        WHERE a.id IS NULL AND p.target_evaluation_date <= ?
    """
    return conn.execute(query, (as_of.isoformat(),)).fetchall()


def evaluate_prediction(conn: sqlite3.Connection, prediction: sqlite3.Row) -> dict | None:
    symbol = prediction["symbol"]
    prediction_date = prediction["prediction_date"]
    target_date = prediction["target_evaluation_date"]

    window = db.get_price_bars(conn, symbol, start=prediction_date, end=target_date, start_exclusive=True)
    if window.empty or window.iloc[-1]["date"] != target_date:
        return None

    target_row = window[window["date"] == target_date].iloc[0]
    actual_close = float(target_row["close"])
    actual_high = float(target_row["high"])
    actual_low = float(target_row["low"])
    window_high = float(window["high"].max())
    window_low = float(window["low"].min())

    current_price = prediction["current_price"]
    target_price = prediction["target_price"]
    predicted_direction = prediction["direction"]

    actual_move = actual_close - current_price
    if predicted_direction == "BULLISH":
        direction_correct = actual_move > 0
    elif predicted_direction == "BEARISH":
        direction_correct = actual_move < 0
    else:
        direction_correct = abs(actual_move / current_price) < 0.005

    if predicted_direction == "BULLISH":
        target_hit = window_high >= target_price
        max_favorable_excursion = window_high - current_price
        max_adverse_excursion = current_price - window_low
    elif predicted_direction == "BEARISH":
        target_hit = window_low <= target_price
        max_favorable_excursion = current_price - window_low
        max_adverse_excursion = window_high - current_price
    else:
        target_hit = window_low <= target_price <= window_high
        max_favorable_excursion = max(window_high - current_price, current_price - window_low)
        max_adverse_excursion = max_favorable_excursion

    within_range = prediction["range_low"] <= actual_close <= prediction["range_high"]
    prediction_error = actual_close - target_price
    abs_error = abs(prediction_error)
    pct_error = abs_error / current_price * 100
    return_after_prediction_percent = (actual_close - current_price) / current_price * 100

    return {
        "prediction_id": prediction["id"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_date": target_date,
        "actual_close": actual_close,
        "actual_high": actual_high,
        "actual_low": actual_low,
        "window_high": window_high,
        "window_low": window_low,
        "direction_correct": int(direction_correct),
        "target_hit": int(target_hit),
        "within_range": int(within_range),
        "prediction_error": prediction_error,
        "abs_error": abs_error,
        "pct_error": pct_error,
        "return_after_prediction_percent": return_after_prediction_percent,
        "max_favorable_excursion": max_favorable_excursion,
        "max_adverse_excursion": max_adverse_excursion,
    }


def insert_accuracy_evaluation(conn: sqlite3.Connection, evaluation: dict) -> None:
    db.insert_accuracy_evaluation(conn, evaluation)


def run_accuracy_evaluation(conn: sqlite3.Connection, as_of: date) -> int:
    due = find_predictions_due_for_evaluation(conn, as_of)
    evaluated_count = 0
    for prediction in due:
        evaluation = evaluate_prediction(conn, prediction)
        if evaluation is not None:
            insert_accuracy_evaluation(conn, evaluation)
            evaluated_count += 1
    return evaluated_count
