from datetime import date, datetime
from .periods import half_month_window


def test_first_half_and_second_half_boundary():
    first = half_month_window(date(2026, 10, 15))
    second = half_month_window(date(2026, 10, 16))
    assert first["period_end"] == second["period_start"]
    assert second["period_start"] == "2026-10-15T16:00:00+00:00"


def test_february_leap_year_and_year_end():
    leap = half_month_window(date(2028, 2, 29))
    assert datetime.fromisoformat(leap["period_end"]) > datetime.fromisoformat(leap["period_start"])
    assert leap["period_end"] == "2028-02-29T16:00:00+00:00"
    assert half_month_window(date(2026, 12, 31))["period_end"] == "2026-12-31T16:00:00+00:00"
