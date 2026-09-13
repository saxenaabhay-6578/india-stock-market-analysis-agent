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
