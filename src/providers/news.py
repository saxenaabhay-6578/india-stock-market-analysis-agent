from __future__ import annotations

from abc import ABC, abstractmethod


class NewsSentimentProvider(ABC):
    @abstractmethod
    def get_sentiment(self, symbol: str) -> dict: ...


class NotAvailableNewsSentimentProvider(NewsSentimentProvider):
    def get_sentiment(self, symbol: str) -> dict:
        return {"symbol": symbol, "status": "Not available in Phase 1"}
