from __future__ import annotations

from datetime import datetime, time, timedelta

from src.universe.trading_calendar import TradingCalendar

CHECKPOINTS: list[time] = [
    time(9, 15), time(10, 15), time(11, 15), time(12, 15),
    time(13, 15), time(14, 15), time(15, 15),
]

NEXT_CHECKPOINT: dict[time, time | None] = {
    time(9, 15): time(10, 15),
    time(10, 15): time(11, 15),
    time(11, 15): time(12, 15),
    time(12, 15): time(13, 15),
    time(13, 15): time(14, 15),
    time(14, 15): time(15, 15),
    time(15, 15): None,
}

# A checkpoint is only "current" within this window after its buffered
# eligibility time, so a run triggered hours late (or accidentally at
# night) does not resolve to a stale checkpoint and redo pointless work.
ELIGIBILITY_WINDOW = timedelta(hours=1)


class IntradayMarketCalendar:
    def __init__(self, trading_calendar: TradingCalendar, buffer_minutes: int = 10):
        self._trading_calendar = trading_calendar
        self._buffer = timedelta(minutes=buffer_minutes)

    def current_checkpoint(self, now: datetime) -> datetime | None:
        """Resolve the latest eligible checkpoint at or before now. Parameter now must be timezone-aware (e.g., Asia/Kolkata for NSE data)."""
        if not self._trading_calendar.is_trading_day(now.date()):
            return None
        eligible = []
        for checkpoint_time in CHECKPOINTS:
            checkpoint_dt = datetime.combine(now.date(), checkpoint_time, tzinfo=now.tzinfo)
            eligible_from = checkpoint_dt + self._buffer
            eligible_until = eligible_from + ELIGIBILITY_WINDOW
            if eligible_from <= now <= eligible_until:
                eligible.append(checkpoint_dt)
        if not eligible:
            return None
        return max(eligible)
