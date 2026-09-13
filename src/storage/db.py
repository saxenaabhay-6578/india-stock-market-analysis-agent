from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config.settings import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    index_weight REAL NOT NULL,
    rank INTEGER NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    adj_close REAL NOT NULL,
    volume INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(symbol, date)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    prediction_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    target_evaluation_date TEXT NOT NULL,
    current_price REAL NOT NULL,
    direction TEXT NOT NULL,
    target_price REAL NOT NULL,
    range_low REAL NOT NULL,
    range_high REAL NOT NULL,
    expected_move_percent REAL NOT NULL,
    risk_level REAL NOT NULL,
    confidence REAL NOT NULL,
    reasoning TEXT NOT NULL,
    key_risks_json TEXT NOT NULL,
    technical_score REAL NOT NULL,
    indicators_json TEXT NOT NULL,
    data_timestamp TEXT NOT NULL,
    data_provider TEXT NOT NULL,
    claude_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_claude_response TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accuracy_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    evaluation_date TEXT NOT NULL,
    actual_close REAL NOT NULL,
    actual_high REAL NOT NULL,
    actual_low REAL NOT NULL,
    window_high REAL NOT NULL,
    window_low REAL NOT NULL,
    direction_correct INTEGER NOT NULL,
    target_hit INTEGER NOT NULL,
    within_range INTEGER NOT NULL,
    prediction_error REAL NOT NULL,
    abs_error REAL NOT NULL,
    pct_error REAL NOT NULL,
    return_after_prediction_percent REAL NOT NULL,
    max_favorable_excursion REAL NOT NULL,
    max_adverse_excursion REAL NOT NULL,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_universe_snapshot_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO universe_snapshots (snapshot_date, symbol, index_weight, rank, source, created_at) "
        "VALUES (:snapshot_date, :symbol, :index_weight, :rank, :source, :created_at)",
        rows,
    )
    conn.commit()


def cache_price_bars(conn: sqlite3.Connection, symbol: str, df: pd.DataFrame, source: str) -> None:
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            symbol, row.date.isoformat(), row.open, row.high, row.low,
            row.close, row.adj_close, int(row.volume), source, fetched_at,
        )
        for row in df.itertuples(index=False)
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO price_bars "
        "(symbol, date, open, high, low, close, adj_close, volume, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def get_price_bars(
    conn: sqlite3.Connection, symbol: str, start: str | None = None, end: str | None = None,
    start_exclusive: bool = False,
) -> pd.DataFrame:
    start_operator = ">" if start_exclusive else ">="
    query = "SELECT date, open, high, low, close, adj_close, volume FROM price_bars WHERE symbol = ?"
    params: list = [symbol]
    if start:
        query += f" AND date {start_operator} ?"
        params.append(start)
    if end:
        query += " AND date <= ?"
        params.append(end)
    query += " ORDER BY date"
    return pd.read_sql_query(query, conn, params=params)


def insert_prediction_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO predictions (created_at, prediction_date, symbol, horizon_days, "
        "target_evaluation_date, current_price, direction, target_price, range_low, "
        "range_high, expected_move_percent, risk_level, confidence, reasoning, "
        "key_risks_json, technical_score, indicators_json, data_timestamp, "
        "data_provider, claude_model, prompt_version, raw_claude_response) VALUES "
        "(:created_at, :prediction_date, :symbol, :horizon_days, :target_evaluation_date, "
        ":current_price, :direction, :target_price, :range_low, :range_high, "
        ":expected_move_percent, :risk_level, :confidence, :reasoning, :key_risks_json, "
        ":technical_score, :indicators_json, :data_timestamp, :data_provider, "
        ":claude_model, :prompt_version, :raw_claude_response)",
        rows,
    )
    conn.commit()


def insert_accuracy_evaluation(conn: sqlite3.Connection, evaluation: dict) -> None:
    conn.execute(
        "INSERT INTO accuracy_evaluations (prediction_id, evaluated_at, evaluation_date, "
        "actual_close, actual_high, actual_low, window_high, window_low, direction_correct, "
        "target_hit, within_range, prediction_error, abs_error, pct_error, "
        "return_after_prediction_percent, max_favorable_excursion, max_adverse_excursion) "
        "VALUES (:prediction_id, :evaluated_at, :evaluation_date, :actual_close, :actual_high, "
        ":actual_low, :window_high, :window_low, :direction_correct, :target_hit, :within_range, "
        ":prediction_error, :abs_error, :pct_error, :return_after_prediction_percent, "
        ":max_favorable_excursion, :max_adverse_excursion)",
        evaluation,
    )
    conn.commit()
