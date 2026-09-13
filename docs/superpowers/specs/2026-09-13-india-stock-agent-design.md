# India Stock Market Analysis Agent — Phase 1 Design

Status: Approved by user (pending final spec review)
Date: 2026-09-13

## 1. Purpose and scope

Build a Python pipeline that, for a fixed universe of 20 NIFTY-50 stocks, pulls
recent market data, computes technical indicators, asks Claude to turn those
indicators into a structured, multi-horizon prediction, stores every
prediction immutably, later scores older predictions against real outcomes,
and reports all of it in an Excel workbook generated from a SQLite database
of record.

This is **Phase 1 only**. Phase 1 is manual-execution — there is no
scheduled/automated run yet (see §11). The system is not investment advice;
all reports carry a disclaimer to that effect.

### Explicitly deferred to later phases

- Paid market-data providers (Kite Connect, Upstox, Alpha Vantage, etc.)
- Real fundamentals data (P/E, ROE, debt, earnings, etc.)
- Real news/sentiment data
- Automated daily scheduling via macOS `launchd`
- Dynamic (market-cap or liquidity based) universe selection

Each deferred area gets a clean interface + stub now so it can be filled in
later without touching the rest of the pipeline.

### Not required

- **Bun**: this project has no Node/JS/Bun dependency anywhere. It is pure
  Python (yfinance, pandas, openpyxl, sqlite3, anthropic SDK). A "Bun not
  found" error the user saw earlier did not originate from this project (no
  project files existed at the time) and does not need to be fixed here.

## 2. Architecture

```
NIFTY-50 static weights (config)
        │
        ▼
Top-20 selection ──────► universe_snapshots (SQLite)
        │
        ▼
MarketDataProvider (interface)
   └── YFinanceProvider (Phase 1 implementation)
        │  3 months OHLCV per symbol
        ▼
price_bars (SQLite, append-only cache)
        │
        ▼
Indicators (pandas) ──► technical score (deterministic)
        │
        ▼
Prediction Engine ──► 1 Claude API call per stock,
        │             covering all 4 horizons at once
        ▼
Strict JSON validation ──► predictions (SQLite, append-only, one row
        │                    per stock × horizon)
        ▼
Accuracy Evaluator (uses TradingCalendar + price_bars)
        │
        ▼
accuracy_evaluations (SQLite, append-only)
        │
        ▼
Excel report generator (reads only from SQLite) ──► reports/YYYY-MM-DD.xlsx
```

`FundamentalsProvider` and `NewsSentimentProvider` sit beside
`MarketDataProvider` as parallel interfaces; their Phase 1 implementations
are stubs that return an explicit "Not available in Phase 1" value and are
never invented or estimated.

## 3. Universe selection

- A static, version-controlled table of NIFTY-50 constituents and their
  published index weights (`universe/nifty50_weights.py`), with a comment
  documenting the source and date it was last refreshed.
- Each run takes the top 20 by weight. This is deterministic and
  reproducible for any given weights snapshot.
- Every run records the exact 20 symbols used that day into
  `universe_snapshots`, so historical reports remain reproducible even after
  the weights file is later updated (no silent survivorship bias).
- The weights file is manually refreshed periodically (documented in
  README); no live scraping of index composition in Phase 1.

## 4. Trading calendar

Horizons are defined in **trading days**, not calendar days. A 5-trading-day
prediction must never be evaluated by adding 5 calendar days.

- `TradingCalendar` interface: `is_trading_day(date)`,
  `add_trading_days(date, n)`, `trading_days_between(start, end)`.
- Phase 1 implementation, `NseStaticHolidayCalendar`: weekday check (Mon–Fri)
  minus a documented, manually-maintained static list of NSE trading
  holidays per year (`universe/nse_holidays.py`), with a README note to
  update it each January. If a maintained free holiday library/API is found
  during implementation that is more reliable, it can replace this
  implementation without changing any caller.

## 5. Market data provider

- `MarketDataProvider` interface: `get_history(symbol, start, end) -> DataFrame[date, open, high, low, close, adj_close, volume]`,
  `get_latest_close(symbol) -> (date, price)`.
- `YFinanceProvider` (Phase 1): wraps `yfinance`, symbols suffixed `.NS`.
  Retries transient failures twice with backoff. Missing/incomplete data for
  a symbol is logged and that symbol is skipped for the run — never
  backfilled with guessed values.
- All fetched bars are cached into `price_bars` (append-only; a bar for a
  given symbol+date is only inserted once). This cache is what accuracy
  evaluation later reads to get "actual" prices — no live re-fetch is needed
  to score old predictions, and it also gives the Excel "Historical Prices"
  sheet its data.

## 6. Indicators and technical score

Computed per symbol from the cached 3-month price history: SMA(5/10/20/50 —
50 only if enough history), RSI(14), MACD, Bollinger Bands, recent
support/resistance (swing highs/lows), rolling volatility, momentum
(rate of change), volume trend, and overall trend direction.

A deterministic **technical score** (documented formula and weights in
README) combines a fixed, documented subset of these indicators into a
single numeric signal. This score — plus the raw indicator values — is what
gets sent to Claude; Claude does not recompute indicators itself.

## 7. Prediction engine

**One Claude API call per stock per day**, covering all four horizons in a
single structured response (this is the cost-control measure: 20 calls/day,
not 80). The prompt contains only the summarized, already-computed
indicator values and technical score for that stock — never the full raw
price history — kept compact by design.

Claude must return strict JSON, no free text outside it:

```json
{
  "current_price": 1450.0,
  "horizons": {
    "1d":  { "direction": "BULLISH", "target_price": 1465, "range_low": 1455, "range_high": 1475, "expected_move_percent": 1.03, "risk_level": 1435, "confidence": 65, "reasoning": "...", "key_risks": ["..."] },
    "5d":  { "...": "same shape" },
    "10d": { "...": "same shape" },
    "20d": { "...": "same shape" }
  }
}
```

- `direction` ∈ {BULLISH, BEARISH, NEUTRAL}.
- `confidence` ∈ [0, 100].
- Validation (before anything is stored): all four horizon keys present,
  required fields present with correct types, `direction` in the allowed
  set, `range_low <= range_high`, `confidence` numeric in range,
  `key_risks` a non-empty list of strings.
- On validation failure: log the raw response and the validation error,
  retry the call once. If it still fails, **skip the stock entirely for
  every horizon** (no partial storage) and continue with the rest of the
  universe.
- Claude only ever interprets data the application supplies in the prompt.
  It is never asked to look up or guess a price, and the application never
  trusts a price value from Claude's response as authoritative — only
  `current_price` echoed back is cross-checked against what was sent, as a
  sanity check.

## 8. Storage (SQLite — source of truth)

All tables are append-only in normal operation; the application never runs
`UPDATE`/`DELETE` on these rows.

**`universe_snapshots`**
`id, snapshot_date, symbol, index_weight, rank, source, created_at`

**`price_bars`**
`id, symbol, date, open, high, low, close, adj_close, volume, source, fetched_at`
(unique on `symbol, date`)

**`predictions`** — one row per stock × horizon, fully self-contained:
`id, created_at, prediction_date, symbol, horizon_days, target_evaluation_date, current_price, direction, target_price, range_low, range_high, expected_move_percent, risk_level, confidence, reasoning, key_risks_json, technical_score, indicators_json, data_timestamp, data_provider, claude_model, prompt_version, raw_claude_response`

`target_evaluation_date` is computed once, at creation time, via
`TradingCalendar.add_trading_days(prediction_date, horizon_days)` — so
"what date do we grade this against" is fixed permanently at creation and
never recomputed later with different calendar logic.

**`accuracy_evaluations`** — one row per prediction, created once that
prediction's `target_evaluation_date` has occurred:
`id, prediction_id, evaluated_at, evaluation_date, actual_close, actual_high, actual_low, window_high, window_low, direction_correct, target_hit, within_range, prediction_error, abs_error, pct_error, return_after_prediction_percent, max_favorable_excursion, max_adverse_excursion`

- `actual_close/high/low` = that single day's bar on `target_evaluation_date`.
- `window_high` / `window_low` = max high / min low across every trading day
  from `prediction_date` (exclusive) to `target_evaluation_date` (inclusive)
  — used to compute `target_hit` (did price touch `target_price` at any
  point in the window, not just on the final day) and the excursion metrics.
- `max_favorable_excursion` / `max_adverse_excursion` are the best/worst
  price movement *in the direction predicted* during that window.
- This schema is sufficient to later compute: direction accuracy, target hit
  rate, MAE, MAPE, average return after prediction, MFE, MAE(adverse),
  accuracy by stock (join on symbol), by horizon (`horizon_days`), by
  confidence range (bucket `confidence`), monthly (bucket `prediction_date`),
  and overall. Phase 1 implements the evaluator that populates this table
  and a handful of the simpler aggregate queries; the richer breakdowns are
  straightforward SQL against this same table in a later phase.

**No look-ahead bias**: the evaluator only ever reads `price_bars` rows dated
on or before "today" when it runs, and only evaluates a prediction once
`target_evaluation_date` has actually occurred and its bar exists in
`price_bars`. It never uses data from after the evaluation date to grade an
earlier evaluation date.

## 9. Accuracy evaluator

Each run, before generating new predictions: find every row in `predictions`
whose `target_evaluation_date` has occurred and that has no matching row in
`accuracy_evaluations` yet. For each, pull the relevant `price_bars` window
for that symbol and compute the fields in §8. Predictions whose evaluation
date hasn't arrived yet, or whose data isn't in `price_bars` yet, are simply
left unevaluated until a future run.

## 10. Excel report (generated fresh each run, from SQLite only)

Excel is a **report**, never the source of truth, and is never hand-edited.
Workbook: `reports/YYYY-MM-DD.xlsx`, built with pandas + openpyxl, sheets:

1. **Dashboard** — run date, universe size, predictions made/skipped today,
   headline historical accuracy (direction accuracy %, target hit rate %,
   avg error %), disclaimer.
2. **Today's Predictions** — every symbol × horizon generated today.
3. **Prediction History** — full historical log from `predictions`.
4. **Accuracy** — `accuracy_evaluations` joined with their predictions.
5. **Stock Performance** — accuracy metrics aggregated by symbol.
6. **Historical Prices** — cached OHLCV from `price_bars` for the current
   universe.
7. **News/Sentiment (placeholder)** — pre-defined columns
   (symbol, headline, sentiment_score, source, date), explicitly labeled
   "Not available in Phase 1", left empty.
8. **Configuration** — universe methodology, indicator/score formula
   version, prompt version, data provider, generation timestamp, phase
   label.

## 11. Execution / CLI

Manual execution only in Phase 1 (no `launchd` job installed yet — deferred,
see §1). The entrypoint is written cleanly (proper exit codes, no
interactive prompts) so wiring it into `launchd` later is a small addition,
not a redesign.

```bash
python -m src.run_daily                                   # full Top 20, full run
python -m src.run_daily --symbols RELIANCE.NS,TCS.NS       # limit universe, for dev/debugging
python -m src.run_daily --dry-run                          # runs the full pipeline incl. Claude calls,
                                                             # prints what would be stored, writes nothing
                                                             # to SQLite or reports/
```

`--dry-run` and `--symbols` combine (`--dry-run --symbols RELIANCE.NS`) for
the cheapest possible test of a real Claude call.

Trading-day check: if invoked on a non-trading day (per `TradingCalendar`),
the run logs that fact and exits cleanly without writing an empty report.

## 12. Error handling and logging

- Every symbol is processed independently; a failure fetching data or
  calling/validating Claude for one symbol is logged and that symbol is
  skipped — the other symbols still complete.
- The Excel Dashboard and run logs both list which symbols were skipped and
  why.
- Logs go to `logs/YYYY-MM-DD.log` (Python `logging`, one file per run day).
- No secrets (API keys) are ever logged or committed; `.env`/API keys are
  git-ignored.

## 13. Testing

Unit tests (no live network or live Claude calls — yfinance and the
Anthropic client are mocked):

- Indicator calculations against known fixture input/output.
- Technical score formula.
- Universe selection determinism (same weights file → same top 20, every
  time).
- Trading-calendar math (`add_trading_days` skips weekends/holidays
  correctly).
- Prompt construction (correct data goes in, nothing extraneous).
- Claude JSON response validation (valid input accepted; each documented
  invalid shape rejected, and rejection never produces a partial/fake
  prediction).
- Accuracy evaluator math (direction correctness, target-hit, MAE/MAPE,
  excursions) against fixture price windows.

## 14. Cost control

One Claude call per stock per day (not per horizon) is the core cost
control. Prompts contain only the computed technical score and summarized
indicator values for that stock — never the full 3-month raw price series.
An empirical token/cost measurement for a real 20-stock run will be reported
as part of the Phase 1 completion summary (§15), rather than estimated
blindly here.

## 15. Phase 1 completion criteria

Before declaring Phase 1 done, an actual end-to-end run against real data
must show:

- [ ] Top 20 selected correctly from the weights file
- [ ] 3 months of history retrieved per symbol
- [ ] Indicators calculate correctly
- [ ] Claude API call succeeds
- [ ] Claude JSON is validated (including a deliberately-triggered invalid
      case, to prove skip-not-fake behavior)
- [ ] Predictions stored in SQLite, one row per stock × horizon
- [ ] No prediction row is ever overwritten
- [ ] Previously-eligible predictions get evaluated against real outcomes
- [ ] Accuracy metrics computed correctly
- [ ] Excel workbook generated with all 8 sheets
- [ ] Excel "Today's Predictions" and "Prediction History" both populated
- [ ] Errors logged; one symbol's failure doesn't stop the other 19
- [ ] No secrets committed to git
- [ ] Unit tests pass
- [ ] README complete (methodology, indicators used, prompt contract,
      accuracy methodology, how to run, how to extend each provider
      interface, known limitations)

Deliverables to present at completion: final project structure, DB schema,
one real sample prediction row, one real sample accuracy row (once any
prediction's horizon has elapsed — may require running the pipeline on
several different days, or seeding a backdated test prediction to
demonstrate the evaluator), Excel sheet structure, test results, known
limitations, and measured Claude API token usage for the run.

Phase 2 (fundamentals, news/sentiment, launchd automation, richer accuracy
breakdowns) does not start until the user reviews and approves this Phase 1
completion summary.
