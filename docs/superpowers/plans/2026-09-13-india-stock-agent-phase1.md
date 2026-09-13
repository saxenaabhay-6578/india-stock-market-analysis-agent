# India Stock Market Analysis Agent — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 pipeline described in the design spec — pull 3 months of OHLCV for the top-20 NIFTY-50 stocks by weight, compute indicators/technical score, get one structured multi-horizon Claude prediction per stock, store everything append-only in SQLite, evaluate previously-due predictions against real outcomes, and generate an 8-sheet Excel report — runnable manually via `python -m src.run_daily`.

**Architecture:** Pure Python, organized as nested packages by responsibility (`providers`, `universe`, `analysis`, `prediction`, `storage`, `accuracy`, `reporting`) so Phase 2 work — new providers, richer accuracy breakdowns — slots in without touching unrelated layers. `src/storage/db.py` is the only module that talks SQL for writes; every other domain module builds plain data (tuples/dicts) and calls into `storage` to persist it. `src/reporting/excel_report.py` reads directly from SQLite (read-only, report-specific projections). A top-level `config/` package holds non-secret runtime settings (paths, retry counts, model id); secrets stay in a git-ignored `.env`, read directly by the libraries that need them.

**Tech Stack:** Python 3.11+, yfinance, pandas, openpyxl, sqlite3 (stdlib), anthropic SDK, pytest, python-dotenv.

**Spec:** `docs/superpowers/specs/2026-09-13-india-stock-agent-design.md`

## Global Constraints

- Pure Python only — no Node/JS/Bun dependency anywhere (spec §1).
- Phase 1 is manual-execution only; no `launchd` scheduling wired in yet (spec §1, §11).
- Paid data providers, real fundamentals, real news/sentiment, dynamic universe selection are explicitly deferred — Phase 1 ships clean interfaces + stubs only (spec §1).
- `universe_snapshots`, `price_bars`, `predictions`, `accuracy_evaluations` are append-only in normal operation — no `UPDATE`/`DELETE` (spec §8).
- One Claude API call per stock per day, covering all 4 horizons at once — never per-horizon (spec §7, §14).
- Prompts to Claude contain only computed technical score + summarized indicators — never the full raw price history (spec §7, §14).
- Claude's `current_price` is only ever used as a sanity-check echo, never trusted as authoritative; Claude never invents/looks up prices (spec §7).
- On Claude JSON validation failure: retry once; if it still fails, skip that stock entirely for every horizon — no partial storage (spec §7).
- `target_evaluation_date` is computed once at prediction-creation time via `TradingCalendar.add_trading_days` and never recomputed later (spec §8).
- No look-ahead bias: the evaluator only reads `price_bars` on or before "today", and only evaluates once `target_evaluation_date` has occurred and its bar exists in `price_bars` (spec §8, §9).
- Horizons are trading days, not calendar days (spec §4).
- Excel is a generated report only, read from SQLite — never hand-edited, never the source of truth (spec §10).
- Every symbol is processed independently; one symbol's failure never stops the other 19 (spec §12).
- No secrets (API keys) ever logged or committed; `.env` is git-ignored; `config/` never holds secrets (spec §12, and user directive of 2026-09-13).
- Unit tests never make live network or live Claude calls — yfinance and the Anthropic client are mocked (spec §13).
- No duplicate implementations of the same persistence/read logic: all table writes go through `src/storage/db.py`; domain modules build data, storage persists it (user directive of 2026-09-13).
- Provider interfaces (`MarketDataProvider`, `FundamentalsProvider`, `NewsSentimentProvider`) and `TradingCalendar` are the only extension points Phase 2 needs — adding an implementation must never require changing `analysis/`, `prediction/`, `storage/`, or `reporting/` (user directive of 2026-09-13).

---

## Canonical project architecture

```
india-stock-agent/
├── requirements.txt
├── .gitignore
├── .env.example
├── README.md
├── config/
│   ├── __init__.py
│   └── settings.py              # non-secret paths & tunables — NO secrets
├── src/
│   ├── __init__.py
│   ├── run_daily.py             # CLI entrypoint, wires every layer together
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── market_data.py       # MarketDataProvider (ABC) + YFinanceProvider
│   │   ├── fundamentals.py      # FundamentalsProvider (ABC) + Phase 1 stub
│   │   └── news.py              # NewsSentimentProvider (ABC) + Phase 1 stub
│   ├── universe/
│   │   ├── __init__.py
│   │   ├── nifty50_weights.py   # static NIFTY-50 weights table (domain data)
│   │   ├── nse_holidays.py      # static NSE holiday list (domain data)
│   │   ├── trading_calendar.py  # TradingCalendar (ABC) + NseStaticHolidayCalendar
│   │   └── selection.py         # top-N selection + snapshot row building
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── indicators.py        # raw indicator math + compute_indicators orchestrator
│   │   └── technical_score.py   # SCORE_WEIGHTS + compute_technical_score (pure formula)
│   ├── prediction/
│   │   ├── __init__.py
│   │   └── engine.py            # prompt, Claude call, JSON validation, row building
│   ├── storage/
│   │   ├── __init__.py
│   │   └── db.py                # SCHEMA, connection, every SQL read/write
│   ├── accuracy/
│   │   ├── __init__.py
│   │   └── scorer.py            # evaluation math + orchestration over due predictions
│   └── reporting/
│       ├── __init__.py
│       └── excel_report.py      # 8-sheet workbook, reads SQLite directly (report-specific queries)
└── tests/
    ├── __init__.py
    ├── test_db.py
    ├── test_selection.py
    ├── test_trading_calendar.py
    ├── test_market_data.py
    ├── test_fundamentals.py
    ├── test_news.py
    ├── test_indicators.py
    ├── test_technical_score.py
    ├── test_engine.py
    ├── test_scorer.py
    ├── test_excel_report.py
    └── test_run_daily.py
```

## Module responsibilities (one line each)

| Module | Responsibility | Depends on | Never does |
|---|---|---|---|
| `config/settings.py` | Non-secret paths (`DB_PATH`, `REPORTS_DIR`, `LOGS_DIR`), tunables (`TOP_N`, retry counts, lookback days, `CLAUDE_MODEL`, `PROMPT_VERSION`) | nothing | hold API keys or any secret |
| `src/providers/market_data.py` | `MarketDataProvider` interface + `YFinanceProvider`: fetch OHLCV, retry transient failures, normalize columns | `config.settings` | know about SQLite or Claude |
| `src/providers/fundamentals.py` | `FundamentalsProvider` interface + Phase 1 "not available" stub | nothing | invent/estimate fundamentals data |
| `src/providers/news.py` | `NewsSentimentProvider` interface + Phase 1 "not available" stub | nothing | invent/estimate sentiment data |
| `src/universe/nifty50_weights.py` | Static, version-controlled index weights (domain data, not config) | nothing | fetch live data |
| `src/universe/nse_holidays.py` | Static, version-controlled NSE holiday list (domain data) | nothing | fetch live data |
| `src/universe/trading_calendar.py` | `TradingCalendar` interface + `NseStaticHolidayCalendar` trading-day math | `nse_holidays` | know about predictions/evaluations |
| `src/universe/selection.py` | Deterministic top-N selection; builds snapshot rows; calls storage to persist | `config.settings`, `src.storage.db` | write raw SQL itself |
| `src/analysis/indicators.py` | Raw indicator math (SMA/RSI/MACD/Bollinger/support-resistance/volatility/momentum/volume trend) + `compute_indicators` orchestrator | `src.analysis.technical_score` | know about Claude or SQLite |
| `src/analysis/technical_score.py` | The documented, weighted scoring formula, isolated as a pure function | nothing | fetch data or format prompts |
| `src/prediction/engine.py` | Builds the Claude prompt, calls Claude, validates strict JSON, builds prediction rows, calls storage to persist | `config.settings`, `src.storage.db` | write raw SQL itself; trust Claude's echoed price as authoritative |
| `src/storage/db.py` | Schema, connection factory, and **every** SQL read/write for `universe_snapshots`, `price_bars`, `predictions`, `accuracy_evaluations` | `config.settings` | contain business/domain logic (calendars, scoring, validation) |
| `src/accuracy/scorer.py` | Finds predictions due for evaluation, computes outcome metrics from cached price bars, calls storage to persist evaluations | `src.storage.db` | re-fetch live prices; evaluate before the target date has occurred |
| `src/reporting/excel_report.py` | Builds all 8 report sheets from read-only SQLite queries | `config.settings` (default output dir) | write to SQLite; hand-edit workbooks |
| `src/run_daily.py` | CLI parsing, logging setup, dependency wiring, orchestration only | all of the above | contain indicator math, validation rules, or SQL |

**Extensibility guarantee:** every provider is consumed only through its ABC (`MarketDataProvider`, `FundamentalsProvider`, `NewsSentimentProvider`), injected by `run_daily.py`. A Phase 2 provider (e.g. `src/providers/kite.py implementing MarketDataProvider`) is added by writing the new file and changing one line in `run_daily.main()` — `analysis/`, `prediction/`, `storage/`, and `reporting/` never change.

---

### Task 1: Project scaffolding + config package + SQLite schema

**Files:**
- Create: `requirements.txt`, `.gitignore`, `.env.example`
- Create: `config/__init__.py`, `config/settings.py`
- Create: `src/__init__.py`, `src/providers/__init__.py`, `src/universe/__init__.py`, `src/analysis/__init__.py`, `src/prediction/__init__.py`, `src/storage/__init__.py`, `src/accuracy/__init__.py`, `src/reporting/__init__.py`, `tests/__init__.py`
- Create: `src/storage/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `config.settings.{DB_PATH, REPORTS_DIR, LOGS_DIR, TOP_N, MARKET_DATA_LOOKBACK_DAYS, MARKET_DATA_MAX_RETRIES, MARKET_DATA_BACKOFF_SECONDS, CLAUDE_MODEL, PROMPT_VERSION}`. `src.storage.db.get_connection(db_path=DB_PATH) -> sqlite3.Connection`; `db.insert_universe_snapshot_rows(conn, rows: list[dict]) -> None`; `db.cache_price_bars(conn, symbol, df, source) -> None`; `db.get_price_bars(conn, symbol, start=None, end=None, start_exclusive=False) -> pd.DataFrame`; `db.insert_prediction_rows(conn, rows: list[dict]) -> None`; `db.insert_accuracy_evaluation(conn, evaluation: dict) -> None`. Every later task's persistence goes through these functions — nowhere else writes SQL.

- [ ] **Step 1: Create scaffolding files**

`requirements.txt`:
```
yfinance>=0.2.40
pandas>=2.2
openpyxl>=3.1
anthropic>=0.40
python-dotenv>=1.0
pytest>=8.0
```

`.gitignore`:
```
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
.env
data/
reports/
logs/
```

`.env.example`:
```
ANTHROPIC_API_KEY=
```

Create empty `__init__.py` in: `config/`, `src/`, `src/providers/`, `src/universe/`, `src/analysis/`, `src/prediction/`, `src/storage/`, `src/accuracy/`, `src/reporting/`, `tests/`.

`config/settings.py`:
```python
"""
Non-secret runtime configuration for the India stock market analysis agent.

No secrets live here. API keys are read directly from environment
variables (populated from a git-ignored .env file) by the libraries that
need them — e.g. the Anthropic SDK reads ANTHROPIC_API_KEY itself. This
module must never import/expose a secret value.
"""
from __future__ import annotations

from pathlib import Path

# Storage locations
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "stock_agent.db"
REPORTS_DIR = Path("reports")
LOGS_DIR = Path("logs")

# Universe
TOP_N = 20

# Market data provider
MARKET_DATA_LOOKBACK_DAYS = 95  # ~3 months including weekends/holidays buffer
MARKET_DATA_MAX_RETRIES = 2
MARKET_DATA_BACKOFF_SECONDS = 1.0

# Prediction engine
CLAUDE_MODEL = "claude-sonnet-5"
PROMPT_VERSION = "v1"
```

- [ ] **Step 2: Write the failing test**

`tests/test_db.py`:
```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.storage'` (or similar import error).

- [ ] **Step 4: Implement `src/storage/db.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore .env.example config/ src/__init__.py src/providers/__init__.py src/universe/__init__.py src/analysis/__init__.py src/prediction/__init__.py src/storage/__init__.py src/accuracy/__init__.py src/reporting/__init__.py tests/__init__.py src/storage/db.py tests/test_db.py
git commit -m "feat: scaffold nested package layout, config settings, and SQLite storage layer"
```

---

### Task 2: NIFTY-50 universe selection

**Files:**
- Create: `src/universe/nifty50_weights.py`
- Create: `src/universe/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `config.settings.TOP_N` (Task 1); `src.storage.db.insert_universe_snapshot_rows` (Task 1).
- Produces: `selection.select_top_n(weights: dict[str, float], n: int = TOP_N) -> list[tuple[str, float, int]]` (symbol, weight, rank); `selection.build_snapshot_rows(snapshot_date: str, selection: list[tuple]) -> list[dict]`; `selection.record_universe_snapshot(conn, snapshot_date: str, selection: list[tuple]) -> None`. Used by `src/run_daily.py` (Task 10).

- [ ] **Step 1: Write the failing test**

`tests/test_selection.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.universe.selection'`.

- [ ] **Step 3: Implement `src/universe/nifty50_weights.py` and `src/universe/selection.py`**

`src/universe/nifty50_weights.py`:
```python
# NIFTY-50 index weights (approximate), last refreshed 2026-09-13.
# Source: publicly reported NSE Indices factsheet approximate weightings.
# These are approximate and must be manually refreshed periodically — see
# README "Updating the universe" section. Not live-scraped in Phase 1.
NIFTY50_WEIGHTS = {
    "HDFCBANK.NS": 13.20,
    "RELIANCE.NS": 9.40,
    "ICICIBANK.NS": 8.70,
    "INFY.NS": 5.60,
    "LT.NS": 4.10,
    "TCS.NS": 3.90,
    "BHARTIARTL.NS": 3.80,
    "ITC.NS": 3.60,
    "KOTAKBANK.NS": 3.10,
    "AXISBANK.NS": 3.00,
    "SBIN.NS": 2.90,
    "HINDUNILVR.NS": 2.30,
    "BAJFINANCE.NS": 2.20,
    "NTPC.NS": 1.90,
    "M&M.NS": 1.90,
    "SUNPHARMA.NS": 1.80,
    "MARUTI.NS": 1.70,
    "HCLTECH.NS": 1.60,
    "TITAN.NS": 1.50,
    "ULTRACEMCO.NS": 1.40,
    "TATAMOTORS.NS": 1.40,
    "POWERGRID.NS": 1.30,
    "ASIANPAINT.NS": 1.20,
    "NESTLEIND.NS": 1.10,
    "TATASTEEL.NS": 1.10,
}
```

`src/universe/selection.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_selection.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/universe/nifty50_weights.py src/universe/selection.py tests/test_selection.py
git commit -m "feat: add NIFTY-50 weights and top-20 universe selection"
```

---

### Task 3: Trading calendar

**Files:**
- Create: `src/universe/nse_holidays.py`
- Create: `src/universe/trading_calendar.py`
- Test: `tests/test_trading_calendar.py`

**Interfaces:**
- Produces: `trading_calendar.TradingCalendar` (ABC with `is_trading_day`, `add_trading_days`, `trading_days_between`); `trading_calendar.NseStaticHolidayCalendar` implementing it. Used by `src.prediction.engine` (Task 7), `src.accuracy.scorer` (Task 8), `src.run_daily` (Task 10).

- [ ] **Step 1: Write the failing test**

`tests/test_trading_calendar.py`:
```python
from datetime import date

from src.universe.trading_calendar import NseStaticHolidayCalendar


def test_weekday_is_trading_day():
    cal = NseStaticHolidayCalendar()
    assert cal.is_trading_day(date(2026, 9, 14))  # Monday


def test_weekend_is_not_trading_day():
    cal = NseStaticHolidayCalendar()
    assert not cal.is_trading_day(date(2026, 9, 12))  # Saturday
    assert not cal.is_trading_day(date(2026, 9, 13))  # Sunday


def test_holiday_is_not_trading_day():
    cal = NseStaticHolidayCalendar()
    assert not cal.is_trading_day(date(2026, 10, 2))  # Gandhi Jayanti


def test_add_trading_days_skips_weekend():
    cal = NseStaticHolidayCalendar()
    friday = date(2026, 9, 11)
    assert cal.add_trading_days(friday, 1) == date(2026, 9, 14)  # Monday


def test_add_trading_days_skips_holiday():
    cal = NseStaticHolidayCalendar()
    before_holiday = date(2026, 10, 1)  # Thursday
    # 2026-10-02 is a holiday, 2026-10-03/04 is Sat/Sun -> next trading day is 10-05
    assert cal.add_trading_days(before_holiday, 1) == date(2026, 10, 5)


def test_trading_days_between_counts_only_trading_days():
    cal = NseStaticHolidayCalendar()
    start = date(2026, 9, 11)  # Friday
    end = date(2026, 9, 15)  # Tuesday
    assert cal.trading_days_between(start, end) == 2  # Mon + Tue
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.universe.trading_calendar'`.

- [ ] **Step 3: Implement `src/universe/nse_holidays.py` and `src/universe/trading_calendar.py`**

`src/universe/nse_holidays.py`:
```python
# NSE trading holidays by year. Manually maintained — refresh every
# January from the official NSE holiday circular (nseindia.com). This list
# is a best-effort approximation as of 2026-09-13 for fixed and commonly
# observed holidays; VERIFY against the official circular before relying on
# it for anything beyond development/testing.
NSE_HOLIDAYS = {
    2026: [
        "2026-01-26",  # Republic Day
        "2026-03-04",  # Holi
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-08-15",  # Independence Day
        "2026-08-26",  # Ganesh Chaturthi
        "2026-10-02",  # Gandhi Jayanti
        "2026-10-21",  # Diwali Laxmi Pujan
        "2026-11-24",  # Guru Nanak Jayanti
        "2026-12-25",  # Christmas
    ],
}
```

`src/universe/trading_calendar.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta

from src.universe.nse_holidays import NSE_HOLIDAYS


class TradingCalendar(ABC):
    @abstractmethod
    def is_trading_day(self, d: date) -> bool: ...

    @abstractmethod
    def add_trading_days(self, d: date, n: int) -> date: ...

    @abstractmethod
    def trading_days_between(self, start: date, end: date) -> int: ...


class NseStaticHolidayCalendar(TradingCalendar):
    def __init__(self, holidays: dict[int, list[str]] = NSE_HOLIDAYS):
        self._holidays = {
            date.fromisoformat(d) for year_holidays in holidays.values() for d in year_holidays
        }

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def add_trading_days(self, d: date, n: int) -> date:
        if n < 0:
            raise ValueError("n must be non-negative")
        current = d
        remaining = n
        while remaining > 0:
            current += timedelta(days=1)
            if self.is_trading_day(current):
                remaining -= 1
        return current

    def trading_days_between(self, start: date, end: date) -> int:
        if end < start:
            raise ValueError("end must be on or after start")
        count = 0
        current = start
        while current < end:
            current += timedelta(days=1)
            if self.is_trading_day(current):
                count += 1
        return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_calendar.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/universe/nse_holidays.py src/universe/trading_calendar.py tests/test_trading_calendar.py
git commit -m "feat: add NSE trading calendar with static holiday list"
```

---

### Task 4: Market data provider

**Files:**
- Create: `src/providers/market_data.py`
- Test: `tests/test_market_data.py`

**Interfaces:**
- Consumes: `config.settings.{MARKET_DATA_MAX_RETRIES, MARKET_DATA_BACKOFF_SECONDS}` (Task 1).
- Produces: `market_data.MarketDataProvider` (ABC); `market_data.YFinanceProvider.get_history(symbol, start, end) -> pd.DataFrame | None` with columns `["date","open","high","low","close","adj_close","volume"]`; `market_data.YFinanceProvider.get_latest_close(symbol) -> tuple[date, float] | None`. Used by `run_daily` (Task 10). Persistence of the returned DataFrame goes through `src.storage.db.cache_price_bars` (Task 1) — this module never touches SQLite.

- [ ] **Step 1: Write the failing tests**

`tests/test_market_data.py`:
```python
from datetime import date

import pandas as pd
import pytest

from src.providers.market_data import YFinanceProvider


class FakeHistoryTicker:
    def __init__(self, frame: pd.DataFrame | None = None, raise_times: int = 0):
        self._frame = frame
        self._raise_times = raise_times
        self.calls = 0

    def history(self, start, end, auto_adjust=False):
        self.calls += 1
        if self.calls <= self._raise_times:
            raise RuntimeError("transient network error")
        return self._frame


def _sample_frame():
    idx = pd.date_range("2026-08-01", periods=3, freq="B")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Adj Close": [100.5, 101.5, 102.5],
            "Volume": [1000, 1100, 1200],
        },
        index=idx,
    )


def test_get_history_returns_normalized_dataframe(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_sample_frame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_history("TCS.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert list(result.columns) == ["date", "open", "high", "low", "close", "adj_close", "volume"]
    assert len(result) == 3


def test_get_history_retries_then_succeeds(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_sample_frame(), raise_times=1)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)
    monkeypatch.setattr("src.providers.market_data.time.sleep", lambda seconds: None)

    provider = YFinanceProvider(max_retries=2, backoff_seconds=0)
    result = provider.get_history("TCS.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert fake_ticker.calls == 2
    assert result is not None


def test_get_history_returns_none_after_exhausting_retries(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_sample_frame(), raise_times=5)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)
    monkeypatch.setattr("src.providers.market_data.time.sleep", lambda seconds: None)

    provider = YFinanceProvider(max_retries=2, backoff_seconds=0)
    result = provider.get_history("TCS.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert result is None
    assert fake_ticker.calls == 3  # initial + 2 retries


def test_get_history_returns_none_for_empty_data(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=pd.DataFrame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_history("BADSYM.NS", date(2026, 8, 1), date(2026, 8, 5))

    assert result is None


def test_default_retry_settings_come_from_config():
    from config.settings import MARKET_DATA_BACKOFF_SECONDS, MARKET_DATA_MAX_RETRIES

    provider = YFinanceProvider()
    assert provider._max_retries == MARKET_DATA_MAX_RETRIES
    assert provider._backoff_seconds == MARKET_DATA_BACKOFF_SECONDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.providers.market_data'`.

- [ ] **Step 3: Implement `src/providers/market_data.py`**

```python
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from config.settings import MARKET_DATA_BACKOFF_SECONDS, MARKET_DATA_MAX_RETRIES

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

_COLUMN_RENAME = {
    "Date": "date", "Open": "open", "High": "high", "Low": "low",
    "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
}


class MarketDataProvider(ABC):
    @abstractmethod
    def get_history(self, symbol: str, start: date, end: date) -> pd.DataFrame | None: ...

    @abstractmethod
    def get_latest_close(self, symbol: str) -> tuple[date, float] | None: ...


class YFinanceProvider(MarketDataProvider):
    def __init__(
        self, max_retries: int = MARKET_DATA_MAX_RETRIES, backoff_seconds: float = MARKET_DATA_BACKOFF_SECONDS,
    ):
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def get_history(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        for attempt in range(self._max_retries + 1):
            try:
                raw = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
                if raw is None or raw.empty:
                    logger.warning("No data returned for %s", symbol)
                    return None
                df = raw.reset_index().rename(columns=_COLUMN_RENAME)
                df["date"] = pd.to_datetime(df["date"]).dt.date
                missing = set(REQUIRED_COLUMNS) - set(df.columns)
                if missing:
                    logger.warning("Symbol %s missing columns %s, skipping", symbol, missing)
                    return None
                return df[REQUIRED_COLUMNS]
            except Exception as exc:
                logger.warning("Attempt %d failed for %s: %s", attempt + 1, symbol, exc)
                if attempt < self._max_retries:
                    time.sleep(self._backoff_seconds * (attempt + 1))
        logger.error("All retries exhausted for %s, skipping", symbol)
        return None

    def get_latest_close(self, symbol: str) -> tuple[date, float] | None:
        history = self.get_history(symbol, start=date.today() - timedelta(days=10), end=date.today())
        if history is None or history.empty:
            return None
        last_row = history.iloc[-1]
        return last_row["date"], float(last_row["close"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_market_data.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Add price_bars caching regression test and confirm it still passes**

Run: `pytest tests/test_db.py -v` (from Task 1 — `cache_price_bars`/`get_price_bars` already covered there; re-run to confirm no regression before moving on).
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/providers/market_data.py tests/test_market_data.py
git commit -m "feat: add yfinance market data provider"
```

---

### Task 5: Fundamentals and news/sentiment provider stubs

**Files:**
- Create: `src/providers/fundamentals.py`
- Create: `src/providers/news.py`
- Test: `tests/test_fundamentals.py`
- Test: `tests/test_news.py`

**Interfaces:**
- Produces: `fundamentals.FundamentalsProvider` (ABC) + `fundamentals.NotAvailableFundamentalsProvider`; `news.NewsSentimentProvider` (ABC) + `news.NotAvailableNewsSentimentProvider`. Not consumed by the Phase 1 pipeline directly — they exist as the deferred-to-Phase-2 interfaces the spec requires (spec §2).

- [ ] **Step 1: Write the failing tests**

`tests/test_fundamentals.py`:
```python
from src.providers.fundamentals import NotAvailableFundamentalsProvider


def test_stub_reports_not_available_and_invents_nothing():
    provider = NotAvailableFundamentalsProvider()
    result = provider.get_fundamentals("TCS.NS")

    assert result["symbol"] == "TCS.NS"
    assert result["status"] == "Not available in Phase 1"
    assert "pe_ratio" not in result
    assert "roe" not in result
```

`tests/test_news.py`:
```python
from src.providers.news import NotAvailableNewsSentimentProvider


def test_stub_reports_not_available_and_invents_nothing():
    provider = NotAvailableNewsSentimentProvider()
    result = provider.get_sentiment("TCS.NS")

    assert result["symbol"] == "TCS.NS"
    assert result["status"] == "Not available in Phase 1"
    assert "sentiment_score" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fundamentals.py tests/test_news.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the stubs**

`src/providers/fundamentals.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod


class FundamentalsProvider(ABC):
    @abstractmethod
    def get_fundamentals(self, symbol: str) -> dict: ...


class NotAvailableFundamentalsProvider(FundamentalsProvider):
    def get_fundamentals(self, symbol: str) -> dict:
        return {"symbol": symbol, "status": "Not available in Phase 1"}
```

`src/providers/news.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod


class NewsSentimentProvider(ABC):
    @abstractmethod
    def get_sentiment(self, symbol: str) -> dict: ...


class NotAvailableNewsSentimentProvider(NewsSentimentProvider):
    def get_sentiment(self, symbol: str) -> dict:
        return {"symbol": symbol, "status": "Not available in Phase 1"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fundamentals.py tests/test_news.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/providers/fundamentals.py src/providers/news.py tests/test_fundamentals.py tests/test_news.py
git commit -m "feat: add fundamentals and news/sentiment provider stubs"
```

---

### Task 6: Indicators and technical score

**Files:**
- Create: `src/analysis/technical_score.py`
- Create: `src/analysis/indicators.py`
- Test: `tests/test_technical_score.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Produces: `technical_score.SCORE_WEIGHTS`; `technical_score.compute_technical_score(close, sma20, sma50, rsi_value, macd_value, macd_signal_value, bb_upper, bb_lower, momentum, volume_trend_value) -> float` (pure, range `[-100, 100]`). `indicators.compute_indicators(df: pd.DataFrame) -> dict` returning keys `sma5, sma10, sma20, sma50, rsi14, macd, macd_signal, macd_histogram, bb_upper, bb_mid, bb_lower, support, resistance, volatility_percent, momentum_roc_percent, volume_trend_percent, trend_direction, technical_score`; `sma50` is `None` when `len(df) < 50`. Used by `src.prediction.engine` (Task 7) and `src.run_daily` (Task 10).

- [ ] **Step 1: Write the failing tests**

`tests/test_technical_score.py`:
```python
from src.analysis.technical_score import compute_technical_score


def test_score_is_within_bounds_for_extreme_bullish_inputs():
    score = compute_technical_score(
        close=120.0, sma20=100.0, sma50=90.0, rsi_value=10.0, macd_value=5.0,
        macd_signal_value=1.0, bb_upper=110.0, bb_lower=90.0, momentum=20.0,
        volume_trend_value=50.0,
    )
    assert -100.0 <= score <= 100.0
    assert score > 0


def test_score_is_within_bounds_for_extreme_bearish_inputs():
    score = compute_technical_score(
        close=80.0, sma20=100.0, sma50=110.0, rsi_value=90.0, macd_value=-5.0,
        macd_signal_value=-1.0, bb_upper=110.0, bb_lower=90.0, momentum=-20.0,
        volume_trend_value=-50.0,
    )
    assert -100.0 <= score <= 100.0
    assert score < 0


def test_score_handles_zero_band_width_without_error():
    score = compute_technical_score(
        close=100.0, sma20=100.0, sma50=None, rsi_value=50.0, macd_value=0.0,
        macd_signal_value=0.0, bb_upper=100.0, bb_lower=100.0, momentum=0.0,
        volume_trend_value=0.0,
    )
    assert -100.0 <= score <= 100.0
```

`tests/test_indicators.py`:
```python
import pandas as pd
import pytest

from src.analysis.indicators import compute_indicators, momentum_roc, rsi, sma


def _frame(closes: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    n = len(closes)
    volumes = volumes or [1000] * n
    idx = pd.bdate_range("2026-06-01", periods=n)
    return pd.DataFrame(
        {
            "date": idx.date,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "adj_close": closes,
            "volume": volumes,
        }
    )


def test_sma_matches_manual_average():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(series, 3)
    assert result.iloc[-1] == pytest.approx((3 + 4 + 5) / 3)


def test_rsi_is_100_for_strictly_increasing_series():
    series = pd.Series([float(i) for i in range(1, 30)])
    result = rsi(series, period=14)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_50_for_flat_series():
    series = pd.Series([100.0] * 30)
    result = rsi(series, period=14)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_momentum_roc_positive_for_rising_series():
    series = pd.Series([float(i) for i in range(1, 30)])
    assert momentum_roc(series, window=10) > 0


def test_compute_indicators_omits_sma50_with_insufficient_history():
    closes = [100.0 + i * 0.5 for i in range(40)]
    result = compute_indicators(_frame(closes))
    assert result["sma50"] is None
    assert result["sma20"] is not None


def test_compute_indicators_includes_sma50_with_enough_history():
    closes = [100.0 + i * 0.5 for i in range(60)]
    result = compute_indicators(_frame(closes))
    assert result["sma50"] is not None


def test_compute_indicators_technical_score_within_bounds():
    closes = [100.0 + i * 0.5 for i in range(60)]
    result = compute_indicators(_frame(closes))
    assert -100.0 <= result["technical_score"] <= 100.0


def test_compute_indicators_bullish_trend_yields_positive_score():
    rising = [100.0 + i * 1.5 for i in range(60)]
    result = compute_indicators(_frame(rising))
    assert result["technical_score"] > 0
    assert result["trend_direction"] == "UP"


def test_compute_indicators_bearish_trend_yields_negative_score():
    falling = [200.0 - i * 1.5 for i in range(60)]
    result = compute_indicators(_frame(falling))
    assert result["technical_score"] < 0
    assert result["trend_direction"] == "DOWN"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_technical_score.py tests/test_indicators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.analysis.technical_score'`.

- [ ] **Step 3: Implement `src/analysis/technical_score.py` and `src/analysis/indicators.py`**

`src/analysis/technical_score.py`:
```python
from __future__ import annotations

SCORE_WEIGHTS = {
    "trend": 0.30,
    "rsi": 0.20,
    "macd": 0.20,
    "bollinger": 0.15,
    "momentum": 0.10,
    "volume": 0.05,
}


def compute_technical_score(
    close: float, sma20: float, sma50: float | None, rsi_value: float,
    macd_value: float, macd_signal_value: float, bb_upper: float, bb_lower: float,
    momentum: float, volume_trend_value: float,
) -> float:
    if sma50 is not None and close > sma20 > sma50:
        trend_signal = 100.0
    elif sma50 is not None and close < sma20 < sma50:
        trend_signal = -100.0
    else:
        trend_signal = max(-100.0, min(100.0, 100.0 * (close - sma20) / sma20))

    rsi_signal = max(-100.0, min(100.0, (50.0 - rsi_value) * 2))
    macd_signal = 100.0 if macd_value > macd_signal_value else -100.0

    band_width = bb_upper - bb_lower
    if band_width > 0:
        position = (close - bb_lower) / band_width
        bollinger_signal = max(-100.0, min(100.0, (0.5 - position) * 200))
    else:
        bollinger_signal = 0.0

    momentum_signal = max(-100.0, min(100.0, momentum * 10))
    volume_signal = max(-100.0, min(100.0, volume_trend_value))

    score = (
        SCORE_WEIGHTS["trend"] * trend_signal
        + SCORE_WEIGHTS["rsi"] * rsi_signal
        + SCORE_WEIGHTS["macd"] * macd_signal
        + SCORE_WEIGHTS["bollinger"] * bollinger_signal
        + SCORE_WEIGHTS["momentum"] * momentum_signal
        + SCORE_WEIGHTS["volume"] * volume_signal
    )
    return round(score, 2)
```

`src/analysis/indicators.py`:
```python
from __future__ import annotations

import pandas as pd

from src.analysis.technical_score import compute_technical_score


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    result = result.mask(avg_loss == 0, 100.0)
    result = result.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    return result


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, window)
    std = series.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def support_resistance(df: pd.DataFrame, window: int = 20) -> tuple[float, float]:
    recent = df.tail(window)
    return float(recent["low"].min()), float(recent["high"].max())


def rolling_volatility(series: pd.Series, window: int = 20) -> float:
    returns = series.pct_change()
    return float(returns.rolling(window=window).std().iloc[-1] * 100)


def momentum_roc(series: pd.Series, window: int = 10) -> float:
    if len(series) <= window:
        return 0.0
    return float((series.iloc[-1] - series.iloc[-window - 1]) / series.iloc[-window - 1] * 100)


def volume_trend(volume: pd.Series, window: int = 20) -> float:
    if len(volume) < window * 2:
        return 0.0
    recent_avg = volume.tail(window).mean()
    prior_avg = volume.tail(window * 2).head(window).mean()
    if prior_avg == 0:
        return 0.0
    return float((recent_avg - prior_avg) / prior_avg * 100)


def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    latest_close = float(close.iloc[-1])

    sma5 = float(sma(close, 5).iloc[-1])
    sma10 = float(sma(close, 10).iloc[-1])
    sma20 = float(sma(close, 20).iloc[-1])
    sma50 = float(sma(close, 50).iloc[-1]) if len(close) >= 50 else None

    rsi14 = float(rsi(close, 14).iloc[-1])
    macd_line, signal_line, histogram = macd(close)
    macd_value = float(macd_line.iloc[-1])
    macd_signal_value = float(signal_line.iloc[-1])
    macd_histogram = float(histogram.iloc[-1])

    bb_upper, bb_mid, bb_lower = bollinger_bands(close)
    bb_upper_value = float(bb_upper.iloc[-1])
    bb_mid_value = float(bb_mid.iloc[-1])
    bb_lower_value = float(bb_lower.iloc[-1])

    support, resistance = support_resistance(df)
    volatility = rolling_volatility(close)
    momentum = momentum_roc(close)
    vol_trend = volume_trend(df["volume"])

    trend_direction = "UP" if latest_close > sma20 else "DOWN"

    technical_score = compute_technical_score(
        latest_close, sma20, sma50, rsi14, macd_value, macd_signal_value,
        bb_upper_value, bb_lower_value, momentum, vol_trend,
    )

    return {
        "sma5": sma5, "sma10": sma10, "sma20": sma20, "sma50": sma50,
        "rsi14": rsi14, "macd": macd_value, "macd_signal": macd_signal_value,
        "macd_histogram": macd_histogram, "bb_upper": bb_upper_value,
        "bb_mid": bb_mid_value, "bb_lower": bb_lower_value,
        "support": support, "resistance": resistance,
        "volatility_percent": volatility, "momentum_roc_percent": momentum,
        "volume_trend_percent": vol_trend, "trend_direction": trend_direction,
        "technical_score": technical_score,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_technical_score.py tests/test_indicators.py -v`
Expected: PASS (3 + 9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/analysis/technical_score.py src/analysis/indicators.py tests/test_technical_score.py tests/test_indicators.py
git commit -m "feat: add technical indicators and deterministic technical score"
```

---

### Task 7: Prediction engine — prompt, Claude call, JSON validation, row building

**Files:**
- Create: `src/prediction/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `config.settings.{CLAUDE_MODEL, PROMPT_VERSION}` (Task 1); `src.storage.db.insert_prediction_rows` (Task 1); `src.universe.trading_calendar.TradingCalendar.add_trading_days` (Task 3).
- Produces: `engine.build_prompt(symbol, current_price, technical_score, indicators) -> str`; `engine.validate_response(raw_text, expected_current_price) -> dict` (raises `PredictionValidationError`); `engine.request_prediction(client, symbol, current_price, technical_score, indicators) -> dict | None`; `engine.build_prediction_rows(symbol, prediction_date, validated, technical_score, indicators, data_timestamp, data_provider, calendar) -> list[dict]`; `engine.insert_predictions(conn, symbol, prediction_date, validated, technical_score, indicators, data_timestamp, data_provider, calendar) -> None`; `engine.HORIZONS = {"1d": 1, "5d": 5, "10d": 10, "20d": 20}`. Used by `src.run_daily` (Task 10).

- [ ] **Step 1: Write the failing tests**

`tests/test_engine.py`:
```python
import json
from datetime import date

import pytest

from src.prediction.engine import (
    PredictionValidationError,
    build_prompt,
    insert_predictions,
    request_prediction,
    validate_response,
)
from src.storage.db import get_connection
from src.universe.trading_calendar import NseStaticHolidayCalendar


def _valid_payload(current_price=1450.0):
    horizon = {
        "direction": "BULLISH", "target_price": 1465, "range_low": 1455,
        "range_high": 1475, "expected_move_percent": 1.03, "risk_level": 1435,
        "confidence": 65, "reasoning": "uptrend", "key_risks": ["macro risk"],
    }
    return {"current_price": current_price, "horizons": {k: dict(horizon) for k in ["1d", "5d", "10d", "20d"]}}


def test_build_prompt_contains_symbol_and_score_not_raw_history():
    prompt = build_prompt("TCS.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert "TCS.NS" in prompt
    assert "42.5" in prompt
    assert "history" not in prompt.lower()


def test_validate_response_accepts_valid_payload():
    result = validate_response(json.dumps(_valid_payload()), expected_current_price=1450.0)
    assert result["horizons"]["1d"]["direction"] == "BULLISH"


def test_validate_response_rejects_invalid_json():
    with pytest.raises(PredictionValidationError):
        validate_response("not json", expected_current_price=1450.0)


def test_validate_response_rejects_missing_horizon():
    payload = _valid_payload()
    del payload["horizons"]["20d"]
    with pytest.raises(PredictionValidationError):
        validate_response(json.dumps(payload), expected_current_price=1450.0)


def test_validate_response_rejects_bad_direction():
    payload = _valid_payload()
    payload["horizons"]["1d"]["direction"] = "UP"
    with pytest.raises(PredictionValidationError):
        validate_response(json.dumps(payload), expected_current_price=1450.0)


def test_validate_response_rejects_range_low_above_range_high():
    payload = _valid_payload()
    payload["horizons"]["1d"]["range_low"] = 1500
    payload["horizons"]["1d"]["range_high"] = 1400
    with pytest.raises(PredictionValidationError):
        validate_response(json.dumps(payload), expected_current_price=1450.0)


def test_validate_response_rejects_confidence_out_of_range():
    payload = _valid_payload()
    payload["horizons"]["1d"]["confidence"] = 150
    with pytest.raises(PredictionValidationError):
        validate_response(json.dumps(payload), expected_current_price=1450.0)


def test_validate_response_rejects_empty_key_risks():
    payload = _valid_payload()
    payload["horizons"]["1d"]["key_risks"] = []
    with pytest.raises(PredictionValidationError):
        validate_response(json.dumps(payload), expected_current_price=1450.0)


def test_validate_response_rejects_mismatched_current_price():
    with pytest.raises(PredictionValidationError):
        validate_response(json.dumps(_valid_payload(current_price=999.0)), expected_current_price=1450.0)


class _FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class _FakeMessages:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = 0

    def create(self, **kwargs):
        text = self._texts[self.calls]
        self.calls += 1
        return _FakeMessage(text)


class _FakeClient:
    def __init__(self, texts):
        self.messages = _FakeMessages(texts)


def test_request_prediction_retries_once_then_succeeds():
    client = _FakeClient(["not json", json.dumps(_valid_payload())])
    result = request_prediction(client, "TCS.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert result is not None
    assert client.messages.calls == 2


def test_request_prediction_returns_none_after_two_failures():
    client = _FakeClient(["not json", "still not json"])
    result = request_prediction(client, "TCS.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert result is None
    assert client.messages.calls == 2


def test_insert_predictions_writes_one_row_per_horizon_and_never_overwrites(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    calendar = NseStaticHolidayCalendar()
    validated = _valid_payload()
    validated["_raw_response"] = json.dumps(validated)

    insert_predictions(conn, "TCS.NS", date(2026, 9, 14), validated, 42.5, {"rsi14": 55.0}, "2026-09-14T00:00:00", "yfinance", calendar)
    rows = conn.execute("SELECT * FROM predictions WHERE symbol = 'TCS.NS'").fetchall()
    assert len(rows) == 4

    insert_predictions(conn, "TCS.NS", date(2026, 9, 14), validated, 42.5, {"rsi14": 55.0}, "2026-09-14T00:00:00", "yfinance", calendar)
    rows_after_second_call = conn.execute("SELECT * FROM predictions WHERE symbol = 'TCS.NS'").fetchall()
    assert len(rows_after_second_call) == 8  # append-only: no overwrite, no upsert
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.prediction.engine'`.

- [ ] **Step 3: Implement `src/prediction/engine.py`**

```python
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from config.settings import CLAUDE_MODEL, PROMPT_VERSION
from src.storage import db

logger = logging.getLogger(__name__)

HORIZONS = {"1d": 1, "5d": 5, "10d": 10, "20d": 20}
ALLOWED_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}
REQUIRED_HORIZON_FIELDS = {
    "direction", "target_price", "range_low", "range_high",
    "expected_move_percent", "risk_level", "confidence", "reasoning", "key_risks",
}


class PredictionValidationError(Exception):
    pass


def build_prompt(symbol: str, current_price: float, technical_score: float, indicators: dict[str, Any]) -> str:
    indicators_summary = json.dumps(indicators, indent=2, default=str)
    return f"""You are a technical analysis assistant for Indian equity markets.

Stock: {symbol}
Current price: {current_price}
Technical score (-100 bearish to +100 bullish): {technical_score}
Indicators:
{indicators_summary}

Using only the data above, predict price direction for these horizons in
trading days: 1d, 5d, 10d, 20d. Do not invent data not given above.

Respond with STRICT JSON only, no text outside the JSON, in exactly this shape:
{{
  "current_price": {current_price},
  "horizons": {{
    "1d": {{"direction": "BULLISH|BEARISH|NEUTRAL", "target_price": number, "range_low": number, "range_high": number, "expected_move_percent": number, "risk_level": number, "confidence": number (0-100), "reasoning": "string", "key_risks": ["string", ...]}},
    "5d": {{ ... same shape ... }},
    "10d": {{ ... same shape ... }},
    "20d": {{ ... same shape ... }}
  }}
}}"""


def validate_response(raw_text: str, expected_current_price: float) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PredictionValidationError(f"invalid JSON: {exc}") from exc

    if "current_price" not in data or "horizons" not in data:
        raise PredictionValidationError("missing current_price or horizons key")

    if abs(float(data["current_price"]) - expected_current_price) > max(0.01 * expected_current_price, 0.5):
        raise PredictionValidationError("current_price echoed back does not match sent value")

    horizons = data["horizons"]
    missing_horizons = set(HORIZONS) - set(horizons)
    if missing_horizons:
        raise PredictionValidationError(f"missing horizon keys: {missing_horizons}")

    for key, horizon in horizons.items():
        missing_fields = REQUIRED_HORIZON_FIELDS - set(horizon)
        if missing_fields:
            raise PredictionValidationError(f"horizon {key} missing fields: {missing_fields}")
        if horizon["direction"] not in ALLOWED_DIRECTIONS:
            raise PredictionValidationError(f"horizon {key} invalid direction: {horizon['direction']}")
        if not (float(horizon["range_low"]) <= float(horizon["range_high"])):
            raise PredictionValidationError(f"horizon {key} range_low > range_high")
        confidence = float(horizon["confidence"])
        if not (0 <= confidence <= 100):
            raise PredictionValidationError(f"horizon {key} confidence out of range: {confidence}")
        key_risks = horizon["key_risks"]
        if not isinstance(key_risks, list) or not key_risks or not all(isinstance(r, str) for r in key_risks):
            raise PredictionValidationError(f"horizon {key} key_risks must be a non-empty list of strings")

    return data


def request_prediction(client, symbol: str, current_price: float, technical_score: float, indicators: dict[str, Any]) -> dict[str, Any] | None:
    prompt = build_prompt(symbol, current_price, technical_score, indicators)
    for attempt in range(2):
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        try:
            validated = validate_response(raw_text, current_price)
            validated["_raw_response"] = raw_text
            return validated
        except PredictionValidationError as exc:
            logger.warning("Validation failed for %s (attempt %d): %s\nRaw response: %s", symbol, attempt + 1, exc, raw_text)
    logger.error("Skipping %s: Claude response failed validation twice", symbol)
    return None


def build_prediction_rows(
    symbol: str, prediction_date: date, validated: dict[str, Any], technical_score: float,
    indicators: dict[str, Any], data_timestamp: str, data_provider: str, calendar,
) -> list[dict]:
    created_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for horizon_key, horizon_days in HORIZONS.items():
        h = validated["horizons"][horizon_key]
        target_eval_date = calendar.add_trading_days(prediction_date, horizon_days)
        rows.append({
            "created_at": created_at, "prediction_date": prediction_date.isoformat(), "symbol": symbol,
            "horizon_days": horizon_days, "target_evaluation_date": target_eval_date.isoformat(),
            "current_price": validated["current_price"], "direction": h["direction"],
            "target_price": h["target_price"], "range_low": h["range_low"], "range_high": h["range_high"],
            "expected_move_percent": h["expected_move_percent"], "risk_level": h["risk_level"],
            "confidence": h["confidence"], "reasoning": h["reasoning"],
            "key_risks_json": json.dumps(h["key_risks"]), "technical_score": technical_score,
            "indicators_json": json.dumps(indicators, default=str), "data_timestamp": data_timestamp,
            "data_provider": data_provider, "claude_model": CLAUDE_MODEL, "prompt_version": PROMPT_VERSION,
            "raw_claude_response": validated["_raw_response"],
        })
    return rows


def insert_predictions(
    conn, symbol: str, prediction_date: date, validated: dict[str, Any], technical_score: float,
    indicators: dict[str, Any], data_timestamp: str, data_provider: str, calendar,
) -> None:
    rows = build_prediction_rows(
        symbol, prediction_date, validated, technical_score, indicators, data_timestamp, data_provider, calendar,
    )
    db.insert_prediction_rows(conn, rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/prediction/engine.py tests/test_engine.py
git commit -m "feat: add Claude prediction engine with strict JSON validation"
```

---

### Task 8: Accuracy evaluator

**Files:**
- Create: `src/accuracy/scorer.py`
- Test: `tests/test_scorer.py`

**Interfaces:**
- Consumes: `predictions` and `price_bars` tables via `src.storage.db.get_price_bars` (Task 1, 4, 7).
- Produces: `scorer.find_predictions_due_for_evaluation(conn, as_of: date) -> list[sqlite3.Row]`; `scorer.evaluate_prediction(conn, prediction) -> dict | None`; `scorer.insert_accuracy_evaluation(conn, evaluation: dict) -> None` (thin wrapper over `src.storage.db.insert_accuracy_evaluation`); `scorer.run_accuracy_evaluation(conn, as_of: date) -> int` (returns count evaluated). Used by `src.run_daily` (Task 10).

- [ ] **Step 1: Write the failing tests**

`tests/test_scorer.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.accuracy.scorer'`.

- [ ] **Step 3: Implement `src/accuracy/scorer.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scorer.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/accuracy/scorer.py tests/test_scorer.py
git commit -m "feat: add accuracy evaluator with no-look-ahead-bias guard"
```

---

### Task 9: Excel report generator

**Files:**
- Create: `src/reporting/excel_report.py`
- Test: `tests/test_excel_report.py`

**Interfaces:**
- Consumes: `config.settings.REPORTS_DIR` (Task 1); `predictions`, `accuracy_evaluations`, `price_bars` tables (read-only, direct SQL — report-specific projections, deliberately not routed through generic storage accessors).
- Produces: `excel_report.generate_report(conn, run_date: date, symbols: list[str], predictions_made: int, predictions_skipped: int, output_dir: Path = REPORTS_DIR) -> Path`. Used by `src.run_daily` (Task 10).

- [ ] **Step 1: Write the failing test**

`tests/test_excel_report.py`:
```python
from datetime import date

import openpyxl

from src.reporting.excel_report import generate_report
from src.storage.db import get_connection

EXPECTED_SHEETS = {
    "Dashboard", "Today's Predictions", "Prediction History", "Accuracy",
    "Stock Performance", "Historical Prices", "News-Sentiment", "Configuration",
}


def _seed_minimal_data(conn):
    conn.execute(
        "INSERT INTO predictions (created_at, prediction_date, symbol, horizon_days, "
        "target_evaluation_date, current_price, direction, target_price, range_low, "
        "range_high, expected_move_percent, risk_level, confidence, reasoning, "
        "key_risks_json, technical_score, indicators_json, data_timestamp, "
        "data_provider, claude_model, prompt_version, raw_claude_response) VALUES "
        "('2026-09-14T00:00:00', '2026-09-14', 'TCS.NS', 1, '2026-09-15', 1000.0, "
        "'BULLISH', 1010.0, 1005.0, 1015.0, 1.0, 990.0, 70.0, 'test', '[]', 50.0, "
        "'{}', '2026-09-14T00:00:00', 'yfinance', 'claude-sonnet-5', 'v1', '{}')"
    )
    conn.execute(
        "INSERT INTO price_bars (symbol, date, open, high, low, close, adj_close, volume, source, fetched_at) "
        "VALUES ('TCS.NS', '2026-09-14', 1000, 1010, 995, 1005, 1005, 1000, 'yfinance', '2026-09-14T00:00:00')"
    )
    conn.commit()


def test_generate_report_creates_all_expected_sheets(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _seed_minimal_data(conn)

    output_path = generate_report(
        conn, date(2026, 9, 14), ["TCS.NS"], predictions_made=1, predictions_skipped=0,
        output_dir=tmp_path / "reports",
    )

    assert output_path.exists()
    workbook = openpyxl.load_workbook(output_path)
    assert set(workbook.sheetnames) == EXPECTED_SHEETS
    dashboard = workbook["Dashboard"]
    assert dashboard.cell(row=1, column=1).value == "run_date"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_excel_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.reporting.excel_report'`.

- [ ] **Step 3: Implement `src/reporting/excel_report.py`**

```python
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from config.settings import REPORTS_DIR

DISCLAIMER = (
    "This report is generated by an automated technical-analysis tool for "
    "research and educational purposes only. It is NOT investment advice. "
    "Past accuracy does not guarantee future performance. Consult a "
    "registered investment advisor before making financial decisions."
)

PHASE_LABEL = "Phase 1"
INDICATOR_VERSION = "v1"
PROMPT_VERSION = "v1"
DATA_PROVIDER = "yfinance"


def _dashboard_df(conn: sqlite3.Connection, run_date: str, universe_size: int, made: int, skipped: int) -> pd.DataFrame:
    accuracy = pd.read_sql_query("SELECT direction_correct, target_hit, pct_error FROM accuracy_evaluations", conn)
    direction_accuracy = accuracy["direction_correct"].mean() * 100 if not accuracy.empty else None
    target_hit_rate = accuracy["target_hit"].mean() * 100 if not accuracy.empty else None
    avg_error = accuracy["pct_error"].mean() if not accuracy.empty else None
    return pd.DataFrame([{
        "run_date": run_date,
        "universe_size": universe_size,
        "predictions_made": made,
        "predictions_skipped": skipped,
        "direction_accuracy_percent": direction_accuracy,
        "target_hit_rate_percent": target_hit_rate,
        "avg_error_percent": avg_error,
        "disclaimer": DISCLAIMER,
    }])


def _todays_predictions_df(conn: sqlite3.Connection, run_date: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT symbol, horizon_days, direction, target_price, range_low, range_high, "
        "expected_move_percent, confidence, reasoning FROM predictions WHERE prediction_date = ? "
        "ORDER BY symbol, horizon_days", conn, params=(run_date,),
    )


def _prediction_history_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM predictions ORDER BY prediction_date, symbol, horizon_days", conn)


def _accuracy_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT a.*, p.symbol, p.horizon_days, p.direction AS predicted_direction, p.confidence "
        "FROM accuracy_evaluations a JOIN predictions p ON p.id = a.prediction_id "
        "ORDER BY a.evaluation_date", conn,
    )


def _stock_performance_df(conn: sqlite3.Connection) -> pd.DataFrame:
    df = _accuracy_df(conn)
    if df.empty:
        return pd.DataFrame(columns=["symbol", "direction_accuracy_percent", "target_hit_rate_percent", "avg_pct_error", "n_evaluations"])
    grouped = df.groupby("symbol").agg(
        direction_accuracy_percent=("direction_correct", lambda s: s.mean() * 100),
        target_hit_rate_percent=("target_hit", lambda s: s.mean() * 100),
        avg_pct_error=("pct_error", "mean"),
        n_evaluations=("prediction_id", "count"),
    ).reset_index()
    return grouped


def _historical_prices_df(conn: sqlite3.Connection, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"])
    placeholders = ",".join("?" for _ in symbols)
    return pd.read_sql_query(
        f"SELECT symbol, date, open, high, low, close, adj_close, volume FROM price_bars "
        f"WHERE symbol IN ({placeholders}) ORDER BY symbol, date", conn, params=symbols,
    )


def _news_sentiment_placeholder_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "headline", "sentiment_score", "source", "date"])


def _configuration_df(run_date: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "universe_methodology": "Static NIFTY-50 weights, top 20 by weight",
        "indicator_score_version": INDICATOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "data_provider": DATA_PROVIDER,
        "generation_timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": PHASE_LABEL,
        "run_date": run_date,
    }])


def generate_report(
    conn: sqlite3.Connection, run_date: date, symbols: list[str],
    predictions_made: int, predictions_skipped: int, output_dir: Path = REPORTS_DIR,
) -> Path:
    # Sheet named "News/Sentiment (placeholder)" in the spec is written as
    # "News-Sentiment" here because "/" is not a legal Excel sheet-name character.
    output_dir.mkdir(parents=True, exist_ok=True)
    run_date_str = run_date.isoformat()
    output_path = output_dir / f"{run_date_str}.xlsx"

    sheets = {
        "Dashboard": _dashboard_df(conn, run_date_str, len(symbols), predictions_made, predictions_skipped),
        "Today's Predictions": _todays_predictions_df(conn, run_date_str),
        "Prediction History": _prediction_history_df(conn),
        "Accuracy": _accuracy_df(conn),
        "Stock Performance": _stock_performance_df(conn),
        "Historical Prices": _historical_prices_df(conn, symbols),
        "News-Sentiment": _news_sentiment_placeholder_df(),
        "Configuration": _configuration_df(run_date_str),
    }

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

    return output_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_excel_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reporting/excel_report.py tests/test_excel_report.py
git commit -m "feat: add 8-sheet Excel report generator"
```

---

### Task 10: CLI entrypoint (`run_daily`)

**Files:**
- Create: `src/run_daily.py`
- Test: `tests/test_run_daily.py`

**Interfaces:**
- Consumes: `config.settings.{LOGS_DIR, REPORTS_DIR, MARKET_DATA_LOOKBACK_DAYS, TOP_N}`, `src.storage.db`, `src.universe.selection.{select_top_n, record_universe_snapshot}`, `src.universe.trading_calendar.NseStaticHolidayCalendar`, `src.universe.nifty50_weights.NIFTY50_WEIGHTS`, `src.providers.market_data.YFinanceProvider`, `src.analysis.indicators.compute_indicators`, `src.prediction.engine.{request_prediction, insert_predictions}`, `src.accuracy.scorer.run_accuracy_evaluation`, `src.reporting.excel_report.generate_report`.
- Produces: `run_daily.run_pipeline(symbols, run_date, dry_run, conn, provider, client, calendar, output_dir=REPORTS_DIR) -> dict` with keys `made, skipped, evaluated, report_path`; `run_daily.main(argv=None) -> int` (CLI entrypoint, exit code).

- [ ] **Step 1: Write the failing tests**

`tests/test_run_daily.py`:
```python
import json
from datetime import date

import pandas as pd
import pytest

from src.run_daily import run_pipeline
from src.storage.db import get_connection
from src.universe.trading_calendar import NseStaticHolidayCalendar


def _fake_history(n=60, start_price=1000.0):
    idx = pd.bdate_range("2026-06-01", periods=n)
    closes = [start_price + i for i in range(n)]
    return pd.DataFrame({
        "date": idx.date, "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes, "adj_close": closes,
        "volume": [1000 + i for i in range(n)],
    })


class _FakeProvider:
    def __init__(self, frame):
        self._frame = frame

    def get_history(self, symbol, start, end):
        return self._frame

    def get_latest_close(self, symbol):
        return date.today(), float(self._frame.iloc[-1]["close"])


def _valid_payload(current_price):
    horizon = {
        "direction": "BULLISH", "target_price": current_price * 1.01, "range_low": current_price * 0.99,
        "range_high": current_price * 1.02, "expected_move_percent": 1.0, "risk_level": current_price * 0.97,
        "confidence": 65, "reasoning": "uptrend", "key_risks": ["macro risk"],
    }
    return {"current_price": current_price, "horizons": {k: dict(horizon) for k in ["1d", "5d", "10d", "20d"]}}


class _FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class _FakeMessages:
    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        price_line = [line for line in prompt.splitlines() if line.startswith("Current price:")][0]
        current_price = float(price_line.split(":")[1].strip())
        return _FakeMessage(json.dumps(_valid_payload(current_price)))


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_dry_run_writes_nothing_to_db_or_reports(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    provider = _FakeProvider(_fake_history())
    client = _FakeClient()
    calendar = NseStaticHolidayCalendar()

    result = run_pipeline(
        ["TCS.NS"], date(2026, 9, 14), dry_run=True, conn=conn, provider=provider,
        client=client, calendar=calendar, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["report_path"] is None
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    assert not (tmp_path / "reports").exists()
    conn.close()


def test_full_run_stores_predictions_and_writes_report(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    provider = _FakeProvider(_fake_history())
    client = _FakeClient()
    calendar = NseStaticHolidayCalendar()

    result = run_pipeline(
        ["TCS.NS"], date(2026, 9, 14), dry_run=False, conn=conn, provider=provider,
        client=client, calendar=calendar, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 0
    assert result["report_path"].exists()
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0] == 60
    assert conn.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 1
    conn.close()


def test_symbol_with_no_market_data_is_skipped_without_stopping_others(tmp_path):
    class _PartialProvider:
        def get_history(self, symbol, start, end):
            return None if symbol == "BADSYM.NS" else _fake_history()

        def get_latest_close(self, symbol):
            return date.today(), 1000.0

    conn = get_connection(tmp_path / "test.db")
    result = run_pipeline(
        ["BADSYM.NS", "TCS.NS"], date(2026, 9, 14), dry_run=False, conn=conn,
        provider=_PartialProvider(), client=_FakeClient(), calendar=NseStaticHolidayCalendar(),
        output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 1
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_daily.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.run_daily'`.

- [ ] **Step 3: Implement `src/run_daily.py`**

```python
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from config.settings import LOGS_DIR, MARKET_DATA_LOOKBACK_DAYS, REPORTS_DIR, TOP_N
from src.accuracy.scorer import run_accuracy_evaluation
from src.analysis.indicators import compute_indicators
from src.prediction.engine import insert_predictions, request_prediction
from src.providers.market_data import YFinanceProvider
from src.reporting.excel_report import generate_report
from src.storage import db
from src.universe.nifty50_weights import NIFTY50_WEIGHTS
from src.universe.selection import record_universe_snapshot, select_top_n
from src.universe.trading_calendar import NseStaticHolidayCalendar


def setup_logging(run_date: date) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / f"{run_date.isoformat()}.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="India stock market analysis agent - daily run")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols to limit the universe, e.g. RELIANCE.NS,TCS.NS")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline including Claude calls but write nothing to SQLite or reports/")
    return parser.parse_args(argv)


def run_pipeline(
    symbols: list[str], run_date: date, dry_run: bool, conn, provider, client, calendar,
    output_dir: Path = REPORTS_DIR,
) -> dict:
    logger = logging.getLogger("run_daily.pipeline")

    if not dry_run:
        selection = [(s, NIFTY50_WEIGHTS.get(s, 0.0), i + 1) for i, s in enumerate(symbols)]
        record_universe_snapshot(conn, run_date.isoformat(), selection)

    made = 0
    skipped = 0
    for symbol in symbols:
        start = run_date - timedelta(days=MARKET_DATA_LOOKBACK_DAYS)
        history = provider.get_history(symbol, start=start, end=run_date)
        if history is None or history.empty:
            logger.warning("Skipping %s: no market data", symbol)
            skipped += 1
            continue

        if not dry_run:
            db.cache_price_bars(conn, symbol, history, "yfinance")

        indicators = compute_indicators(history)
        current_price = float(history.iloc[-1]["close"])

        prediction = request_prediction(client, symbol, current_price, indicators["technical_score"], indicators)
        if prediction is None:
            skipped += 1
            continue

        if not dry_run:
            insert_predictions(
                conn, symbol, run_date, prediction, indicators["technical_score"], indicators,
                datetime.now(timezone.utc).isoformat(), "yfinance", calendar,
            )
        made += 1
        logger.info("Prediction generated for %s (dry_run=%s)", symbol, dry_run)

    result = {"made": made, "skipped": skipped, "evaluated": 0, "report_path": None}
    if not dry_run:
        result["evaluated"] = run_accuracy_evaluation(conn, run_date)
        result["report_path"] = generate_report(conn, run_date, symbols, made, skipped, output_dir=output_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    run_date = date.today()
    setup_logging(run_date)
    logger = logging.getLogger("run_daily")

    calendar = NseStaticHolidayCalendar()
    if not calendar.is_trading_day(run_date):
        logger.info("%s is not a trading day, exiting cleanly", run_date.isoformat())
        return 0

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = [symbol for symbol, _, _ in select_top_n(NIFTY50_WEIGHTS, TOP_N)]

    conn = db.get_connection()
    provider = YFinanceProvider()
    client = anthropic.Anthropic()

    result = run_pipeline(symbols, run_date, args.dry_run, conn, provider, client, calendar)
    logger.info(
        "Run complete: %d made, %d skipped, %d evaluated",
        result["made"], result["skipped"], result["evaluated"],
    )
    if result["report_path"]:
        logger.info("Report written to %s", result["report_path"])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_run_daily.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests across every task pass with zero failures.

- [ ] **Step 6: Commit**

```bash
git add src/run_daily.py tests/test_run_daily.py
git commit -m "feat: wire pipeline into CLI entrypoint python -m src.run_daily"
```

---

### Task 11: README

**Files:**
- Create: `README.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Write `README.md`**

Cover, in this order, each drawn directly from the spec and the code written in Tasks 1–10:
1. **Overview & disclaimer** — one paragraph, plus the exact disclaimer text used in `src/reporting/excel_report.py::DISCLAIMER`, stating this is not investment advice.
2. **Project layout** — reproduce the canonical tree from this plan's "Canonical project architecture" section and the one-line module-responsibility table, so the README stays the single place a new contributor reads to understand where things live.
3. **Setup** — `pip install -r requirements.txt`, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. State explicitly that `config/settings.py` holds only non-secret paths/tunables and must never contain a key.
4. **How to run** — the three CLI invocations from spec §11 (`python -m src.run_daily`, `--symbols`, `--dry-run`, and the combined form), and what each does/writes.
5. **Universe methodology** — static top-20-by-weight selection from `src/universe/nifty50_weights.py`; note it's approximate and manually refreshed; explain how/when to refresh it (edit the dict, re-run — historical snapshots stay reproducible via `universe_snapshots`).
6. **Trading calendar** — how `NseStaticHolidayCalendar` works and the note to refresh `src/universe/nse_holidays.py` every January.
7. **Indicators & technical score** — list each indicator computed in `src/analysis/indicators.py` and the exact weight formula/weights from `src/analysis/technical_score.py::SCORE_WEIGHTS`.
8. **Prediction contract** — the JSON shape from spec §7, validation rules, and the skip-on-double-failure behavior.
9. **Storage schema** — the four SQLite tables, that `src/storage/db.py` is the only module that writes SQL, and that tables are append-only.
10. **Accuracy methodology** — what `accuracy_evaluations` fields mean and how they're computed (§8/§9 of the spec).
11. **Excel report** — list all 8 sheets and what's in each (note the `News-Sentiment` sheet name vs. the spec's `News/Sentiment (placeholder)` — `/` isn't legal in Excel sheet names).
12. **Extending each provider interface** — how to add a Phase 2 `MarketDataProvider`, `FundamentalsProvider`, `NewsSentimentProvider`, or `TradingCalendar` implementation under `src/providers/` or `src/universe/` without touching `analysis/`, `prediction/`, `storage/`, or `reporting/`.
13. **Known limitations** — no automation/`launchd` yet, static universe, no real fundamentals/news, single free data provider with no SLA, manual holiday list maintenance.
14. **Running tests** — `pytest -v`.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with methodology, run instructions, and limitations"
```

---

### Task 12: End-to-end verification run and Phase 1 completion summary

**Files:** None created — this task exercises the real system end-to-end and records results for the user.

**Interfaces:** None (verification only).

- [ ] **Step 1: Run the full unit test suite one more time**

Run: `pytest -v`
Expected: all tests pass (this is the final regression check before touching live systems).

- [ ] **Step 2: Confirm `ANTHROPIC_API_KEY` is available**

Check `.env` exists and is git-ignored (`git check-ignore .env` should print `.env`). If no key is available, stop here and report to the user that live verification is blocked pending a key — do not fabricate results.

- [ ] **Step 3: Run a cheap real end-to-end test**

```bash
python -m src.run_daily --dry-run --symbols RELIANCE.NS
```
Confirm: exits 0, log file appears in `logs/`, log shows a real Claude call succeeded and validated, and confirms nothing was written to `data/stock_agent.db` or `reports/` (dry-run).

- [ ] **Step 4: Run a real full-write run against a small symbol set**

```bash
python -m src.run_daily --symbols RELIANCE.NS,TCS.NS
```
Confirm via `sqlite3 data/stock_agent.db`: one `universe_snapshots` row per symbol, `price_bars` populated, 4 `predictions` rows per symbol (8 total), and `reports/YYYY-MM-DD.xlsx` exists with all 8 sheets populated for "Today's Predictions" and "Prediction History".

- [ ] **Step 5: Demonstrate the accuracy evaluator on a backdated prediction**

Manually insert one backdated `predictions` row (`prediction_date` several trading days in the past, `target_evaluation_date` already elapsed) using the same INSERT shape as `tests/test_scorer.py::_insert_prediction`, run `python -m src.run_daily --symbols TCS.NS` again, and confirm a matching `accuracy_evaluations` row was created — proving the evaluator populates real outcomes without needing to wait multiple calendar days.

- [ ] **Step 6: Record measured Claude token usage**

From the Anthropic API response objects logged/observed during Step 3–4 (`response.usage.input_tokens` / `output_tokens`), compute per-call and per-20-stock-run token/cost estimates.

- [ ] **Step 7: Report Phase 1 completion**

Walk the checklist in spec §15 point by point with evidence (log excerpts, a sample `predictions` row, a sample `accuracy_evaluations` row, sheet names, test output, measured token usage) and present it to the user for review before any Phase 2 work begins.

---

## Self-review notes

- **Spec coverage:** §1 (scope/stubs) → Task 5; §2 (architecture) → Tasks 1–10 wiring, now nested under `providers/universe/analysis/prediction/storage/accuracy/reporting`; §3 (universe) → Task 2; §4 (calendar) → Task 3; §5 (market data + cache) → Task 4 + `storage/db.py` in Task 1; §6 (indicators/score) → Task 6, split into `analysis/indicators.py` + `analysis/technical_score.py`; §7 (prediction engine/validation) → Task 7; §8 (schema, append-only, target_evaluation_date fixed at creation) → Tasks 1, 7; §9 (evaluator, no-look-ahead) → Task 8; §10 (8-sheet Excel) → Task 9; §11 (CLI/manual execution/trading-day check) → Task 10; §12 (per-symbol error isolation, logging, no secrets) → Task 10, `.gitignore`, `config/settings.py`; §13 (unit tests, mocked network/Claude) → all tasks; §14 (cost control: 1 call/stock, compact prompt) → Task 7; §15 (completion criteria) → Task 12.
- **Reconciliation with 2026-09-13 user directive:** every table write now goes through `src/storage/db.py` (`insert_universe_snapshot_rows`, `cache_price_bars`, `insert_prediction_rows`, `insert_accuracy_evaluation`) — `selection.py`, `engine.py`, and `scorer.py` build data and delegate, never issuing their own INSERT statements. `config/settings.py` holds only paths and tunables, explicitly documented as never containing secrets; `.env` remains the only secret store and stays git-ignored. Every nested package has an `__init__.py` (Task 1). Providers are consumed only via their ABC + dependency injection in `run_daily.run_pipeline`, so a new Phase 2 provider never requires touching `analysis/`, `prediction/`, `storage/`, or `reporting/`.
- **Placeholder scan:** no TBD/"add appropriate handling"/unshown code remains; every step has literal code or literal shell commands.
- **Type consistency:** `run_pipeline` signature (`symbols, run_date, dry_run, conn, provider, client, calendar, output_dir`) matches its Task 10 test calls; `insert_predictions(conn, symbol, prediction_date, validated, technical_score, indicators, data_timestamp, data_provider, calendar)` in `src/prediction/engine.py` matches both Task 7 and Task 10 call sites and internally delegates to `db.insert_prediction_rows`; `compute_indicators` return dict keys are the same set used by `engine.build_prompt`'s `indicators` param and by `excel_report`/`run_daily`; `db.get_price_bars(..., start_exclusive=False)` in Task 1 is reused as-is by `scorer.evaluate_prediction` in Task 8 with `start_exclusive=True` — no second implementation of the same query.
