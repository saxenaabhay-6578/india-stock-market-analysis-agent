from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from config.settings import TOP_N
from src.storage import db

SOURCE = "nifty50_weights.py static snapshot"


def select_top_n(weights: dict[str, float], n: int = TOP_N) -> list[tuple[str, float, int]]:
    ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [(symbol, weight, rank + 1) for rank, (symbol, weight) in enumerate(ranked)]


def build_snapshot_rows(snapshot_date: str, selection: list[tuple[str, float, int]]) -> list[dict]:
    created_at = datetime.now(timezone.utc).isoformat()
    return [
        {
            "snapshot_date": snapshot_date, "symbol": symbol, "index_weight": weight,
            "rank": rank, "source": SOURCE, "created_at": created_at,
        }
        for symbol, weight, rank in selection
    ]


def record_universe_snapshot(
    conn: sqlite3.Connection, snapshot_date: str, selection: list[tuple[str, float, int]]
) -> None:
    db.insert_universe_snapshot_rows(conn, build_snapshot_rows(snapshot_date, selection))
