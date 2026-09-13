from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta

from src.universe.nse_holidays import NSE_HOLIDAYS


class TradingCalendar(ABC):
    @abstractmethod
    def is_trading_day(self, d: date) -> bool: ...

    @abstractmethod
    def add_trading_days(self, d: date, n: int) -> date: ...

    @abstractmethod
    def trading_days_between(self, start: date, end: date) -> int: ...


class NseStaticHolidayCalendar(TradingCalendar):
    def __init__(self, holidays: dict[int, list[str]] = NSE_HOLIDAYS):
        self._holidays = {
            date.fromisoformat(d) for year_holidays in holidays.values() for d in year_holidays
        }

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def add_trading_days(self, d: date, n: int) -> date:
        if n < 0:
            raise ValueError("n must be non-negative")
        current = d
        remaining = n
        while remaining > 0:
            current += timedelta(days=1)
            if self.is_trading_day(current):
                remaining -= 1
        return current

    def trading_days_between(self, start: date, end: date) -> int:
        if end < start:
            raise ValueError("end must be on or after start")
        count = 0
        current = start
        while current < end:
            current += timedelta(days=1)
            if self.is_trading_day(current):
                count += 1
        return count
