from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.universe.intraday_calendar import NEXT_CHECKPOINT, IntradayMarketCalendar
from src.universe.trading_calendar import NseStaticHolidayCalendar

IST = ZoneInfo("Asia/Kolkata")


def test_next_checkpoint_maps_each_hour_forward():
    assert NEXT_CHECKPOINT[time(9, 15)] == time(10, 15)
    assert NEXT_CHECKPOINT[time(14, 15)] == time(15, 15)


def test_next_checkpoint_is_none_at_15_15():
    assert NEXT_CHECKPOINT[time(15, 15)] is None


def test_current_checkpoint_none_on_holiday():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar())
    now = datetime(2026, 9, 14, 10, 25, tzinfo=IST)  # Ganesh Chaturthi, a Monday
    assert cal.current_checkpoint(now) is None


def test_current_checkpoint_none_on_weekend():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar())
    now = datetime(2026, 9, 12, 10, 25, tzinfo=IST)  # Saturday
    assert cal.current_checkpoint(now) is None


def test_current_checkpoint_none_before_buffer_elapsed():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 9, 20, tzinfo=IST)  # Tuesday, only 5 min after 09:15
    assert cal.current_checkpoint(now) is None


def test_current_checkpoint_resolves_to_09_15_once_buffer_elapsed():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 9, 25, tzinfo=IST)  # Tuesday
    result = cal.current_checkpoint(now)
    assert result == datetime(2026, 9, 15, 9, 15, tzinfo=IST)


def test_current_checkpoint_resolves_to_latest_eligible_mid_day():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 12, 47, tzinfo=IST)  # simulates a missed-run recovery scenario
    result = cal.current_checkpoint(now)
    assert result == datetime(2026, 9, 15, 12, 15, tzinfo=IST)


def test_current_checkpoint_resolves_to_15_15_as_final_checkpoint():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 15, 30, tzinfo=IST)
    result = cal.current_checkpoint(now)
    assert result == datetime(2026, 9, 15, 15, 15, tzinfo=IST)


def test_current_checkpoint_none_well_after_market_close():
    cal = IntradayMarketCalendar(NseStaticHolidayCalendar(), buffer_minutes=10)
    now = datetime(2026, 9, 15, 18, 0, tzinfo=IST)  # 3+ hours after the last checkpoint
    assert cal.current_checkpoint(now) is None
