from src.storage.db import get_connection
from src.universe import selection

WEIGHTS = {
    "HDFCBANK.NS": 13.2,
    "RELIANCE.NS": 9.4,
    "ICICIBANK.NS": 8.7,
    "INFY.NS": 5.6,
    "TCS.NS": 3.9,
}


def test_select_top_n_orders_by_weight_descending():
    result = selection.select_top_n(WEIGHTS, n=3)
    assert [symbol for symbol, _, _ in result] == ["HDFCBANK.NS", "RELIANCE.NS", "ICICIBANK.NS"]
    assert [rank for _, _, rank in result] == [1, 2, 3]


def test_select_top_n_is_deterministic():
    first = selection.select_top_n(WEIGHTS, n=3)
    second = selection.select_top_n(WEIGHTS, n=3)
    assert first == second


def test_build_snapshot_rows_shapes_one_dict_per_symbol():
    ranked = selection.select_top_n(WEIGHTS, n=2)
    rows = selection.build_snapshot_rows("2026-09-13", ranked)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "HDFCBANK.NS"
    assert rows[0]["rank"] == 1
    assert rows[0]["snapshot_date"] == "2026-09-13"


def test_record_universe_snapshot_persists_via_storage(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    ranked = selection.select_top_n(WEIGHTS, n=3)
    selection.record_universe_snapshot(conn, "2026-09-13", ranked)

    rows = conn.execute(
        "SELECT symbol, index_weight, rank FROM universe_snapshots WHERE snapshot_date = '2026-09-13' ORDER BY rank"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["symbol"] == "HDFCBANK.NS"
    assert rows[0]["rank"] == 1
    conn.close()
