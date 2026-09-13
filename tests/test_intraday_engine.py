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
