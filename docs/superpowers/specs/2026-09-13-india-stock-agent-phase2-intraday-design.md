# India Stock Market Analysis Agent — Phase 2 Design: Intraday Hourly Prediction & Accuracy System

Status: Approved by user
Date: 2026-09-13

## 1. Purpose and scope

Extend the Phase 1 daily prediction agent with a second, independent capability:
an **intraday hourly prediction and accuracy-tracking system** for the same
fixed NIFTY-50 Top 20 universe, running once per hour during NSE trading
hours. At each hourly checkpoint the system captures the current market
price for each of the 20 stocks, computes hourly technical indicators from
data available strictly up to that checkpoint, asks Claude to predict the
stock's price/direction for the *next* hourly checkpoint, stores the
prediction immutably, and — at the following checkpoint — evaluates the
prior prediction against the real outcome.

**This is Phase 2. Phase 1 (daily prediction) is untouched and continues to
run independently.** The two systems share the same SQLite database file,
the same universe/weights configuration, and the same non-secret settings
module, but never share prediction or accuracy records, and never call each
other.

### Explicitly deferred / out of scope for this phase

- A paid, low-latency market-data provider (the free `yfinance` feed's
  ~15-minute delay is accepted and documented, not worked around).
- Any change to the Phase 1 daily prediction/accuracy/reporting code paths.
- Real fundamentals or news/sentiment data (same stubs as Phase 1, untouched).
- Cloud/server-based scheduling — this phase targets a single macOS machine
  via `launchd`.

## 2. Research findings that ground this design

These facts were independently verified (not assumed) on 2026-09-13 and
directly shape the design below:

- **NSE trading hours changed mid-2026.** As of August 3, 2026, stocks with
  F&O contracts — which includes every stock in our Top-20-by-weight
  universe — stop *continuous* trading at **3:15 PM IST**, moving into a
  Closing Auction Session until 3:35 PM. Stocks without F&O contracts still
  trade to 3:30 PM, but that does not apply to our universe.
- **Empirically confirmed via a live `yfinance` pull** (not just from the
  above news item): 60-minute interval bars for our universe land at
  exactly **09:15, 10:15, 11:15, 12:15, 13:15, 14:15, 15:15 IST** every
  trading day — seven checkpoints, and no 15:30 or 16:15 bar exists. This
  is the checkpoint grid the whole design uses.
- **`yfinance` interval/lookback limits** (empirically tested against
  RELIANCE.NS and, for reliability, all 20 target symbols): 1-minute bars
  are only available for ~7–8 days; 5/15/30-minute bars for ~60 days;
  **60-minute/1-hour bars for up to ~730 days**. All 20 target symbols
  fetched successfully in ~6 seconds total with zero failures and no
  rate-limiting observed.
- **Intraday data delay:** Yahoo/`yfinance` NSE intraday data is
  documented and confirmed to be **approximately 15 minutes delayed**, not
  real-time. This is a structural property of the free feed (NSE's true
  real-time feed is a paid product) and is designed around explicitly
  throughout this spec, not hidden or assumed away.
- **Bar labeling:** `yfinance` intraday bars are **start-labeled** — the
  bar timestamped `09:15` covers the interval `09:15→10:15`. This is the
  basis of the no-look-ahead rule in §7.
- **2026 NSE holiday data bug found and fixed separately.** The existing
  `src/universe/nse_holidays.py` had Ganesh Chaturthi on the wrong month
  entirely (listed as 2026-08-26; actually 2026-09-14), Holi off by a day,
  and a fabricated Diwali entry. This has already been corrected in a
  standalone hotfix (branch `hotfix/nse-holidays-2026-correction`,
  cross-verified against two independent published NSE holiday calendars),
  independent of this Phase 2 work but a prerequisite for it, since
  intraday scheduling correctness depends on accurate holiday data even
  more than daily does.

## 3. Architecture

```
india-stock-agent/
├── config/settings.py                    [MODIFIED — new intraday constants]
├── launchd/
│   └── com.stockagent.intraday.plist     [NEW]
├── src/
│   ├── run_daily.py                       [UNCHANGED]
│   ├── run_intraday.py                    [NEW — CLI entrypoint]
│   ├── providers/
│   │   ├── market_data.py                 [MODIFIED — new get_intraday_history method]
│   │   ├── fundamentals.py, news.py       [UNCHANGED]
│   ├── universe/
│   │   ├── nifty50_weights.py             [UNCHANGED — same universe]
│   │   ├── nse_holidays.py                [FIXED — separate hotfix]
│   │   ├── trading_calendar.py            [UNCHANGED]
│   │   ├── selection.py                   [UNCHANGED — reused]
│   │   └── intraday_calendar.py           [NEW — checkpoint grid, floor-to-checkpoint]
│   ├── analysis/
│   │   ├── indicators.py                  [UNCHANGED]
│   │   ├── technical_score.py             [UNCHANGED — reused as-is]
│   │   └── intraday_indicators.py         [NEW — hourly-period SMA/EMA/RSI/MACD/BB/vol]
│   ├── prediction/
│   │   ├── engine.py                      [UNCHANGED]
│   │   └── intraday_engine.py             [NEW — single-horizon Claude call]
│   ├── storage/
│   │   └── db.py                          [MODIFIED — 3 new tables, new CRUD fns]
│   ├── accuracy/
│   │   ├── scorer.py                      [UNCHANGED]
│   │   └── intraday_scorer.py             [NEW]
│   └── reporting/
│       ├── excel_report.py                [UNCHANGED]
│       └── intraday_excel_report.py       [NEW — incremental single-sheet workbook]
└── tests/                                  [8 new files, 2 extended]
```

Every daily-path module (`engine.py`, `scorer.py`, `excel_report.py`,
`run_daily.py`, `indicators.py`, `technical_score.py`, `trading_calendar.py`,
`selection.py`, `nifty50_weights.py`) is **untouched**. The only shared
files touched are `config/settings.py`, `src/storage/db.py`, and
`src/providers/market_data.py` — all changes are additive (new constants,
new tables, a new method on an existing ABC), and the full existing
68-test Phase 1 suite must continue to pass unchanged throughout.

**Extensibility guarantee (same principle as Phase 1):** `MarketDataProvider`
remains a single ABC; `get_intraday_history` is added to it alongside the
existing `get_history`/`get_latest_close`. A future paid provider
implements the same ABC and is swapped in via one line in `run_intraday.py`
— `intraday_engine.py`, `intraday_scorer.py`, and
`intraday_excel_report.py` never need to change.

## 4. Market-hour & checkpoint strategy (exact)

**Checkpoint grid (7 per trading day):**
`09:15, 10:15, 11:15, 12:15, 13:15, 14:15, 15:15` IST.

**What "actual price at checkpoint T" means:**
- For `T = 09:15` (market open): the latest available tick with a market
  timestamp ≤ 09:15, fetched directly (not drawn from the hourly bar
  series — no 09:15-labeled bar exists yet at that instant).
- For `T ∈ {10:15 … 15:15}`: the **close** of the 60-minute bar labeled
  `T − 1h` (the bar that just finished).

**Two distinct timestamps, always both recorded, never conflated:**
- `prediction_timestamp` — the **logical checkpoint** (e.g. `09:15:00+05:30`).
  Everything data-selection-related, evaluation-matching, and every Excel
  column keys off this value.
- `created_at` — the **actual wall-clock execution time** (e.g.
  `09:27:14+05:30`, whenever the process actually ran). An audit/diagnostic
  field only — never used for data selection or evaluation logic.

**Prediction generation:** at checkpoints 09:15 through 14:15 (six runs per
day), generate one prediction targeting the *next* checkpoint.
**15:15 is evaluation-only** (see §6) — it scores the 14:15 prediction and
generates no new prediction, because no 16:15 checkpoint exists.

**Launchd trigger vs. logical checkpoint (accounts for the 15-minute data
delay):** `launchd` fires 10 minutes after each nominal checkpoint —
`09:25, 10:25, 11:25, 12:25, 13:25, 14:25, 15:25` — giving the delayed feed
time to surface the just-closed bar. The *stored* `prediction_timestamp`
is always the clean checkpoint label, never the fuzzy trigger time.

**Self-healing checkpoint resolution (this is also the recovery
mechanism):** every run computes, independently of `launchd`'s exact
firing time, "the latest checkpoint `T` such that `T ≤ now − buffer`."
This single computation is what makes the following all fall out of one
mechanism:
- *Duplicate-fire protection:* a second trigger for the same checkpoint
  resolves to the same `T`; the `UNIQUE(symbol, prediction_timestamp,
  prediction_type)` constraint on `intraday_predictions` makes the second
  insert attempt a harmless no-op (`INSERT OR IGNORE`, same pattern as
  Phase 1's `price_bars` cache).
- *Missed-run recovery:* if the Mac sleeps through 10:15 and 11:15 and
  wakes at 12:47, the resolved checkpoint is `12:15`. The app logs
  "missed checkpoints: 10:15, 11:15" (detected by their absence in
  `intraday_predictions` for today), does **not** fabricate backfilled
  predictions for them, evaluates any prior predictions whose target bar
  has since become available (this can retroactively catch up 10:15's
  evaluation once the 11:15 bar exists, even on the 12:15 run), and
  generates exactly one new prediction, for `12:15` only.

**Application-level gate, independent of `launchd`:** every run first
checks `NseStaticHolidayCalendar.is_trading_day(today)` (existing, now
holiday-corrected) **and** that a valid checkpoint with sufficient buffer
has elapsed. If either check fails, the run logs why and exits 0 without
touching SQLite or Excel — the same philosophy as Phase 1's `run_daily.py`.

### The 09:15 checkpoint, precisely

1. `launchd` (or a manual run) fires around 09:25.
2. The app resolves the logical checkpoint to `09:15`.
3. `raw_current_price` is fetched as "the latest available tick with a
   market timestamp ≤ 09:15" — a separate provider call from the hourly
   bar series (see §8, data integrity).
4. Indicators are computed from the hourly bar history filtered to
   `bar_timestamp < 09:15` (§7's rule) — which, since today's first bar
   doesn't exist until 10:15, naturally resolves to **all of yesterday's
   (and earlier) hourly bars**. No special-case code path is needed for
   market open: it falls out of applying the same filter rule uniformly.
5. The 09:15→10:15 bar is never touched when generating the 09:15
   prediction — it does not exist yet at the time the filter is applied.

## 5. The 15:15 boundary — explicit, structural, testable

At the 15:15 checkpoint the application:

- **Evaluates** the 14:15 prediction, using the 14:15→15:15 bar's close as
  the actual price.
- **Does not** call the Claude prediction function at all. The
  checkpoint-iteration logic maps `NEXT_CHECKPOINT[15:15] = None`, and the
  pipeline branches on that to skip prediction generation entirely — this
  is a structural branch, not a try/fail-gracefully pattern, so it cannot
  degrade into accidentally attempting a prediction.
- **Never** queries or waits for a 16:15 bar — no code path references one.

Dedicated tests (§12) assert this directly: driving the pipeline through a
full synthetic trading day with a mocked Claude client must show exactly
six calls, never a seventh at 15:15; and the checkpoint resolver, called
directly on `15:15`, must return `None`, not raise or wrap around to
`09:15` of the next day.

## 6. Intraday data flow

```
launchd (hourly, HH:25) or manual invocation
        │
        ▼
IntradayMarketCalendar.current_checkpoint(now)  ──► None ──► log + exit 0
        │ (a checkpoint T is due)
        ▼
For each of 20 stocks (independently, failures isolated):
    YFinanceProvider.get_intraday_history(symbol, lookback, interval="60m")
        │
        ▼
    cache into intraday_price_bars (append-only, UNIQUE(symbol, timestamp, interval))
        │
        ▼
    intraday_indicators.select_bars_up_to_checkpoint(bars, T)   [§7 no-look-ahead filter]
        │
        ▼
    intraday_indicators.compute(...)  ──► technical_score (same formula as daily, reused)
        │
        ▼
    [if NEXT_CHECKPOINT[T] is not None] intraday_engine.request_prediction() ──► 1 Claude call
        │                                                                          │
        │                                                                  validate JSON
        ▼                                                                          ▼
    intraday_predictions (append-only, UNIQUE(symbol, prediction_timestamp, prediction_type))
        │
        ▼
intraday_scorer.evaluate_due_predictions(T)
    — finds predictions whose target checkpoint's actual bar now exists
    — computes error / direction / target-hit from provider data only
        │
        ▼
    intraday_accuracy_evaluations (append-only)
        │
        ▼
intraday_excel_report.update(reports/intraday/YYYY-MM-DD.xlsx)
    — write to temp file, atomic rename over the target
    — opens existing workbook if present for today, else creates fresh
```

## 7. No-look-ahead bias — the exact rule and required tests

**The rule:** at checkpoint `T`, only hourly bars with
`bar_start_timestamp < T` may be read. This single inequality, applied
uniformly, is the entire no-look-ahead mechanism:

| Checkpoint T | Bars usable (`timestamp < T`) | Bar that must NEVER be read |
|---|---|---|
| 09:15 | all bars from prior trading days | 09:15 bar (09:15→10:15 — doesn't exist yet) |
| 10:15 | …including today's 09:15 bar (closed, `09:15 < 10:15`) | 10:15 bar (10:15→11:15) |
| 15:15 (eval only) | …including today's 14:15 bar | n/a — no prediction generated |

This filter lives in exactly one function,
`intraday_indicators.select_bars_up_to_checkpoint(bars_df, checkpoint) ->
DataFrame`. Every indicator computation and the prompt-building step must
go through it; no other code path is permitted to slice the bars
DataFrame directly.

**Required tests:**
- `test_select_bars_up_to_checkpoint_excludes_bar_starting_at_checkpoint` —
  construct bars including one timestamped exactly `10:15` with a
  deliberately distinctive close value; call the filter for `10:15`;
  assert that bar is absent and its distinctive value never appears in the
  computed indicators.
- `test_select_bars_up_to_checkpoint_at_market_open_returns_only_prior_days` —
  feed bars spanning two days; filter for `09:15` on day 2; assert zero
  day-2 bars are returned.
- `test_intraday_engine_prompt_never_contains_future_bar_data` — end-to-end
  through `intraday_engine.build_prompt`; assert the distinctive
  future-bar value from the first test never appears in the generated
  prompt string.
- `test_pipeline_never_generates_prediction_at_15_15` — drive
  `run_intraday`'s per-checkpoint loop through a synthetic full day with a
  mocked Claude client; assert `messages.create` is called exactly six
  times, never for the 15:15 checkpoint.
- `test_09_15_prediction_uses_delayed_open_tick_not_hourly_bar_series` —
  mock the provider so the "latest tick" call and the "hourly bar series"
  call return distinguishable values; assert the 09:15 prediction's
  `raw_current_price` comes from the tick call and the indicators come
  from the bar-series call, correctly filtered.

## 8. Data integrity — raw market data vs. Claude output

Claude is **never** the source of truth for current price, actual/evaluation
price, OHLCV, or any raw market value:

- `intraday_predictions.raw_current_price` and its OHLCV columns are
  populated from the market-data provider **before** the Claude call is
  made, and are never touched by `validate_response()`'s output.
- `intraday_accuracy_evaluations.actual_price` comes exclusively from
  `intraday_price_bars` (provider data), never from anything Claude
  returned.
- Claude's own `current_price` field in its JSON response is used **only**
  as a tolerance-checked sanity echo (same pattern as the daily engine) —
  read, validated for plausibility, then discarded. It is never written to
  any "actual" or "current" column.

## 9. Database schema (extends the same SQLite file)

```sql
CREATE TABLE intraday_price_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,        -- ISO8601 with IST offset, bar start time
    interval TEXT NOT NULL,         -- '60m'
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(symbol, timestamp, interval)
);

CREATE TABLE intraday_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,                -- actual wall-clock execution time
    symbol TEXT NOT NULL,
    prediction_timestamp TEXT NOT NULL,      -- logical checkpoint T, e.g. '2026-09-14T10:15:00+05:30'
    evaluation_timestamp TEXT NOT NULL,      -- T + 1h, fixed at creation
    prediction_type TEXT NOT NULL,           -- 'next_hour' (constant for now; future-proofs the unique key)
    raw_current_price REAL NOT NULL,         -- from the market-data provider, NEVER Claude's echo
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,  -- OHLCV snapshot at T, provider-sourced
    direction TEXT NOT NULL, predicted_price REAL NOT NULL,
    expected_move_percent REAL NOT NULL, confidence REAL NOT NULL,
    reasoning TEXT NOT NULL, key_risks_json TEXT NOT NULL,
    technical_score REAL NOT NULL, indicators_json TEXT NOT NULL,
    data_provider TEXT NOT NULL, interval TEXT NOT NULL,
    claude_model TEXT NOT NULL, prompt_version TEXT NOT NULL, raw_claude_response TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    UNIQUE(symbol, prediction_timestamp, prediction_type)
);

CREATE TABLE intraday_accuracy_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES intraday_predictions(id),
    evaluated_at TEXT NOT NULL,
    evaluation_timestamp TEXT NOT NULL,
    actual_price REAL NOT NULL,              -- raw, from provider, at evaluation_timestamp
    predicted_price REAL NOT NULL,
    abs_error REAL NOT NULL, pct_error REAL NOT NULL,
    direction_correct INTEGER NOT NULL,
    target_hit INTEGER NOT NULL,             -- from finer 5m bars between T and T+1h if cached, else close-only
    UNIQUE(prediction_id)
);
```

Entirely separate tables from Phase 1's `predictions`/`accuracy_evaluations`
— daily and intraday never share rows. Same SQLite file; `src/storage/db.py`
remains the sole write path (new `insert_intraday_*`/`get_intraday_*`
functions added there, following the exact pattern already established).
The `CREATE TABLE IF NOT EXISTS` additions to `SCHEMA` are idempotent — an
existing database gains the new tables on the next `get_connection()` call,
no migration script required.

## 10. Technical indicators (intraday-specific periods)

Computed on 60-minute bars. Documented explicitly since "period=20" means
something different at hourly resolution than at daily resolution:

| Indicator | Daily period | Intraday period | Rationale |
|---|---|---|---|
| SMA | 5/10/20/50 days | **SMA 5/10/20/50 hours** (~1/1.5/3/8 trading days) | Same relative shape, different unit |
| EMA *(new for intraday)* | — | **EMA 9, EMA 21 hours** | Standard fast/slow intraday EMA pair |
| RSI | 14 days | **RSI 14 hours** (~2 days) | Same formula as `indicators.py`, reused |
| MACD | 12/26/9 days | **MACD 12/26/9 hours** | Same standard ratios |
| Bollinger | 20d, ±2σ | **20h, ±2σ** | Same |
| Volatility / Momentum / Volume-trend | 20d/10d/20d | **20h/10h/20h** | Same formulas, hourly window |
| Support/Resistance | 20-bar swing | **20-hour swing** | Same |

`technical_score`'s formula and weights (`src/analysis/technical_score.py`)
are reused **unchanged** — it is a pure function over already-computed
indicator values, agnostic to timeframe.

Cached lookback: 20 trading days of hourly bars (~140 bars) — comfortably
covers a 50-hour SMA with margin, well within `yfinance`'s ~730-day 60m
window.

## 11. Claude prediction (intraday engine)

One call per stock per prediction-generating checkpoint (six per day, not
seven — see §5). Prompt structure mirrors the daily engine's proven
pattern: only the computed technical score and indicator values, an
explicit "do not invent data" instruction, JSON-only response required.
Response contract:

```json
{
  "direction": "BULLISH",
  "current_price": 1450.25,
  "predicted_price": 1472.50,
  "expected_move_percent": 1.53,
  "confidence": 78,
  "reasoning": "Short explanation",
  "key_risks": ["Resistance near 1475", "Market-wide weakness"]
}
```

Validation mirrors the daily engine's hardened contract: rejects invalid
JSON, non-dict payloads, missing fields, an invalid `direction` enum,
`confidence` outside `[0, 100]`, an empty/non-string `key_risks` list, and
a mismatched `current_price` echo (tolerance check, same formula as
daily). Retry-once-then-skip-the-symbol on failure, with the same
defensive `try/except` around the API call itself that Phase 1's live
validation proved necessary (the extended-thinking `ThinkingBlock`
text-extraction fix applies identically here, since it's the same
underlying Anthropic SDK call pattern).

## 12. Accuracy calculations

Before generating a new prediction at each checkpoint: find
`intraday_predictions` rows whose `evaluation_timestamp` has occurred and
have no matching `intraday_accuracy_evaluations` row (the same
`LEFT JOIN ... WHERE id IS NULL` pattern already proven in Phase 1's
`accuracy/scorer.py`). For each: fetch the actual price at the evaluation
timestamp from `intraday_price_bars` (provider data only, per §8), compute
`abs_error`, `pct_error`, `direction_correct`, and `target_hit` (using
finer 5-minute bars between prediction and evaluation time if cached, to
check whether price touched the target at any point during that hour;
falls back to close-only comparison if finer bars aren't cached).
Aggregates (direction accuracy, average error, per-stock/overall) are
computed on read, directly off these tables.

## 13. Excel layout — strictly one worksheet

`reports/intraday/YYYY-MM-DD.xlsx`, **exactly one worksheet**, updated in
place after every hourly run (write-to-temp-file-then-atomic-rename, per
§14). No separate Config sheet — all reference/configuration information
is folded into the top summary block of the single primary sheet.

```
Row 1: Run: 2026-09-14 12:25 IST | Market: OPEN | Processed: 20 | Skipped: 0
Row 2: Avg Confidence: 62% | Direction Acc so far: 58% | Avg Error: 0.8% | Tokens used today: 41,230
Row 3: Best: RELIANCE +0.1% err | Worst: TCS -2.1% err
Row 4: Indicators: SMA(5/10/20/50h) EMA(9/21h) RSI(14h) MACD(12/26/9h) BB(20h,2σ) | Model: claude-sonnet-5 | Prompt: v1 | Provider: yfinance | Checkpoints: 09:15–15:15 hourly
Row 5: (blank)
Row 6:          |   09:15   |            10:15              |            11:15              | ...
Row 7: Stock     |  Actual   | Pred | Actual | Err% | Dir✓ | Conf | Pred | Actual | Err% | Dir✓ | Conf | ...
Row 8: RELIANCE  |  1450.00  | 1465 | 1458   | -0.5%|  ✓   | 78%  | 1462 |        |      |      |      |
...
Row 27: ULTRACEMCO| ...
```

- **Frozen panes** at column C / row 8 — stock names and the summary block
  stay visible while scrolling through the day's accumulating hour-blocks.
- Each hour-block after 09:15 is 5 columns (Predicted, Actual, Error%,
  Direction✓, Confidence); cells stay blank until that checkpoint's
  evaluation actually occurs — never fabricated.
- Conditional formatting: green/red fill on Direction✓; a color scale on
  Error%.
- By end of day: 1 (stock name) + 1 (09:15 actual) + 6 × 5 (hour blocks)
  = 32 columns — comfortably within Excel's limits, readable with frozen
  panes.
- Column headers use the **evaluation/target timestamp** as the label
  (e.g. "10:15 Predicted" = the prediction that targets 10:15, made at
  09:15) — matches the natural reading order of the grid.
- Verified in tests via `len(openpyxl.load_workbook(path).sheetnames) == 1`.

## 14. Failure & recovery strategy

- **Per-symbol isolation:** one stock's data-fetch or Claude failure is
  logged and that stock is skipped for the checkpoint; the other 19
  continue — same proven pattern as `run_daily.py`.
- **Claude failure:** never fabricates a prediction; retry-once-then-skip,
  same hardened `try/except` as the daily engine.
- **Market-data provider failure:** retried per the existing
  `MARKET_DATA_MAX_RETRIES`/`MARKET_DATA_BACKOFF_SECONDS` policy
  (extended to the new intraday method), then the symbol is skipped —
  never a fabricated price.
- **Excel write failure:** the report generator writes to a temp file and
  atomically renames it over the target workbook; a failure or
  interruption mid-write leaves the previous good workbook untouched, and
  is logged. SQLite is never affected by an Excel failure.
- **SQLite as sole source of truth:** the workbook is write-only output —
  no code path ever reads the `.xlsx` back into the pipeline for a
  decision. It is fully reconstructable from `intraday_price_bars` +
  `intraday_predictions` + `intraday_accuracy_evaluations` at any time.
- **Missed runs / sleep recovery:** see the self-healing checkpoint
  resolution in §4 — missed checkpoints are logged, never backfilled with
  fabricated data, and the system simply resumes at the next valid
  checkpoint.
- **Duplicate triggers:** the `UNIQUE(symbol, prediction_timestamp,
  prediction_type)` constraint plus `INSERT OR IGNORE` makes a duplicate
  `launchd` fire for the same checkpoint a harmless no-op.

## 15. Cost control

**Six prediction-generating checkpoints × 20 stocks = 120 Claude calls per
trading day**, plus Phase 1's existing 20 calls/day for the daily system =
**140 calls/day total**. No Claude calls occur anywhere else in the
intraday pipeline — accuracy scoring, indicator computation, Excel
generation, and the market-status/checkpoint gate are all pure local
Python, exactly as in Phase 1. This is a checkable invariant: `grep -r
"client.messages.create"` across `src/prediction/` must find exactly two
call sites, one in `engine.py` (daily) and one in `intraday_engine.py`.

**Token measurement:** every `request_prediction` call in
`intraday_engine.py` logs `response.usage.input_tokens`/`output_tokens`
and persists both values on the `intraday_predictions` row itself (§9's
`input_tokens`/`output_tokens` columns) — not just to the log file. This
keeps the "Excel is fully reconstructable from SQLite" guarantee (§14)
true even for the token-usage figures: `run_intraday.py`'s end-of-run log
line and the Excel summary block's "Tokens used today" (§13, Row 2) are
both simple `SUM()` queries over today's `intraday_predictions` rows, not
an in-memory-only running total that would be lost if the process
restarted mid-day. Actual measured per-call and per-day figures will be
captured on the first live run and folded into the README, exactly as
Phase 1's completion process did — no cost number in this spec is
presented as more precise than "expect fewer tokens per call than daily's
~1,500 measured figure, since the response schema is a single horizon
rather than four."

Prompts stay compact: only computed indicators and technical score, never
raw price history — same design principle as the daily engine.

## 16. launchd scheduling design

`launchd/com.stockagent.intraday.plist` — a `StartCalendarInterval` array
with seven entries (09:25, 10:25, 11:25, 12:25, 13:25, 14:25, 15:25 IST),
restricted to weekdays via the plist's weekday field. `launchd` is a
coarse trigger only, per §4 — the application independently re-derives the
correct checkpoint and safely no-ops on holidays, weekends, pre-market,
post-market, or duplicate fires. The README documents `launchctl
load`/`unload` and the log location
(`~/Library/Logs/stockagent-intraday.log` via the plist's
`StandardOutPath`).

## 17. Testing strategy

Comprehensive coverage, all mocking `yfinance`/`anthropic` — **zero live
calls in `pytest`**, matching the existing 68-test suite's discipline:

- `test_intraday_calendar.py` — checkpoint grid, floor-to-checkpoint
  resolution, market-open/close boundary handling, holiday interaction,
  `NEXT_CHECKPOINT[15:15] is None`.
- `test_intraday_indicators.py` — hourly SMA/EMA/RSI/MACD/BB/volatility/
  momentum/volume-trend correctness, plus the five no-look-ahead tests
  from §7.
- `test_intraday_engine.py` — JSON validation (same contract rigor as
  Phase 1's `test_engine.py`), retry-then-skip, the future-bar-never-in-prompt
  test from §7.
- `test_intraday_scorer.py` — evaluation matching, no duplicate
  evaluation, `target_hit` computation from finer bars when available.
- `test_intraday_excel_report.py` — single-worksheet assertion, incremental
  column updates across repeated calls, atomic-write-on-failure behavior,
  empty-workbook creation.
- `test_run_intraday.py` — duplicate-checkpoint no-op via the UNIQUE
  constraint, missed-checkpoint recovery, per-symbol/Claude/provider
  failure isolation, the "exactly six Claude calls, never at 15:15" test
  from §5.
- Extends `test_market_data.py` (new `get_intraday_history` method) and
  `test_db.py` (new tables) — same style as the existing tests.

## 18. Migration strategy from Phase 1

1. **Prerequisite:** merge the NSE holiday hotfix
   (`hotfix/nse-holidays-2026-correction`).
2. Extend `config/settings.py` with intraday constants (checkpoint list,
   lookback days, launchd buffer minutes, `reports/intraday` path,
   intraday retry/backoff) — additive only.
3. Extend `src/storage/db.py`: add the three `CREATE TABLE IF NOT EXISTS`
   statements to `SCHEMA` (idempotent) plus new CRUD functions.
4. Extend `MarketDataProvider` ABC + `YFinanceProvider` with
   `get_intraday_history` — additive method; `get_history`/`get_latest_close`
   untouched.
5. Build the five new intraday-only modules (calendar, indicators, engine,
   scorer, Excel report) independently, each TDD'd against the interfaces
   above — same rigor as Phase 1's subagent-driven development.
6. Build `src/run_intraday.py`.
7. Write the `launchd` plist and the README's intraday section.
8. Full regression: all existing 68 tests must still pass, unchanged,
   throughout every step.

## 19. Known limitations of free intraday data

- **~15-minute data delay** (Yahoo/`yfinance`, not a bug): the "current
  price" at any checkpoint is really the price as of roughly 15 minutes
  earlier. This affects prediction generation and evaluation identically,
  so accuracy comparisons remain internally consistent, but the system is
  not suitable for anything requiring true real-time prices.
- **1-minute bars available for only ~7–8 days** — irrelevant to this
  design (we use 60-minute bars), but rules out very short-horizon
  backtesting later without a different provider.
- **No SLA on the free feed** — Yahoo can change or throttle the endpoint
  without notice. This is precisely why `MarketDataProvider` is an
  interface: a paid provider can be substituted later without touching
  `intraday_engine.py`, `intraday_scorer.py`, or
  `intraday_excel_report.py`.
- **3:15 PM close boundary behavior** should be empirically re-confirmed
  on the first live trading day — this spec's checkpoint grid was
  verified against historical data pulled on a non-trading day (2026-09-13,
  a Sunday); true intraday behavior exactly at the moment of close is
  worth a spot check once the system runs live.

## 20. Phase 2 completion criteria

Before declaring Phase 2 done, an actual end-to-end run against real data
must show:

- [ ] All 68 Phase 1 tests still pass, unchanged
- [ ] New intraday test suite passes, zero live calls in `pytest`
- [ ] A full synthetic trading day drives exactly 6 Claude calls per
      stock (never a 7th at 15:15), verified by test
- [ ] No-look-ahead tests from §7 pass, including the future-bar-never-
      leaks-into-the-prompt test
- [ ] `launchd` plist installs and fires at the documented times
- [ ] A real live trading-hour run produces a valid prediction, stores it
      immutably, and the workbook has exactly one worksheet
- [ ] A subsequent live checkpoint evaluates the prior prediction using
      only provider-sourced actual prices
- [ ] Measured real Claude token usage (input/output, per call and daily
      total) is captured and documented
- [ ] Duplicate-trigger and missed-checkpoint recovery behavior demonstrated
      (can be via a controlled test scenario if a real missed run hasn't
      occurred yet)
- [ ] README updated with the intraday methodology, schedule, and known
      limitations

Deliverables to present at completion: updated project structure, new
schema, one real sample intraday prediction row, one real sample intraday
accuracy row, the generated `reports/intraday/YYYY-MM-DD.xlsx` structure,
test results, measured Claude token usage, and known limitations —
mirroring Phase 1's completion summary format.
