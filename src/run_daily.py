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
