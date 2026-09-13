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

    if not isinstance(data, dict):
        raise PredictionValidationError("response is not a JSON object")

    if "current_price" not in data or "horizons" not in data:
        raise PredictionValidationError("missing current_price or horizons key")

    try:
        price_matches = abs(float(data["current_price"]) - expected_current_price) <= max(
            0.01 * expected_current_price, 0.5
        )
    except (ValueError, TypeError) as exc:
        raise PredictionValidationError(f"current_price is not numeric: {exc}") from exc
    if not price_matches:
        raise PredictionValidationError("current_price echoed back does not match sent value")

    horizons = data["horizons"]
    if not isinstance(horizons, dict):
        raise PredictionValidationError("horizons is not a JSON object")
    missing_horizons = set(HORIZONS) - set(horizons)
    if missing_horizons:
        raise PredictionValidationError(f"missing horizon keys: {missing_horizons}")

    for key, horizon in horizons.items():
        if not isinstance(horizon, dict):
            raise PredictionValidationError(f"horizon {key} is not a JSON object")
        missing_fields = REQUIRED_HORIZON_FIELDS - set(horizon)
        if missing_fields:
            raise PredictionValidationError(f"horizon {key} missing fields: {missing_fields}")
        if horizon["direction"] not in ALLOWED_DIRECTIONS:
            raise PredictionValidationError(f"horizon {key} invalid direction: {horizon['direction']}")
        try:
            range_ok = float(horizon["range_low"]) <= float(horizon["range_high"])
            confidence = float(horizon["confidence"])
        except (ValueError, TypeError) as exc:
            raise PredictionValidationError(f"horizon {key} has malformed field: {exc}") from exc
        if not range_ok:
            raise PredictionValidationError(f"horizon {key} range_low > range_high")
        if not (0 <= confidence <= 100):
            raise PredictionValidationError(f"horizon {key} confidence out of range: {confidence}")
        key_risks = horizon["key_risks"]
        if not isinstance(key_risks, list) or not key_risks or not all(isinstance(r, str) for r in key_risks):
            raise PredictionValidationError(f"horizon {key} key_risks must be a non-empty list of strings")

    return data


def request_prediction(client, symbol: str, current_price: float, technical_score: float, indicators: dict[str, Any]) -> dict[str, Any] | None:
    prompt = build_prompt(symbol, current_price, technical_score, indicators)
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text
        except Exception as exc:
            logger.warning("Claude API call failed for %s (attempt %d): %s", symbol, attempt + 1, exc)
            continue
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
