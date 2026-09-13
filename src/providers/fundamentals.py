from __future__ import annotations

from abc import ABC, abstractmethod


class FundamentalsProvider(ABC):
    @abstractmethod
    def get_fundamentals(self, symbol: str) -> dict: ...


class NotAvailableFundamentalsProvider(FundamentalsProvider):
    def get_fundamentals(self, symbol: str) -> dict:
        return {"symbol": symbol, "status": "Not available in Phase 1"}
