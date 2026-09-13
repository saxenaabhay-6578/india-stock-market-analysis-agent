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

Using only the data above, predict the price and direction for the NEXT hourly checkpoint (1 hour from now). Do not invent data not given above.

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
