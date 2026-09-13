# India Stock Market Analysis Agent — Phase 2 Implementation Plan (Intraday Hourly System)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent intraday hourly prediction/accuracy system for the same NIFTY-50 Top-20 universe, running once per hour during NSE trading hours, without touching any Phase 1 daily code path.

**Architecture:** New sibling modules alongside the existing daily ones (`src/prediction/intraday_engine.py`, `src/accuracy/intraday_scorer.py`, `src/reporting/intraday_excel_report.py`, `src/universe/intraday_calendar.py`, `src/analysis/intraday_indicators.py`, `src/run_intraday.py`). Three additive tables in the same SQLite file. `MarketDataProvider` gains one new method; `config/settings.py` gains new constants. Every existing Phase 1 module and its 68 tests remain untouched and must keep passing throughout.

**Tech Stack:** Same as Phase 1 — Python 3.11+, yfinance, pandas, openpyxl, sqlite3, anthropic SDK, pytest, python-dotenv. No new dependencies (Python's stdlib `zoneinfo` covers IST timezone handling).

**Spec:** `docs/superpowers/specs/2026-09-13-india-stock-agent-phase2-intraday-design.md`

## Global Constraints

- Daily prediction code paths (`engine.py`, `scorer.py`, `excel_report.py`, `run_daily.py`, `indicators.py`, `technical_score.py`, `trading_calendar.py`, `selection.py`, `nifty50_weights.py`) are **never modified** by this plan. All 68 existing tests must pass, unchanged, after every task.
- Checkpoint grid: `09:15, 10:15, 11:15, 12:15, 13:15, 14:15, 15:15` IST — empirically verified (spec §2). Six of these generate a prediction (09:15–14:15); **15:15 is evaluation-only** and must never call the Claude prediction function (spec §5).
- No-look-ahead rule: at checkpoint T, only bars with `timestamp < T` may be read (spec §7). This lives in exactly one function, `select_bars_up_to_checkpoint`.
- `raw_current_price` and all OHLCV values come from the market-data provider **only**, captured before any Claude call, and are never overwritten by Claude's output (spec §8). This is a deliberate correction from Phase 1's daily engine, where the parked design used Claude's echoed price — intraday does this correctly from the start per explicit user requirement.
- One Claude call per stock per prediction-generating checkpoint: 6 × 20 = 120 calls/day (spec §15). No Claude calls anywhere else (accuracy, indicators, Excel, calendar checks are pure Python).
- `src/storage/db.py` remains the only module that writes SQL (same rule as Phase 1); `intraday_scorer.py` and `intraday_excel_report.py` may issue their own read-only `SELECT`s, mirroring the approved Phase 1 exception.
- Duplicate protection: `UNIQUE(symbol, prediction_timestamp, prediction_type)` on `intraday_predictions`, enforced via `INSERT OR IGNORE` + rowcount check at the call site (spec §4, §14).
- Excel: exactly **one worksheet**, atomic write (temp file + rename), regenerated fresh from SQLite on every run (spec §13, §14).
- Unit tests never make live network/Claude calls (same discipline as Phase 1's 68 tests).
- Every symbol is processed independently; one symbol's failure never stops the other 19 (spec §14).

---

## File structure (additions only)

```
india-stock-agent/
├── launchd/
│   └── com.stockagent.intraday.plist        [NEW]
├── config/settings.py                        [MODIFIED]
├── src/
│   ├── run_intraday.py                       [NEW]
│   ├── providers/market_data.py              [MODIFIED]
│   ├── universe/intraday_calendar.py         [NEW]
│   ├── analysis/intraday_indicators.py       [NEW]
│   ├── prediction/intraday_engine.py         [NEW]
│   ├── storage/db.py                         [MODIFIED]
│   ├── accuracy/intraday_scorer.py           [NEW]
│   └── reporting/intraday_excel_report.py    [NEW]
└── tests/
    ├── test_db.py                             [EXTENDED]
    ├── test_market_data.py                    [EXTENDED]
    ├── test_intraday_calendar.py              [NEW]
    ├── test_intraday_indicators.py            [NEW]
    ├── test_intraday_engine.py                [NEW]
    ├── test_intraday_scorer.py                [NEW]
    ├── test_intraday_excel_report.py           [NEW]
    ├── test_run_intraday.py                    [NEW]
    └── test_launchd_plist.py                   [NEW]
```

---

### Task 1: Config settings + SQLite schema/migration

**Files:**
- Modify: `config/settings.py`
- Modify: `src/storage/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `config.settings.{INTRADAY_INTERVAL, INTRADAY_LOOKBACK_DAYS, INTRADAY_CHECKPOINTS, INTRADAY_LAUNCHD_BUFFER_MINUTES, INTRADAY_REPORTS_DIR, INTRADAY_PROMPT_VERSION}`. `db.insert_intraday_price_bar_rows(conn, rows: list[dict]) -> None`; `db.get_intraday_price_bars(conn, symbol, interval, start=None, end=None, start_exclusive=False) -> pd.DataFrame`; `db.insert_intraday_prediction_row(conn, row: dict) -> int` (rowcount: 1 inserted, 0 duplicate-ignored); `db.insert_intraday_accuracy_evaluation(conn, evaluation: dict) -> int` (same rowcount contract).

- [ ] **Step 1: Add intraday constants to `config/settings.py`**

Append to the existing file:
```python

# Intraday (Phase 2)
INTRADAY_INTERVAL = "60m"
INTRADAY_LOOKBACK_DAYS = 20
INTRADAY_CHECKPOINTS = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
INTRADAY_LAUNCHD_BUFFER_MINUTES = 10
INTRADAY_REPORTS_DIR = Path("reports/intraday")
INTRADAY_PROMPT_VERSION = "intraday-v1"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_db.py`:
```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v -k intraday`
Expected: FAIL — `sqlite3.OperationalError: no such table: intraday_price_bars` (or `AttributeError` for the missing functions).

- [ ] **Step 4: Add the schema and CRUD functions to `src/storage/db.py`**

Add to the `SCHEMA` string (before the closing `"""`):
```sql

CREATE TABLE IF NOT EXISTS intraday_price_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    interval TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(symbol, timestamp, interval)
);

CREATE TABLE IF NOT EXISTS intraday_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    prediction_timestamp TEXT NOT NULL,
    evaluation_timestamp TEXT NOT NULL,
    prediction_type TEXT NOT NULL,
    raw_current_price REAL NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    direction TEXT NOT NULL, predicted_price REAL NOT NULL,
    expected_move_percent REAL NOT NULL, confidence REAL NOT NULL,
    reasoning TEXT NOT NULL, key_risks_json TEXT NOT NULL,
    technical_score REAL NOT NULL, indicators_json TEXT NOT NULL,
    data_provider TEXT NOT NULL, interval TEXT NOT NULL,
    claude_model TEXT NOT NULL, prompt_version TEXT NOT NULL, raw_claude_response TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    UNIQUE(symbol, prediction_timestamp, prediction_type)
);

CREATE TABLE IF NOT EXISTS intraday_accuracy_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES intraday_predictions(id),
    evaluated_at TEXT NOT NULL,
    evaluation_timestamp TEXT NOT NULL,
    actual_price REAL NOT NULL,
    predicted_price REAL NOT NULL,
    abs_error REAL NOT NULL, pct_error REAL NOT NULL,
    direction_correct INTEGER NOT NULL,
    target_hit INTEGER NOT NULL,
    UNIQUE(prediction_id)
);
```

Append these functions to `src/storage/db.py`:
```python


def insert_intraday_price_bar_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO intraday_price_bars "
        "(symbol, timestamp, interval, open, high, low, close, volume, source, fetched_at) "
        "VALUES (:symbol, :timestamp, :interval, :open, :high, :low, :close, :volume, :source, :fetched_at)",
        rows,
    )
    conn.commit()


def get_intraday_price_bars(
    conn: sqlite3.Connection, symbol: str, interval: str, start: str | None = None,
    end: str | None = None, start_exclusive: bool = False,
) -> pd.DataFrame:
    start_operator = ">" if start_exclusive else ">="
    query = "SELECT timestamp, open, high, low, close, volume FROM intraday_price_bars WHERE symbol = ? AND interval = ?"
    params: list = [symbol, interval]
    if start:
        query += f" AND timestamp {start_operator} ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)
    query += " ORDER BY timestamp"
    return pd.read_sql_query(query, conn, params=params)


def insert_intraday_prediction_row(conn: sqlite3.Connection, row: dict) -> int:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO intraday_predictions "
        "(created_at, symbol, prediction_timestamp, evaluation_timestamp, prediction_type, "
        "raw_current_price, open, high, low, close, volume, direction, predicted_price, "
        "expected_move_percent, confidence, reasoning, key_risks_json, technical_score, "
        "indicators_json, data_provider, interval, claude_model, prompt_version, "
        "raw_claude_response, input_tokens, output_tokens) VALUES "
        "(:created_at, :symbol, :prediction_timestamp, :evaluation_timestamp, :prediction_type, "
        ":raw_current_price, :open, :high, :low, :close, :volume, :direction, :predicted_price, "
        ":expected_move_percent, :confidence, :reasoning, :key_risks_json, :technical_score, "
        ":indicators_json, :data_provider, :interval, :claude_model, :prompt_version, "
        ":raw_claude_response, :input_tokens, :output_tokens)",
        row,
    )
    conn.commit()
    return cursor.rowcount


def insert_intraday_accuracy_evaluation(conn: sqlite3.Connection, evaluation: dict) -> int:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO intraday_accuracy_evaluations "
        "(prediction_id, evaluated_at, evaluation_timestamp, actual_price, predicted_price, "
        "abs_error, pct_error, direction_correct, target_hit) VALUES "
        "(:prediction_id, :evaluated_at, :evaluation_timestamp, :actual_price, :predicted_price, "
        ":abs_error, :pct_error, :direction_correct, :target_hit)",
        evaluation,
    )
    conn.commit()
    return cursor.rowcount
```

- [ ] **Step 5: Run tests to verify they pass, then run the full suite for regression**

Run: `pytest tests/test_db.py -v` — expect all pass, including the pre-existing daily tests in this file.
Run: `pytest -v` — expect all 68 pre-existing tests plus the new ones to pass, zero regressions.

- [ ] **Step 6: Commit**

```bash
git add config/settings.py src/storage/db.py tests/test_db.py
git commit -m "feat(intraday): add SQLite schema and config settings for intraday tables"
```

---

### Task 2: Intraday market-data provider

**Files:**
- Modify: `src/providers/market_data.py`
- Modify: `tests/test_market_data.py`

**Interfaces:**
- Consumes: `config.settings.{MARKET_DATA_MAX_RETRIES, MARKET_DATA_BACKOFF_SECONDS}` (reused, no new retry constants).
- Produces: `MarketDataProvider.get_intraday_history(symbol, start: datetime, end: datetime, interval: str) -> pd.DataFrame | None` with columns `["timestamp", "open", "high", "low", "close", "volume"]`; `MarketDataProvider.get_latest_intraday_price(symbol, as_of: datetime) -> tuple[datetime, float] | None`. Used by Task 8 (`run_intraday.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_market_data.py`:
```python
from datetime import datetime, timedelta, timezone


def _intraday_sample_frame():
    idx = pd.date_range("2026-09-14 09:15", periods=3, freq="60min", tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "Open": [1450.0, 1452.0, 1458.0], "High": [1455.0, 1460.0, 1462.0],
            "Low": [1448.0, 1450.0, 1455.0], "Close": [1452.0, 1458.0, 1460.0],
            "Volume": [10000, 12000, 9000],
        },
        index=idx,
    )


def test_get_intraday_history_returns_normalized_dataframe(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=_intraday_sample_frame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_intraday_history(
        "RELIANCE.NS", datetime(2026, 9, 14, 9, 0), datetime(2026, 9, 14, 12, 0), interval="60m",
    )

    assert list(result.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(result) == 3


def test_get_intraday_history_returns_none_for_empty_data(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=pd.DataFrame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_intraday_history(
        "BADSYM.NS", datetime(2026, 9, 14, 9, 0), datetime(2026, 9, 14, 12, 0), interval="60m",
    )
    assert result is None


def test_get_latest_intraday_price_filters_to_ticks_at_or_before_as_of(monkeypatch):
    idx = pd.date_range("2026-09-14 09:10", periods=10, freq="1min", tz="Asia/Kolkata")
    frame = pd.DataFrame({
        "Open": [100.0] * 10, "High": [100.0] * 10, "Low": [100.0] * 10,
        "Close": [100.0 + i for i in range(10)], "Volume": [1000] * 10,
    }, index=idx)
    fake_ticker = FakeHistoryTicker(frame=frame)
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    as_of = datetime(2026, 9, 14, 9, 15, tzinfo=idx.tz)
    result = provider.get_latest_intraday_price("RELIANCE.NS", as_of)

    assert result is not None
    ts, price = result
    assert ts <= as_of
    assert price == 105.0  # the 09:15 row is index 5 (09:10 + 5 min), Close = 100+5


def test_get_latest_intraday_price_returns_none_when_no_ticks_available(monkeypatch):
    fake_ticker = FakeHistoryTicker(frame=pd.DataFrame())
    monkeypatch.setattr("src.providers.market_data.yf.Ticker", lambda symbol: fake_ticker)

    provider = YFinanceProvider()
    result = provider.get_latest_intraday_price("RELIANCE.NS", datetime(2026, 9, 14, 9, 15))
    assert result is None
```

Note: `FakeHistoryTicker` already exists in this test file from Task 4 of the Phase 1 plan — reuse it as-is; its `history()` method already accepts arbitrary kwargs including `interval`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_data.py -v -k intraday`
Expected: FAIL — `AttributeError: 'YFinanceProvider' object has no attribute 'get_intraday_history'`.

- [ ] **Step 3: Implement in `src/providers/market_data.py`**

Add the abstract method to `MarketDataProvider`:
```python
    @abstractmethod
    def get_intraday_history(self, symbol: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame | None: ...

    @abstractmethod
    def get_latest_intraday_price(self, symbol: str, as_of: datetime) -> tuple[datetime, float] | None: ...
```

Add near the top of the file (with the other module constants):
```python
INTRADAY_REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

_INTRADAY_COLUMN_RENAME = {
    "Datetime": "timestamp", "Date": "timestamp", "Open": "open", "High": "high",
    "Low": "low", "Close": "close", "Volume": "volume",
}
```

Add these methods to `YFinanceProvider`:
```python
    def get_intraday_history(self, symbol: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame | None:
        for attempt in range(self._max_retries + 1):
            try:
                raw = yf.Ticker(symbol).history(start=start, end=end, interval=interval, auto_adjust=False)
                if raw is None or raw.empty:
                    logger.warning("No intraday data returned for %s", symbol)
                    return None
                df = raw.reset_index().rename(columns=_INTRADAY_COLUMN_RENAME)
                missing = set(INTRADAY_REQUIRED_COLUMNS) - set(df.columns)
                if missing:
                    logger.warning("Symbol %s missing intraday columns %s, skipping", symbol, missing)
                    return None
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                return df[INTRADAY_REQUIRED_COLUMNS]
            except Exception as exc:
                logger.warning("Intraday attempt %d failed for %s: %s", attempt + 1, symbol, exc)
                if attempt < self._max_retries:
                    time.sleep(self._backoff_seconds * (attempt + 1))
        logger.error("All intraday retries exhausted for %s, skipping", symbol)
        return None

    def get_latest_intraday_price(self, symbol: str, as_of: datetime) -> tuple[datetime, float] | None:
        start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        history = self.get_intraday_history(symbol, start=start, end=as_of + timedelta(minutes=1), interval="1m")
        if history is None or history.empty:
            return None
        filtered = history[history["timestamp"] <= as_of]
        if filtered.empty:
            return None
        last_row = filtered.iloc[-1]
        return last_row["timestamp"].to_pydatetime(), float(last_row["close"])
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_market_data.py -v` then `pytest -v` (full suite, expect all pre-existing + new tests passing).

- [ ] **Step 5: Commit**

```bash
git add src/providers/market_data.py tests/test_market_data.py
git commit -m "feat(intraday): add intraday market data methods to YFinanceProvider"
```

---

### Task 3: NSE intraday trading calendar & checkpoint handling

**Files:**
- Create: `src/universe/intraday_calendar.py`
- Create: `tests/test_intraday_calendar.py`

**Interfaces:**
- Consumes: `src.universe.trading_calendar.TradingCalendar.is_trading_day` (existing, reused).
- Produces: `intraday_calendar.CHECKPOINTS: list[time]`; `intraday_calendar.NEXT_CHECKPOINT: dict[time, time | None]` (maps each checkpoint to the next one; `15:15 -> None`); `intraday_calendar.IntradayMarketCalendar(trading_calendar, buffer_minutes=10).current_checkpoint(now: datetime) -> datetime | None`. Used by Task 8 (`run_intraday.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_intraday_calendar.py`:
```python
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.universe.intraday_calendar import NEXT_CHECKPOINT, IntradayMarketCalendar
from src.universe.trading_calendar import NseStaticHolidayCalendar

IST = ZoneInfo("Asia/Kolkata")


def test_next_checkpoint_maps_each_hour_forward():
    assert NEXT_CHECKPOINT[time(9, 15)] == time(10, 15)
    assert NEXT_CHECKPOINT[time(14, 15)] == time(15, 15)


def test_next_checkpoint_is_none_at_15_15():
    assert NEXT_CHECKPOINT[time(15, 15)] is None


def test_current_checkpoint_none_on_holiday():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar())
    now = datetime(2026, 9, 14, 10, 25, tzinfo=IST)  # Ganesh Chaturthi, a Monday
    assert cal.current_checkpoint(now) is None


def test_current_checkpoint_none_on_weekend():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar())
    now = datetime(2026, 9, 12, 10, 25, tzinfo=IST)  # Saturday
    assert cal.current_checkpoint(now) is None


def test_current_checkpoint_none_before_buffer_elapsed():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 9, 20, tzinfo=IST)  # Tuesday, only 5 min after 09:15
    assert cal.current_checkpoint(now) is None


def test_current_checkpoint_resolves_to_09_15_once_buffer_elapsed():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 9, 25, tzinfo=IST)  # Tuesday
    result = cal.current_checkpoint(now)
    assert result == datetime(2026, 9, 15, 9, 15, tzinfo=IST)


def test_current_checkpoint_resolves_to_latest_eligible_mid_day():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 12, 47, tzinfo=IST)  # simulates a missed-run recovery scenario
    result = cal.current_checkpoint(now)
    assert result == datetime(2026, 9, 15, 12, 15, tzinfo=IST)


def test_current_checkpoint_resolves_to_15_15_as_final_checkpoint():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 15, 30, tzinfo=IST)
    result = cal.current_checkpoint(now)
    assert result == datetime(2026, 9, 15, 15, 15, tzinfo=IST)


def test_current_checkpoint_none_well_after_market_close():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 18, 0, tzinfo=IST)  # 3+ hours after the last checkpoint
    assert cal.current_checkpoint(now) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intraday_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.universe.intraday_calendar'`.

- [ ] **Step 3: Implement `src/universe/intraday_calendar.py`**

```python
from __future__ import annotations

from datetime import datetime, time, timedelta

from src.universe.trading_calendar import TradingCalendar

CHECKPOINTS: list[time] = [
    time(9, 15), time(10, 15), time(11, 15), time(12, 15),
    time(13, 15), time(14, 15), time(15, 15),
]

NEXT_CHECKPOINT: dict[time, time | None] = {
    time(9, 15): time(10, 15),
    time(10, 15): time(11, 15),
    time(11, 15): time(12, 15),
    time(12, 15): time(13, 15),
    time(13, 15): time(14, 15),
    time(14, 15): time(15, 15),
    time(15, 15): None,
}

# A checkpoint is only "current" within this window after its buffered
# eligibility time, so a run triggered hours late (or accidentally at
# night) does not resolve to a stale checkpoint and redo pointless work.
ELIGIBILITY_WINDOW = timedelta(hours=1)


class IntradayMarketCalendar:
    def __init__(self, trading_calendar: TradingCalendar, buffer_minutes: int = 10):
        self._trading_calendar = trading_calendar
        self._buffer = timedelta(minutes=buffer_minutes)

    def current_checkpoint(self, now: datetime) -> datetime | None:
        if not self._trading_calendar.is_trading_day(now.date()):
            return None
        eligible = []
        for checkpoint_time in CHECKPOINTS:
            checkpoint_dt = datetime.combine(now.date(), checkpoint_time, tzinfo=now.tzinfo)
            eligible_from = checkpoint_dt + self._buffer
            eligible_until = eligible_from + ELIGIBILITY_WINDOW
            if eligible_from <= now <= eligible_until:
                eligible.append(checkpoint_dt)
        if not eligible:
            return None
        return max(eligible)
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_intraday_calendar.py -v` then `pytest -v`.

- [ ] **Step 5: Commit**

```bash
git add src/universe/intraday_calendar.py tests/test_intraday_calendar.py
git commit -m "feat(intraday): add checkpoint grid and self-healing checkpoint resolution"
```

---

### Task 4: Intraday indicators + no-look-ahead filter

**Files:**
- Create: `src/analysis/intraday_indicators.py`
- Create: `tests/test_intraday_indicators.py`

**Interfaces:**
- Consumes: `src.analysis.indicators.{sma, rsi, macd, bollinger_bands, support_resistance, rolling_volatility, momentum_roc, volume_trend}` (reused, unchanged); `src.analysis.technical_score.compute_technical_score` (reused, unchanged).
- Produces: `intraday_indicators.select_bars_up_to_checkpoint(bars: pd.DataFrame, checkpoint: datetime) -> pd.DataFrame` (the no-look-ahead filter — **this is the single function every other module must use to slice bars**); `intraday_indicators.ema(series, span) -> pd.Series`; `intraday_indicators.compute_intraday_indicators(bars: pd.DataFrame) -> dict`. Used by Task 5 (`intraday_engine.py`) and Task 8 (`run_intraday.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_intraday_indicators.py`:
```python
from datetime import datetime, timezone

import pandas as pd
import pytest

from src.analysis.intraday_indicators import compute_intraday_indicators, ema, select_bars_up_to_checkpoint


def _bars(timestamps: list[str], closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "close": closes, "volume": [1000 + i for i in range(n)],
    })


def test_select_bars_up_to_checkpoint_excludes_bar_starting_at_checkpoint():
    bars = _bars(
        ["2026-09-15T09:15:00+05:30", "2026-09-15T10:15:00+05:30"],
        [100.0, 999999.0],  # 10:15 bar has a deliberately distinctive value
    )
    checkpoint = pd.Timestamp("2026-09-15T10:15:00+05:30")
    result = select_bars_up_to_checkpoint(bars, checkpoint)
    assert len(result) == 1
    assert 999999.0 not in result["close"].values


def test_select_bars_up_to_checkpoint_includes_bar_that_just_closed():
    bars = _bars(
        ["2026-09-15T09:15:00+05:30", "2026-09-15T10:15:00+05:30"],
        [100.0, 105.0],
    )
    checkpoint = pd.Timestamp("2026-09-15T10:15:00+05:30")
    result = select_bars_up_to_checkpoint(bars, checkpoint)
    assert list(result["close"]) == [100.0]  # only the 09:15 bar, which closed at 10:15


def test_select_bars_up_to_checkpoint_at_market_open_returns_only_prior_days():
    bars = _bars(
        ["2026-09-14T14:15:00+05:30", "2026-09-15T09:15:00+05:30"],
        [200.0, 300.0],
    )
    checkpoint = pd.Timestamp("2026-09-15T09:15:00+05:30")
    result = select_bars_up_to_checkpoint(bars, checkpoint)
    assert list(result["close"]) == [200.0]


def test_ema_matches_manual_ewm():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(series, span=3)
    assert result.iloc[-1] == pytest.approx(series.ewm(span=3, adjust=False).mean().iloc[-1])


def test_compute_intraday_indicators_omits_sma50_with_insufficient_bars():
    closes = [100.0 + i * 0.5 for i in range(30)]
    timestamps = [f"2026-09-{10 + i // 7:02d}T{9 + i % 7:02d}:15:00+05:30" for i in range(30)]
    bars = _bars(timestamps, closes)
    result = compute_intraday_indicators(bars)
    assert result["sma50"] is None
    assert result["ema9"] is not None
    assert result["ema21"] is not None


def test_compute_intraday_indicators_technical_score_within_bounds():
    closes = [100.0 + i * 0.3 for i in range(60)]
    timestamps = [f"2026-09-{10 + i // 7:02d}T{9 + i % 7:02d}:15:00+05:30" for i in range(60)]
    bars = _bars(timestamps, closes)
    result = compute_intraday_indicators(bars)
    assert -100.0 <= result["technical_score"] <= 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intraday_indicators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.analysis.intraday_indicators'`.

- [ ] **Step 3: Implement `src/analysis/intraday_indicators.py`**

```python
from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.analysis.indicators import (
    bollinger_bands, macd, momentum_roc, rolling_volatility, rsi, sma,
    support_resistance, volume_trend,
)
from src.analysis.technical_score import compute_technical_score


def select_bars_up_to_checkpoint(bars: pd.DataFrame, checkpoint) -> pd.DataFrame:
    checkpoint_ts = pd.Timestamp(checkpoint)
    timestamps = pd.to_datetime(bars["timestamp"])
    return bars[timestamps < checkpoint_ts].reset_index(drop=True)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_intraday_indicators(bars: pd.DataFrame) -> dict:
    close = bars["close"]
    latest_close = float(close.iloc[-1])

    sma5 = float(sma(close, 5).iloc[-1])
    sma10 = float(sma(close, 10).iloc[-1])
    sma20 = float(sma(close, 20).iloc[-1])
    sma50 = float(sma(close, 50).iloc[-1]) if len(close) >= 50 else None

    ema9 = float(ema(close, 9).iloc[-1])
    ema21 = float(ema(close, 21).iloc[-1])

    rsi14 = float(rsi(close, 14).iloc[-1])
    macd_line, signal_line, histogram = macd(close)
    macd_value = float(macd_line.iloc[-1])
    macd_signal_value = float(signal_line.iloc[-1])
    macd_histogram = float(histogram.iloc[-1])

    bb_upper, bb_mid, bb_lower = bollinger_bands(close)
    bb_upper_value = float(bb_upper.iloc[-1])
    bb_mid_value = float(bb_mid.iloc[-1])
    bb_lower_value = float(bb_lower.iloc[-1])

    support, resistance = support_resistance(bars, window=20)
    volatility = rolling_volatility(close, window=20)
    momentum = momentum_roc(close, window=10)
    vol_trend = volume_trend(bars["volume"], window=20)

    trend_direction = "UP" if latest_close > sma20 else "DOWN"

    technical_score = compute_technical_score(
        latest_close, sma20, sma50, rsi14, macd_value, macd_signal_value,
        bb_upper_value, bb_lower_value, momentum, vol_trend,
    )

    return {
        "sma5": sma5, "sma10": sma10, "sma20": sma20, "sma50": sma50,
        "ema9": ema9, "ema21": ema21,
        "rsi14": rsi14, "macd": macd_value, "macd_signal": macd_signal_value,
        "macd_histogram": macd_histogram, "bb_upper": bb_upper_value,
        "bb_mid": bb_mid_value, "bb_lower": bb_lower_value,
        "support": support, "resistance": resistance,
        "volatility_percent": volatility, "momentum_roc_percent": momentum,
        "volume_trend_percent": vol_trend, "trend_direction": trend_direction,
        "technical_score": technical_score,
    }
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_intraday_indicators.py -v` then `pytest -v`.

- [ ] **Step 5: Commit**

```bash
git add src/analysis/intraday_indicators.py tests/test_intraday_indicators.py
git commit -m "feat(intraday): add hourly indicators, EMA, and the no-look-ahead bar filter"
```

---

### Task 5: Intraday prediction engine (Claude call + validation)

**Files:**
- Create: `src/prediction/intraday_engine.py`
- Create: `tests/test_intraday_engine.py`

**Interfaces:**
- Consumes: `config.settings.{CLAUDE_MODEL, INTRADAY_PROMPT_VERSION}`; `src.storage.db.insert_intraday_prediction_row`.
- Produces: `intraday_engine.build_prompt(symbol, current_price, technical_score, indicators) -> str`; `intraday_engine.validate_response(raw_text, expected_current_price) -> dict` (raises `IntradayPredictionValidationError`); `intraday_engine.request_prediction(client, symbol, current_price, technical_score, indicators) -> dict | None` (the returned dict carries `_input_tokens`/`_output_tokens` alongside the validated fields); `intraday_engine.build_prediction_row(symbol, prediction_timestamp, evaluation_timestamp, validated, raw_current_price, bar_snapshot, technical_score, indicators, data_provider, interval) -> dict`; `intraday_engine.insert_prediction(conn, row) -> int`. Used by Task 8 (`run_intraday.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_intraday_engine.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from src.prediction.intraday_engine import (
    IntradayPredictionValidationError,
    build_prediction_row,
    build_prompt,
    request_prediction,
    validate_response,
)


def _valid_payload(current_price=1450.0):
    return {
        "direction": "BULLISH", "current_price": current_price, "predicted_price": 1465.0,
        "expected_move_percent": 1.03, "confidence": 65, "reasoning": "uptrend",
        "key_risks": ["macro risk"],
    }


def test_build_prompt_is_single_horizon_not_multi_horizon():
    prompt = build_prompt("RELIANCE.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert "RELIANCE.NS" in prompt
    assert "next hourly checkpoint" in prompt.lower() or "next hour" in prompt.lower()
    assert "1d" not in prompt and "5d" not in prompt and "20d" not in prompt
    assert "history" not in prompt.lower()


def test_validate_response_accepts_valid_payload():
    result = validate_response(json.dumps(_valid_payload()), expected_current_price=1450.0)
    assert result["direction"] == "BULLISH"


@pytest.mark.parametrize("mutate,expected_error_substring", [
    (lambda p: "not json", "invalid JSON"),
    (lambda p: json.dumps([1, 2, 3]), "not a JSON object"),
    (lambda p: json.dumps({k: v for k, v in p.items() if k != "confidence"}), "missing fields"),
    (lambda p: json.dumps({**p, "direction": "UP"}), "invalid direction"),
    (lambda p: json.dumps({**p, "confidence": 150}), "out of range"),
    (lambda p: json.dumps({**p, "key_risks": []}), "key_risks"),
    (lambda p: json.dumps({**p, "confidence": "high"}), "malformed numeric field"),
    (lambda p: json.dumps({**p, "current_price": 999.0}), "does not match"),
])
def test_validate_response_rejects_each_invalid_shape(mutate, expected_error_substring):
    raw = mutate(_valid_payload())
    with pytest.raises(IntradayPredictionValidationError, match=expected_error_substring):
        validate_response(raw, expected_current_price=1450.0)


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeThinkingBlock:
    def __init__(self):
        self.type = "thinking"


class _FakeUsage:
    def __init__(self, input_tokens=650, output_tokens=400):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, content_blocks, usage=None):
        self.content = content_blocks
        self.usage = usage or _FakeUsage()


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        response = self._responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def test_request_prediction_extracts_text_block_when_thinking_block_precedes_it():
    client = _FakeClient([_FakeMessage([_FakeThinkingBlock(), _FakeTextBlock(json.dumps(_valid_payload()))])])
    result = request_prediction(client, "RELIANCE.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert result is not None
    assert result["_input_tokens"] == 650
    assert result["_output_tokens"] == 400
    assert client.messages.calls == 1


def test_request_prediction_retries_when_api_call_raises_then_succeeds():
    client = _FakeClient([
        RuntimeError("transient API error"),
        _FakeMessage([_FakeTextBlock(json.dumps(_valid_payload()))]),
    ])
    result = request_prediction(client, "RELIANCE.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert result is not None
    assert client.messages.calls == 2


def test_request_prediction_returns_none_after_two_failures():
    client = _FakeClient([
        _FakeMessage([_FakeTextBlock("not json")]),
        _FakeMessage([_FakeTextBlock("still not json")]),
    ])
    result = request_prediction(client, "RELIANCE.NS", 1450.0, 42.5, {"rsi14": 55.0})
    assert result is None
    assert client.messages.calls == 2


def test_build_prediction_row_uses_raw_current_price_param_not_claude_echo():
    validated = _valid_payload(current_price=1450.0)
    validated["_raw_response"] = json.dumps(validated)
    validated["_input_tokens"] = 650
    validated["_output_tokens"] = 400
    prediction_ts = datetime(2026, 9, 15, 9, 15, tzinfo=timezone.utc)
    evaluation_ts = datetime(2026, 9, 15, 10, 15, tzinfo=timezone.utc)
    bar_snapshot = {"open": 1449.0, "high": 1451.0, "low": 1448.0, "close": 1449.5, "volume": 5000}

    # deliberately pass a raw_current_price that differs from Claude's echoed current_price
    row = build_prediction_row(
        "RELIANCE.NS", prediction_ts, evaluation_ts, validated, raw_current_price=1449.5,
        bar_snapshot=bar_snapshot, technical_score=42.5, indicators={"rsi14": 55.0},
        data_provider="yfinance", interval="60m",
    )
    assert row["raw_current_price"] == 1449.5  # NOT 1450.0 (Claude's echo)
    assert row["open"] == 1449.0 and row["close"] == 1449.5
    assert row["input_tokens"] == 650 and row["output_tokens"] == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intraday_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.prediction.intraday_engine'`.

- [ ] **Step 3: Implement `src/prediction/intraday_engine.py`**

```python
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from config.settings import CLAUDE_MODEL, INTRADAY_PROMPT_VERSION
from src.storage import db

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {
    "direction", "current_price", "predicted_price", "expected_move_percent",
    "confidence", "reasoning", "key_risks",
}
ALLOWED_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}


class IntradayPredictionValidationError(Exception):
    pass


def build_prompt(symbol: str, current_price: float, technical_score: float, indicators: dict[str, Any]) -> str:
    indicators_summary = json.dumps(indicators, indent=2, default=str)
    return f"""You are an intraday technical analysis assistant for Indian equity markets.

Stock: {symbol}
Current price: {current_price}
Technical score (-100 bearish to +100 bullish): {technical_score}
Hourly indicators:
{indicators_summary}

Using only the data above, predict the price and direction for the NEXT
hourly checkpoint (1 hour from now). Do not invent data not given above.

Respond with STRICT JSON only, no text outside the JSON, in exactly this shape:
{{
  "direction": "BULLISH|BEARISH|NEUTRAL",
  "current_price": {current_price},
  "predicted_price": number,
  "expected_move_percent": number,
  "confidence": number (0-100),
  "reasoning": "string",
  "key_risks": ["string", ...]
}}"""


def validate_response(raw_text: str, expected_current_price: float) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise IntradayPredictionValidationError(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise IntradayPredictionValidationError("response is not a JSON object")

    missing = REQUIRED_FIELDS - set(data)
    if missing:
        raise IntradayPredictionValidationError(f"missing fields: {missing}")

    if data["direction"] not in ALLOWED_DIRECTIONS:
        raise IntradayPredictionValidationError(f"invalid direction: {data['direction']}")

    try:
        price_matches = abs(float(data["current_price"]) - expected_current_price) <= max(
            0.01 * expected_current_price, 0.5
        )
        confidence = float(data["confidence"])
        float(data["predicted_price"])
        float(data["expected_move_percent"])
    except (ValueError, TypeError) as exc:
        raise IntradayPredictionValidationError(f"malformed numeric field: {exc}") from exc

    if not price_matches:
        raise IntradayPredictionValidationError("current_price echoed back does not match sent value")
    if not (0 <= confidence <= 100):
        raise IntradayPredictionValidationError(f"confidence out of range: {confidence}")

    key_risks = data["key_risks"]
    if not isinstance(key_risks, list) or not key_risks or not all(isinstance(r, str) for r in key_risks):
        raise IntradayPredictionValidationError("key_risks must be a non-empty list of strings")

    return data


def request_prediction(client, symbol: str, current_price: float, technical_score: float, indicators: dict[str, Any]) -> dict[str, Any] | None:
    prompt = build_prompt(symbol, current_price, technical_score, indicators)
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text_block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
            if text_block is None:
                raise ValueError("no text block found in Claude response content")
            raw_text = text_block.text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        except Exception as exc:
            logger.warning("Intraday Claude API call failed for %s (attempt %d): %s", symbol, attempt + 1, exc)
            continue
        try:
            validated = validate_response(raw_text, current_price)
            validated["_raw_response"] = raw_text
            validated["_input_tokens"] = input_tokens
            validated["_output_tokens"] = output_tokens
            return validated
        except IntradayPredictionValidationError as exc:
            logger.warning("Intraday validation failed for %s (attempt %d): %s\nRaw response: %s", symbol, attempt + 1, exc, raw_text)
    logger.error("Skipping %s: intraday Claude response failed validation twice", symbol)
    return None


def build_prediction_row(
    symbol: str, prediction_timestamp: datetime, evaluation_timestamp: datetime,
    validated: dict[str, Any], raw_current_price: float, bar_snapshot: dict,
    technical_score: float, indicators: dict[str, Any], data_provider: str, interval: str,
) -> dict:
    return {
        "created_at": datetime.now(prediction_timestamp.tzinfo).isoformat(),
        "symbol": symbol,
        "prediction_timestamp": prediction_timestamp.isoformat(),
        "evaluation_timestamp": evaluation_timestamp.isoformat(),
        "prediction_type": "next_hour",
        "raw_current_price": raw_current_price,
        "open": bar_snapshot.get("open"), "high": bar_snapshot.get("high"),
        "low": bar_snapshot.get("low"), "close": bar_snapshot.get("close"),
        "volume": bar_snapshot.get("volume"),
        "direction": validated["direction"], "predicted_price": validated["predicted_price"],
        "expected_move_percent": validated["expected_move_percent"], "confidence": validated["confidence"],
        "reasoning": validated["reasoning"], "key_risks_json": json.dumps(validated["key_risks"]),
        "technical_score": technical_score, "indicators_json": json.dumps(indicators, default=str),
        "data_provider": data_provider, "interval": interval,
        "claude_model": CLAUDE_MODEL, "prompt_version": INTRADAY_PROMPT_VERSION,
        "raw_claude_response": validated["_raw_response"],
        "input_tokens": validated["_input_tokens"], "output_tokens": validated["_output_tokens"],
    }


def insert_prediction(conn, row: dict) -> int:
    return db.insert_intraday_prediction_row(conn, row)
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_intraday_engine.py -v` then `pytest -v`.

- [ ] **Step 5: Commit**

```bash
git add src/prediction/intraday_engine.py tests/test_intraday_engine.py
git commit -m "feat(intraday): add single-horizon Claude prediction engine with strict validation"
```

---

### Task 6: Intraday accuracy evaluator

**Files:**
- Create: `src/accuracy/intraday_scorer.py`
- Create: `tests/test_intraday_scorer.py`

**Interfaces:**
- Consumes: `src.storage.db.get_intraday_price_bars`, `src.storage.db.insert_intraday_accuracy_evaluation`.
- Produces: `intraday_scorer.find_intraday_predictions_due_for_evaluation(conn, as_of: datetime) -> list[sqlite3.Row]`; `intraday_scorer.evaluate_intraday_prediction(conn, prediction) -> dict | None`; `intraday_scorer.insert_intraday_accuracy_evaluation(conn, evaluation) -> int`; `intraday_scorer.run_intraday_accuracy_evaluation(conn, as_of: datetime) -> int`. Used by Task 8 (`run_intraday.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_intraday_scorer.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intraday_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.accuracy.intraday_scorer'`.

- [ ] **Step 3: Implement `src/accuracy/intraday_scorer.py`**

```python
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
```

Note: the finer-5-minute-bar `target_hit` refinement described in the spec is a documented enhancement, not required for this task's correctness — the hourly-bar-only computation above is correct and fully tested. If 5-minute bars are cached for the same window (from a future extension), `evaluate_intraday_prediction` can be revisited to prefer them; this plan does not require caching 5-minute bars, since Task 8's orchestration only fetches the configured `INTRADAY_INTERVAL` ("60m").

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_intraday_scorer.py -v` then `pytest -v`.

- [ ] **Step 5: Commit**

```bash
git add src/accuracy/intraday_scorer.py tests/test_intraday_scorer.py
git commit -m "feat(intraday): add accuracy evaluator with no-double-evaluation guard"
```

---

### Task 7: One-sheet hourly Excel report

**Files:**
- Create: `src/reporting/intraday_excel_report.py`
- Create: `tests/test_intraday_excel_report.py`

**Interfaces:**
- Consumes: `config.settings.{INTRADAY_CHECKPOINTS, INTRADAY_REPORTS_DIR}`; `intraday_predictions`/`intraday_accuracy_evaluations` tables (read-only, direct SQL — same approved exception as the daily `excel_report.py`).
- Produces: `intraday_excel_report.generate_intraday_report(conn, run_date: date, symbols: list[str], run_timestamp: datetime, market_status: str, processed: int, skipped: int, output_dir: Path = INTRADAY_REPORTS_DIR) -> Path`. Used by Task 8 (`run_intraday.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_intraday_excel_report.py`:
```python
from datetime import date, datetime, timezone

import openpyxl

from src.reporting.intraday_excel_report import generate_intraday_report
from src.storage.db import get_connection


def _seed_prediction_and_evaluation(conn, symbol, checkpoint_hhmm, predicted_price, actual_price=None, direction_correct=None, pct_error=None):
    ts = f"2026-09-15T{checkpoint_hhmm}:00+05:30"
    conn.execute(
        "INSERT INTO intraday_predictions (created_at, symbol, prediction_timestamp, "
        "evaluation_timestamp, prediction_type, raw_current_price, open, high, low, close, "
        "volume, direction, predicted_price, expected_move_percent, confidence, reasoning, "
        "key_risks_json, technical_score, indicators_json, data_provider, interval, "
        "claude_model, prompt_version, raw_claude_response, input_tokens, output_tokens) VALUES "
        "(?, ?, ?, '2026-09-15T99:99:00+05:30', 'next_hour', 1450.0, 1450, 1450, 1450, 1450, "
        "1000, 'BULLISH', ?, 1.0, 70.0, 'test', '[]', 40.0, '{}', 'yfinance', '60m', "
        "'claude-sonnet-5', 'intraday-v1', '{}', 650, 400)",
        ("2026-09-15T00:00:00+00:00", symbol, ts, predicted_price),
    )
    prediction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if actual_price is not None:
        conn.execute(
            "INSERT INTO intraday_accuracy_evaluations (prediction_id, evaluated_at, "
            "evaluation_timestamp, actual_price, predicted_price, abs_error, pct_error, "
            "direction_correct, target_hit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (prediction_id, "2026-09-15T00:00:00+00:00", ts, actual_price, predicted_price,
             abs(actual_price - predicted_price), pct_error, direction_correct),
        )
    conn.commit()


def test_generate_intraday_report_creates_exactly_one_worksheet(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _seed_prediction_and_evaluation(conn, "RELIANCE.NS", "10:15", 1465.0, actual_price=1458.0, direction_correct=1, pct_error=0.48)

    output_path = generate_intraday_report(
        conn, date(2026, 9, 15), ["RELIANCE.NS"], datetime(2026, 9, 15, 10, 25, tzinfo=timezone.utc),
        "OPEN", processed=1, skipped=0, output_dir=tmp_path / "reports",
    )

    assert output_path.exists()
    workbook = openpyxl.load_workbook(output_path)
    assert len(workbook.sheetnames) == 1
    conn.close()


def test_report_shows_blank_cells_for_unevaluated_checkpoints(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _seed_prediction_and_evaluation(conn, "RELIANCE.NS", "10:15", 1465.0)  # no evaluation yet

    output_path = generate_intraday_report(
        conn, date(2026, 9, 15), ["RELIANCE.NS"], datetime(2026, 9, 15, 10, 25, tzinfo=timezone.utc),
        "OPEN", processed=1, skipped=0, output_dir=tmp_path / "reports",
    )
    workbook = openpyxl.load_workbook(output_path)
    ws = workbook.active
    values = [cell.value for row in ws.iter_rows() for cell in row]
    assert 1465.0 in values  # the prediction is shown
    conn.close()


def test_report_regeneration_is_atomic_and_does_not_corrupt_existing_file_on_failure(tmp_path, monkeypatch):
    conn = get_connection(tmp_path / "test.db")
    _seed_prediction_and_evaluation(conn, "RELIANCE.NS", "10:15", 1465.0, actual_price=1458.0, direction_correct=1, pct_error=0.48)
    output_dir = tmp_path / "reports"

    good_path = generate_intraday_report(
        conn, date(2026, 9, 15), ["RELIANCE.NS"], datetime(2026, 9, 15, 10, 25, tzinfo=timezone.utc),
        "OPEN", processed=1, skipped=0, output_dir=output_dir,
    )
    original_bytes = good_path.read_bytes()

    import openpyxl as _openpyxl
    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated save failure")
    monkeypatch.setattr(_openpyxl.Workbook, "save", _boom)

    with pytest.raises(RuntimeError):
        generate_intraday_report(
            conn, date(2026, 9, 15), ["RELIANCE.NS"], datetime(2026, 9, 15, 11, 25, tzinfo=timezone.utc),
            "OPEN", processed=1, skipped=0, output_dir=output_dir,
        )

    assert good_path.read_bytes() == original_bytes  # untouched by the failed write
    conn.close()
```

Add `import pytest` at the top of the test file (needed for `pytest.raises` in the third test).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intraday_excel_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.reporting.intraday_excel_report'`.

- [ ] **Step 3: Implement `src/reporting/intraday_excel_report.py`**

```python
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import openpyxl

from config.settings import INTRADAY_CHECKPOINTS, INTRADAY_REPORTS_DIR

HOUR_BLOCK_COLUMNS = ["Predicted", "Actual", "Error %", "Direction", "Confidence %"]


def _fetch_today_predictions(conn: sqlite3.Connection, run_date: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT p.*, a.actual_price, a.pct_error, a.direction_correct "
        "FROM intraday_predictions p LEFT JOIN intraday_accuracy_evaluations a ON a.prediction_id = p.id "
        "WHERE date(p.prediction_timestamp) = ? ORDER BY p.symbol, p.prediction_timestamp",
        (run_date.isoformat(),),
    ).fetchall()


def _fetch_open_prices(conn: sqlite3.Connection, symbols: list[str], run_date: date) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol in symbols:
        row = conn.execute(
            "SELECT raw_current_price FROM intraday_predictions "
            "WHERE symbol = ? AND date(prediction_timestamp) = ? ORDER BY prediction_timestamp LIMIT 1",
            (symbol, run_date.isoformat()),
        ).fetchone()
        if row:
            prices[symbol] = row["raw_current_price"]
    return prices


def generate_intraday_report(
    conn: sqlite3.Connection, run_date: date, symbols: list[str], run_timestamp: datetime,
    market_status: str, processed: int, skipped: int, output_dir: Path = INTRADAY_REPORTS_DIR,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{run_date.isoformat()}.xlsx"
    tmp_path = output_dir / f".{run_date.isoformat()}.xlsx.tmp"

    rows = _fetch_today_predictions(conn, run_date)
    by_symbol: dict[str, dict[str, sqlite3.Row]] = {}
    for row in rows:
        checkpoint = row["prediction_timestamp"][11:16]
        by_symbol.setdefault(row["symbol"], {})[checkpoint] = row

    open_prices = _fetch_open_prices(conn, symbols, run_date)

    evaluated = [r for r in rows if r["actual_price"] is not None]
    confidences = [r["confidence"] for r in rows]
    avg_confidence = sum(confidences) / len(confidences) if confidences else None
    direction_hits = [r["direction_correct"] for r in evaluated]
    direction_accuracy = (sum(direction_hits) / len(direction_hits) * 100) if direction_hits else None
    errors = [r["pct_error"] for r in evaluated]
    avg_error = sum(errors) / len(errors) if errors else None
    total_tokens = sum(r["input_tokens"] + r["output_tokens"] for r in rows)

    best = min(evaluated, key=lambda r: r["pct_error"], default=None)
    worst = max(evaluated, key=lambda r: r["pct_error"], default=None)

    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Intraday"

    ws.append([
        f"Run: {run_timestamp.isoformat()}", f"Market: {market_status}",
        f"Processed: {processed}", f"Skipped: {skipped}",
    ])
    ws.append([
        f"Avg Confidence: {avg_confidence:.0f}%" if avg_confidence is not None else "Avg Confidence: n/a",
        f"Direction Acc so far: {direction_accuracy:.0f}%" if direction_accuracy is not None else "Direction Acc so far: n/a",
        f"Avg Error: {avg_error:.2f}%" if avg_error is not None else "Avg Error: n/a",
        f"Tokens used today: {total_tokens}",
    ])
    ws.append([
        f"Best: {best['symbol']} {best['pct_error']:.2f}% err" if best else "Best: n/a",
        f"Worst: {worst['symbol']} {worst['pct_error']:.2f}% err" if worst else "Worst: n/a",
    ])
    ws.append([
        "Indicators: SMA(5/10/20/50h) EMA(9/21h) RSI(14h) MACD(12/26/9h) BB(20h,2sd) | "
        "Checkpoints: 09:15-15:15 hourly | Provider: yfinance",
    ])
    ws.append([])

    hour_checkpoints = INTRADAY_CHECKPOINTS[1:]
    header_top = ["Stock", "09:15"]
    header_sub = ["", "Actual"]
    for cp in hour_checkpoints:
        header_top.extend([cp, "", "", "", ""])
        header_sub.extend(HOUR_BLOCK_COLUMNS)
    ws.append(header_top)
    ws.append(header_sub)

    header_row = ws.max_row - 1
    col = 3
    for _ in hour_checkpoints:
        ws.merge_cells(start_row=header_row, start_column=col, end_row=header_row, end_column=col + 4)
        col += 5

    for symbol in symbols:
        checkpoints = by_symbol.get(symbol, {})
        row_cells = [symbol, open_prices.get(symbol, "")]
        for cp in hour_checkpoints:
            record = checkpoints.get(cp)
            if record is None:
                row_cells.extend(["", "", "", "", ""])
                continue
            if record["direction_correct"] is None:
                direction_mark = ""
            else:
                direction_mark = "Yes" if record["direction_correct"] else "No"
            row_cells.extend([
                record["predicted_price"],
                record["actual_price"] if record["actual_price"] is not None else "",
                f"{record['pct_error']:.2f}%" if record["pct_error"] is not None else "",
                direction_mark,
                f"{record['confidence']:.0f}%",
            ])
        ws.append(row_cells)

    ws.freeze_panes = f"C{header_row + 2}"

    workbook.save(tmp_path)
    tmp_path.replace(output_path)
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_intraday_excel_report.py -v` then `pytest -v`.

- [ ] **Step 5: Commit**

```bash
git add src/reporting/intraday_excel_report.py tests/test_intraday_excel_report.py
git commit -m "feat(intraday): add single-worksheet hourly Excel report with atomic writes"
```

---

### Task 8: Intraday CLI orchestration

**Files:**
- Create: `src/run_intraday.py`
- Create: `tests/test_run_intraday.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: `run_intraday.run_intraday_pipeline(symbols, checkpoint, conn, provider, client, output_dir=INTRADAY_REPORTS_DIR, dry_run=False) -> dict` with keys `made, skipped, evaluated, report_path`; `run_intraday.main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_run_intraday.py`:
```python
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.run_intraday import run_intraday_pipeline
from src.storage.db import get_connection

IST = ZoneInfo("Asia/Kolkata")


def _hourly_frame(checkpoint: datetime, n_hours_back=60):
    timestamps = pd.date_range(end=checkpoint - timedelta(hours=1), periods=n_hours_back, freq="60min", tz=IST)
    closes = [1440.0 + i * 0.2 for i in range(n_hours_back)]
    return pd.DataFrame({
        "timestamp": timestamps, "open": closes, "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes], "close": closes, "volume": [1000 + i for i in range(n_hours_back)],
    })


class _FakeProvider:
    def __init__(self, hourly_frame, latest_tick=None, fail_symbols=None):
        self._hourly_frame = hourly_frame
        self._latest_tick = latest_tick
        self._fail_symbols = fail_symbols or set()

    def get_intraday_history(self, symbol, start, end, interval):
        if symbol in self._fail_symbols:
            return None
        return self._hourly_frame

    def get_latest_intraday_price(self, symbol, as_of):
        if symbol in self._fail_symbols:
            return None
        return self._latest_tick or (as_of, 1450.0)


def _valid_payload(current_price):
    return {
        "direction": "BULLISH", "current_price": current_price, "predicted_price": current_price * 1.01,
        "expected_move_percent": 1.0, "confidence": 65, "reasoning": "uptrend", "key_risks": ["macro risk"],
    }


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    input_tokens = 650
    output_tokens = 400


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        price_line = [line for line in prompt.splitlines() if line.startswith("Current price:")][0]
        current_price = float(price_line.split(":")[1].strip())
        return _FakeMessage(json.dumps(_valid_payload(current_price)))


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_dry_run_writes_nothing_to_db_or_reports(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    result = run_intraday_pipeline(
        ["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports", dry_run=True,
    )

    assert result["made"] == 1
    assert result["report_path"] is None
    assert conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM intraday_price_bars").fetchone()[0] == 0
    assert not (tmp_path / "reports").exists()
    conn.close()


def test_full_run_at_10_15_stores_prediction_and_writes_report(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    result = run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    assert result["made"] == 1
    assert result["skipped"] == 0
    assert result["report_path"].exists()
    assert conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0] == 1
    row = conn.execute("SELECT * FROM intraday_predictions").fetchone()
    assert row["prediction_timestamp"] == checkpoint.isoformat()
    conn.close()


def test_15_15_checkpoint_never_generates_a_new_prediction(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 15, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    result = run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    assert client.messages.calls == 0
    assert conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0] == 0
    conn.close()


def test_09_15_checkpoint_uses_latest_tick_not_hourly_bar_series(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint), latest_tick=(checkpoint, 1449.75))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    row = conn.execute("SELECT raw_current_price FROM intraday_predictions").fetchone()
    assert row["raw_current_price"] == 1449.75
    conn.close()


def test_symbol_with_no_market_data_is_skipped_without_stopping_others(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint), fail_symbols={"BADSYM.NS"})
    client = _FakeClient()

    result = run_intraday_pipeline(
        ["BADSYM.NS", "RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports",
    )

    assert result["made"] == 1
    assert result["skipped"] == 1
    conn.close()


def test_duplicate_checkpoint_run_does_not_create_a_second_prediction_row(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 10, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")
    result = run_intraday_pipeline(["RELIANCE.NS"], checkpoint, conn, provider, client, output_dir=tmp_path / "reports")

    count = conn.execute("SELECT COUNT(*) FROM intraday_predictions").fetchone()[0]
    assert count == 1
    assert result["made"] == 0  # the duplicate insert was ignored, not counted as a new prediction
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_intraday.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.run_intraday'`.

- [ ] **Step 3: Implement `src/run_intraday.py`**

```python
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from dotenv import load_dotenv

from config.settings import (
    INTRADAY_INTERVAL, INTRADAY_LAUNCHD_BUFFER_MINUTES, INTRADAY_LOOKBACK_DAYS,
    INTRADAY_REPORTS_DIR, TOP_N,
)
from src.accuracy.intraday_scorer import run_intraday_accuracy_evaluation
from src.analysis.intraday_indicators import compute_intraday_indicators, select_bars_up_to_checkpoint
from src.prediction.intraday_engine import build_prediction_row, insert_prediction, request_prediction
from src.providers.market_data import YFinanceProvider
from src.reporting.intraday_excel_report import generate_intraday_report
from src.storage import db
from src.universe.intraday_calendar import NEXT_CHECKPOINT, IntradayMarketCalendar
from src.universe.nifty50_weights import NIFTY50_WEIGHTS
from src.universe.selection import select_top_n
from src.universe.trading_calendar import NseStaticHolidayCalendar

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN_TIME = time(9, 15)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="India stock market analysis agent - intraday hourly run")
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def run_intraday_pipeline(
    symbols: list[str], checkpoint: datetime, conn, provider, client,
    output_dir: Path = INTRADAY_REPORTS_DIR, dry_run: bool = False,
) -> dict:
    logger = logging.getLogger("run_intraday.pipeline")
    next_checkpoint_time = NEXT_CHECKPOINT.get(checkpoint.time())
    start = checkpoint - timedelta(days=INTRADAY_LOOKBACK_DAYS)

    made = 0
    skipped = 0
    for symbol in symbols:
        if checkpoint.time() == MARKET_OPEN_TIME:
            latest = provider.get_latest_intraday_price(symbol, checkpoint)
            if latest is None:
                logger.warning("Skipping %s: no market-open tick available", symbol)
                skipped += 1
                continue
            _, raw_current_price = latest
            bar_snapshot = {"open": raw_current_price, "high": raw_current_price,
                             "low": raw_current_price, "close": raw_current_price, "volume": None}
        else:
            hourly = provider.get_intraday_history(symbol, start=start, end=checkpoint, interval=INTRADAY_INTERVAL)
            if hourly is None or hourly.empty:
                logger.warning("Skipping %s: no intraday market data", symbol)
                skipped += 1
                continue
            if not dry_run:
                rows = [
                    {
                        "symbol": symbol, "timestamp": r["timestamp"].isoformat(), "interval": INTRADAY_INTERVAL,
                        "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                        "close": float(r["close"]), "volume": int(r["volume"]), "source": "yfinance",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    for _, r in hourly.iterrows()
                ]
                db.insert_intraday_price_bar_rows(conn, rows)
            last_bar = hourly.iloc[-1]
            raw_current_price = float(last_bar["close"])
            bar_snapshot = {
                "open": float(last_bar["open"]), "high": float(last_bar["high"]),
                "low": float(last_bar["low"]), "close": float(last_bar["close"]),
                "volume": int(last_bar["volume"]),
            }

        if next_checkpoint_time is None:
            continue  # 15:15: evaluation-only, never generate a new prediction

        history = provider.get_intraday_history(symbol, start=start, end=checkpoint, interval=INTRADAY_INTERVAL)
        if history is None or history.empty:
            logger.warning("Skipping %s: no indicator history", symbol)
            skipped += 1
            continue
        filtered = select_bars_up_to_checkpoint(history, checkpoint)
        if filtered.empty:
            logger.warning("Skipping %s: no bars available before checkpoint", symbol)
            skipped += 1
            continue
        indicators = compute_intraday_indicators(filtered)

        prediction = request_prediction(client, symbol, raw_current_price, indicators["technical_score"], indicators)
        if prediction is None:
            skipped += 1
            continue

        if not dry_run:
            evaluation_ts = datetime.combine(checkpoint.date(), next_checkpoint_time, tzinfo=checkpoint.tzinfo)
            row = build_prediction_row(
                symbol, checkpoint, evaluation_ts, prediction, raw_current_price, bar_snapshot,
                indicators["technical_score"], indicators, "yfinance", INTRADAY_INTERVAL,
            )
            inserted = insert_prediction(conn, row)
            if inserted == 0:
                logger.info("Prediction for %s at %s already exists, treating as duplicate no-op", symbol, checkpoint.isoformat())
                continue
        made += 1
        logger.info("Intraday prediction generated for %s at %s (dry_run=%s)", symbol, checkpoint.isoformat(), dry_run)

    result = {"made": made, "skipped": skipped, "evaluated": 0, "report_path": None}
    if not dry_run:
        result["evaluated"] = run_intraday_accuracy_evaluation(conn, checkpoint)
        result["report_path"] = generate_intraday_report(
            conn, checkpoint.date(), symbols, checkpoint, "OPEN", made, skipped, output_dir=output_dir,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    now = datetime.now(IST)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / f"intraday-{now.date().isoformat()}.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("run_intraday")

    trading_calendar = NseStaticHolidayCalendar()
    intraday_calendar = IntradayMarketCalendar(trading_calendar, buffer_minutes=INTRADAY_LAUNCHD_BUFFER_MINUTES)
    checkpoint = intraday_calendar.current_checkpoint(now)
    if checkpoint is None:
        logger.info("No valid intraday checkpoint at %s, exiting cleanly", now.isoformat())
        return 0

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = [symbol for symbol, _, _ in select_top_n(NIFTY50_WEIGHTS, TOP_N)]

    conn = db.get_connection()
    provider = YFinanceProvider()
    client = anthropic.Anthropic()

    result = run_intraday_pipeline(symbols, checkpoint, conn, provider, client, dry_run=args.dry_run)
    logger.info(
        "Intraday run complete for checkpoint %s: %d made, %d skipped, %d evaluated",
        checkpoint.isoformat(), result["made"], result["skipped"], result["evaluated"],
    )
    if result["report_path"]:
        logger.info("Report written to %s", result["report_path"])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_run_intraday.py -v` then `pytest -v` (expect all pre-existing 68 tests plus every new test from Tasks 1-8 passing).

- [ ] **Step 5: Commit**

```bash
git add src/run_intraday.py tests/test_run_intraday.py
git commit -m "feat(intraday): wire intraday pipeline into CLI entrypoint python -m src.run_intraday"
```

---

### Task 9: Recovery / idempotency (missed-checkpoint detection)

**Files:**
- Modify: `src/run_intraday.py`
- Modify: `tests/test_run_intraday.py`

**Interfaces:**
- Produces: `run_intraday.PREDICTION_CHECKPOINTS: list[str]` (the six "HH:MM" checkpoints that generate predictions, excluding 15:15); `run_intraday.find_missed_checkpoints(conn, checkpoint: datetime) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_intraday.py`:
```python
from src.run_intraday import find_missed_checkpoints


def test_find_missed_checkpoints_empty_on_first_run_of_the_day(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    assert find_missed_checkpoints(conn, checkpoint) == []
    conn.close()


def test_find_missed_checkpoints_detects_a_gap(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    provider = _FakeProvider(_hourly_frame(datetime(2026, 9, 15, 9, 15, tzinfo=IST)))
    client = _FakeClient()

    # simulate a successful 09:15 run, then jump straight to 12:15 (10:15 and 11:15 were missed)
    run_intraday_pipeline(["RELIANCE.NS"], datetime(2026, 9, 15, 9, 15, tzinfo=IST), conn, provider, client, output_dir=tmp_path / "reports")

    missed = find_missed_checkpoints(conn, datetime(2026, 9, 15, 12, 15, tzinfo=IST))
    assert missed == ["10:15", "11:15"]
    conn.close()


def test_recovery_run_never_fabricates_predictions_for_missed_checkpoints(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    checkpoint_0915 = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    checkpoint_1215 = datetime(2026, 9, 15, 12, 15, tzinfo=IST)
    provider = _FakeProvider(_hourly_frame(checkpoint_1215))
    client = _FakeClient()

    run_intraday_pipeline(["RELIANCE.NS"], checkpoint_0915, conn, provider, client, output_dir=tmp_path / "reports")
    run_intraday_pipeline(["RELIANCE.NS"], checkpoint_1215, conn, provider, client, output_dir=tmp_path / "reports")

    recorded_checkpoints = {
        row["prediction_timestamp"][11:16]
        for row in conn.execute("SELECT prediction_timestamp FROM intraday_predictions WHERE symbol = 'RELIANCE.NS'")
    }
    assert recorded_checkpoints == {"09:15", "12:15"}  # never 10:15 or 11:15
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_intraday.py -v -k missed_checkpoint`
Expected: FAIL — `ImportError: cannot import name 'find_missed_checkpoints' from 'src.run_intraday'`.

- [ ] **Step 3: Add to `src/run_intraday.py`**

Add near the top, after the existing module constants:
```python
PREDICTION_CHECKPOINTS = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15"]


def find_missed_checkpoints(conn, checkpoint: datetime) -> list[str]:
    today = checkpoint.date().isoformat()
    current_hhmm = checkpoint.strftime("%H:%M")
    rows = conn.execute(
        "SELECT DISTINCT substr(prediction_timestamp, 12, 5) AS hhmm FROM intraday_predictions "
        "WHERE date(prediction_timestamp) = ?",
        (today,),
    ).fetchall()
    seen = {row["hhmm"] for row in rows}
    return [cp for cp in PREDICTION_CHECKPOINTS if cp < current_hhmm and cp not in seen]
```

Call it at the top of `run_intraday_pipeline`, right after computing `next_checkpoint_time`:
```python
    missed = find_missed_checkpoints(conn, checkpoint)
    if missed:
        logger.warning("Missed intraday checkpoints today (no prediction generated, not backfilled): %s", missed)
```

- [ ] **Step 4: Run tests to verify they pass, then regression**

Run: `pytest tests/test_run_intraday.py -v` then `pytest -v`.

- [ ] **Step 5: Commit**

```bash
git add src/run_intraday.py tests/test_run_intraday.py
git commit -m "feat(intraday): detect and log missed checkpoints without fabricating backfilled predictions"
```

---

### Task 10: macOS launchd automation

**Files:**
- Create: `launchd/com.stockagent.intraday.plist`
- Create: `tests/test_launchd_plist.py`

**Interfaces:** None (deployment artifact, not imported by application code).

- [ ] **Step 1: Write the failing test**

`tests/test_launchd_plist.py`:
```python
import plistlib
from pathlib import Path

PLIST_PATH = Path("launchd/com.stockagent.intraday.plist")


def test_plist_is_valid_and_has_exactly_seven_checkpoints():
    with open(PLIST_PATH, "rb") as f:
        data = plistlib.load(f)

    assert data["Label"] == "com.stockagent.intraday"
    assert data["ProgramArguments"][1:] == ["-m", "src.run_intraday"]

    intervals = data["StartCalendarInterval"]
    assert len(intervals) == 7
    hhmm_pairs = sorted((entry["Hour"], entry["Minute"]) for entry in intervals)
    assert hhmm_pairs == [(9, 25), (10, 25), (11, 25), (12, 25), (13, 25), (14, 25), (15, 25)]

    for entry in intervals:
        assert "Weekday" not in entry  # launchd fires daily; the app itself gates on trading days
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_launchd_plist.py -v`
Expected: FAIL — `FileNotFoundError: launchd/com.stockagent.intraday.plist`.

- [ ] **Step 3: Create `launchd/com.stockagent.intraday.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.stockagent.intraday</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>-m</string>
        <string>src.run_intraday</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/REPLACE/WITH/ABSOLUTE/PATH/TO/india-stock-agent</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>25</integer></dict>
        <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>25</integer></dict>
        <dict><key>Hour</key><integer>11</integer><key>Minute</key><integer>25</integer></dict>
        <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>25</integer></dict>
        <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>25</integer></dict>
        <dict><key>Hour</key><integer>14</integer><key>Minute</key><integer>25</integer></dict>
        <dict><key>Hour</key><integer>15</integer><key>Minute</key><integer>25</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/REPLACE/WITH/HOME/Library/Logs/stockagent-intraday.log</string>
    <key>StandardErrorPath</key>
    <string>/REPLACE/WITH/HOME/Library/Logs/stockagent-intraday-error.log</string>
</dict>
</plist>
```

No `Weekday` key on any interval — `launchd` fires every day at these seven times; `run_intraday.main()`'s own `IntradayMarketCalendar.current_checkpoint()` check (Task 3, Task 8) rejects weekends and holidays. This matches the spec's explicit design: launchd is a coarse trigger only.

The `/REPLACE/WITH/...` paths are genuine install-time parameters (the actual clone location and home directory differ per machine) — documented in Task 11's README section with the exact `sed`/manual-edit instructions, the same way Phase 1's `.env.example` documents a value that must be filled in per-installation.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_launchd_plist.py -v`

- [ ] **Step 5: Commit**

```bash
git add launchd/com.stockagent.intraday.plist tests/test_launchd_plist.py
git commit -m "feat(intraday): add launchd schedule (app-level checkpoint gate is the real authority)"
```

---

### Task 11: README documentation

**Files:**
- Modify: `README.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Add an "Intraday System (Phase 2)" section to `README.md`**

Cover, drawing accurately from the code written in Tasks 1-10:
1. **Overview** — one paragraph: independent hourly system, same NIFTY-50 Top 20 universe, same SQLite file, entirely separate tables, daily system untouched.
2. **How to run** — `python -m src.run_intraday`, `--symbols`, `--dry-run`, and what each does; note that a manual run only does something if `IntradayMarketCalendar.current_checkpoint()` resolves to a valid checkpoint (trading day + within the buffered eligibility window of one of the seven daily checkpoints).
3. **Checkpoint schedule & timing model** — the seven checkpoints, the `prediction_timestamp`/`created_at` distinction, the ~15-minute data-delay accommodation via the 10-minute launchd buffer, and the 15:15 evaluation-only boundary.
4. **Indicator periods** — the hourly SMA/EMA/RSI/MACD/BB periods table from the spec.
5. **Prediction contract** — the single-horizon JSON shape and validation rules from `intraday_engine.py`.
6. **Storage schema** — the three new tables, append-only, `src/storage/db.py` remains the only write path.
7. **Accuracy methodology** — what `intraday_accuracy_evaluations` fields mean.
8. **Excel report** — exactly one worksheet, the hourly column-block layout, where to find it (`reports/intraday/YYYY-MM-DD.xlsx`).
9. **launchd installation** — exact steps: copy the plist, replace the two `/REPLACE/WITH/...` paths with real absolute paths, `launchctl load ~/Library/LaunchAgents/com.stockagent.intraday.plist` (note it must be copied into `~/Library/LaunchAgents/` first), `launchctl unload` to stop, log locations.
10. **Cost** — 120 Claude calls/day for intraday (6 checkpoints × 20 stocks), 140/day combined with daily; token tracking via `input_tokens`/`output_tokens` columns and the Excel summary block.
11. **Known limitations** — the four items from spec §19 (data delay, 1-minute-bar lookback cap, no SLA on the free feed, 3:15 PM close boundary worth a live spot-check).
12. **Relationship to the daily system** — explicitly state they share only the SQLite file and universe configuration; no shared prediction/accuracy logic, no code path calls the other.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add intraday system section to README"
```

---

### Task 12: Live end-to-end validation

**Files:** None created — this task exercises the real system end-to-end and records results.

**Interfaces:** None (verification only).

- [ ] **Step 1: Full regression**

Run: `pytest -v` — expect all tests (68 pre-existing + every test added in Tasks 1-10) passing, zero failures.

- [ ] **Step 2: Confirm `ANTHROPIC_API_KEY` is available**

Same check as Phase 1: available via environment (`.env` or exported shell variable), never committed.

- [ ] **Step 3: Dry-run against real APIs, single symbol**

```bash
python -m src.run_intraday --dry-run --symbols RELIANCE.NS
```
Only produces output if invoked within the buffered eligibility window of a real checkpoint on a real trading day — if not, note this explicitly and either wait for a live window or invoke `run_intraday_pipeline` directly (bypassing only the calendar gate, exactly as Phase 1's Task 12 did for the daily system) with a real provider/client to exercise the real APIs.

Confirm: a real Claude call succeeds, the response validates, and nothing is written to SQLite or `reports/intraday/`.

- [ ] **Step 4: Real full-write run, two symbols**

```bash
python -m src.run_intraday --symbols RELIANCE.NS,TCS.NS
```
(or the direct `run_intraday_pipeline` call if outside a live checkpoint window). Confirm via `sqlite3`: `intraday_price_bars` populated, one `intraday_predictions` row per symbol with a real `raw_current_price`, real OHLCV snapshot, real `input_tokens`/`output_tokens`, and `reports/intraday/YYYY-MM-DD.xlsx` exists with exactly one worksheet.

- [ ] **Step 5: Demonstrate accuracy evaluation against real cached bars**

Either wait for the next real checkpoint and re-run (naturally evaluates the prior prediction), or seed one backdated `intraday_predictions` row plus real cached `intraday_price_bars` for its `evaluation_timestamp` (same technique as Phase 1's Task 12 backdated-prediction demo) and call `run_intraday_accuracy_evaluation` directly. Confirm a real `intraday_accuracy_evaluations` row is created with correct `direction_correct`/`target_hit`/error math.

- [ ] **Step 6: Verify the 15:15 boundary against real data**

If a live session happens to reach the 15:15 IST checkpoint during this validation, run it live and confirm zero Claude calls and zero new `intraday_predictions` rows are created at that checkpoint (only an evaluation of the 14:15 prediction). If not reached live during this session, this is already proven by Task 8's `test_15_15_checkpoint_never_generates_a_new_prediction` and Task 3's calendar tests — note explicitly which form of evidence was used.

- [ ] **Step 7: Record measured Claude token usage**

From the real `intraday_predictions.input_tokens`/`output_tokens` columns populated in Step 4, compute per-call and per-120-call-day token/cost estimates, and compare against the daily system's previously measured ~1,500-token figure per the spec's expectation that intraday's single-horizon response uses fewer tokens.

- [ ] **Step 8: Report Phase 2 completion**

Walk the checklist in the spec's §20 point by point with evidence (log excerpts, a sample `intraday_predictions` row, a sample `intraday_accuracy_evaluations` row, the Excel workbook's single-sheet structure, test results, measured token usage) and present it for review.

---

## Self-review notes

- **Spec coverage:** §2 (research findings) → embedded as Global Constraints and Task 3's checkpoint grid; §4 (checkpoint/timing strategy, 09:15 special case) → Task 3, Task 8; §5 (15:15 boundary) → Task 8 (structural `if next_checkpoint_time is None: continue`), tested explicitly in Task 8 and re-verified in Task 12; §7 (no-look-ahead) → Task 4's `select_bars_up_to_checkpoint`, used exclusively by Task 8; §8 (data integrity, raw price never from Claude) → Task 5's `build_prediction_row` takes `raw_current_price` as an explicit parameter, tested directly; §9 (schema) → Task 1; §10 (indicator periods) → Task 4; §11 (prediction contract) → Task 5; §12 (accuracy) → Task 6; §13 (one-sheet Excel) → Task 7, asserted via `len(sheetnames) == 1`; §14 (failure/recovery) → Task 8 (per-symbol isolation, atomic Excel write) + Task 9 (missed-checkpoint detection); §15 (cost, token tracking) → Task 1's `input_tokens`/`output_tokens` columns, Task 5's usage capture, Task 7's Excel summary; §16 (launchd) → Task 10; §17 (testing) → embedded per-task; §18 (migration) → this plan's task ordering; §19 (limitations) → Task 11's README section; §20 (completion criteria) → Task 12.
- **Placeholder scan:** no TBD/"add appropriate handling" remains; every step has literal code, literal test code, or literal shell/plist content. The two `/REPLACE/WITH/...` paths in the plist are genuine per-machine install parameters (same category as Phase 1's empty `.env.example` value), not unresolved planning placeholders, and Task 11 documents exactly how to fill them in.
- **Type consistency:** `run_intraday_pipeline`'s signature (`symbols, checkpoint, conn, provider, client, output_dir, dry_run`) matches every test call site across Tasks 8-9; `build_prediction_row`'s parameter list matches its Task 5 test and its Task 8 call site exactly; `db.insert_intraday_prediction_row`/`insert_intraday_accuracy_evaluation` both return `int` rowcounts, used consistently by Task 5/6's wrapper functions and Task 8/9's duplicate-detection logic; `NEXT_CHECKPOINT`'s `time` keys match `IntradayMarketCalendar.current_checkpoint`'s returned `datetime`'s `.time()` value exactly (both use the same `time(H, 15)` objects from `datetime.time`).
