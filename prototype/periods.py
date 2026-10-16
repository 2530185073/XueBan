"""Calendar reporting windows. End is exclusive, Asia/Shanghai is authoritative."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def half_month_window(day: date):
    local = ZoneInfo("Asia/Shanghai")
    start_day = 1 if day.day <= 15 else 16
    start = datetime(day.year, day.month, start_day, tzinfo=local)
    if start_day == 1:
        end = datetime(day.year, day.month, 16, tzinfo=local)
    else:
        year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
        end = datetime(year, month, 1, tzinfo=local)
    return {"timezone": "Asia/Shanghai", "period_start": start.astimezone(timezone.utc).isoformat(), "period_end": end.astimezone(timezone.utc).isoformat(), "end_exclusive": True}
