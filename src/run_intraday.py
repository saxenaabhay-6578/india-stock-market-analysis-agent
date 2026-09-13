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

# Smallest rolling-window size behind sma20/bollinger/volatility/momentum in
# compute_intraday_indicators. Below this many post-filter bars those values
# silently resolve to NaN (and trend_direction silently resolves to "DOWN",
# since any comparison with NaN is False in Python) -- so we skip the symbol
# rather than hand Claude NaN-poisoned indicators.
MIN_BARS_FOR_INDICATORS = 20

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

    missed = find_missed_checkpoints(conn, checkpoint)
    if missed:
        logger.warning("Missed intraday checkpoints today (no prediction generated, not backfilled): %s", missed)

    start = checkpoint - timedelta(days=INTRADAY_LOOKBACK_DAYS)

    made = 0
    skipped = 0
    for symbol in symbols:
        try:
            history_for_indicators = None
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
                # Reuse the frame just fetched for bar-caching as the indicator input
                # instead of re-fetching identical data from the provider a second time.
                history_for_indicators = hourly

            if next_checkpoint_time is None:
                continue  # 15:15: evaluation-only, never generate a new prediction

            if not dry_run:
                existing = conn.execute(
                    "SELECT 1 FROM intraday_predictions WHERE symbol = ? AND prediction_timestamp = ? "
                    "AND prediction_type = 'next_hour'",
                    (symbol, checkpoint.isoformat()),
                ).fetchone()
                if existing is not None:
                    logger.info(
                        "Prediction for %s at %s already exists, skipping Claude call",
                        symbol, checkpoint.isoformat(),
                    )
                    continue

            if history_for_indicators is None:
                history_for_indicators = provider.get_intraday_history(
                    symbol, start=start, end=checkpoint, interval=INTRADAY_INTERVAL,
                )
            if history_for_indicators is None or history_for_indicators.empty:
                logger.warning("Skipping %s: no indicator history", symbol)
                skipped += 1
                continue
            filtered = select_bars_up_to_checkpoint(history_for_indicators, checkpoint)
            if filtered.empty:
                logger.warning("Skipping %s: no bars available before checkpoint", symbol)
                skipped += 1
                continue
            if len(filtered) < MIN_BARS_FOR_INDICATORS:
                logger.warning(
                    "Skipping %s: insufficient bar history for reliable indicators (%d bars, need >= %d)",
                    symbol, len(filtered), MIN_BARS_FOR_INDICATORS,
                )
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
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", symbol, exc)
            skipped += 1
            continue

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
