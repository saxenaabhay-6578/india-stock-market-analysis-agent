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
