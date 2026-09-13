from datetime import date

from src.universe.trading_calendar import NseStaticHolidayCalendar


def test_weekday_is_trading_day():
    cal = NseStaticHolidayCalendar()
    assert cal.is_trading_day(date(2026, 9, 7))  # Monday


def test_weekend_is_not_trading_day():
    cal = NseStaticHolidayCalendar()
    assert not cal.is_trading_day(date(2026, 9, 12))  # Saturday
    assert not cal.is_trading_day(date(2026, 9, 13))  # Sunday


def test_holiday_is_not_trading_day():
    cal = NseStaticHolidayCalendar()
    assert not cal.is_trading_day(date(2026, 10, 2))  # Gandhi Jayanti


def test_add_trading_days_skips_weekend():
    cal = NseStaticHolidayCalendar()
    friday = date(2026, 9, 4)
    assert cal.add_trading_days(friday, 1) == date(2026, 9, 7)  # Monday


def test_add_trading_days_skips_holiday():
    cal = NseStaticHolidayCalendar()
    before_holiday = date(2026, 10, 1)  # Thursday
    # 2026-10-02 is a holiday, 2026-10-03/04 is Sat/Sun -> next trading day is 10-05
    assert cal.add_trading_days(before_holiday, 1) == date(2026, 10, 5)


def test_trading_days_between_counts_only_trading_days():
    cal = NseStaticHolidayCalendar()
    start = date(2026, 9, 4)  # Friday
    end = date(2026, 9, 8)  # Tuesday
    assert cal.trading_days_between(start, end) == 2  # Mon + Tue
