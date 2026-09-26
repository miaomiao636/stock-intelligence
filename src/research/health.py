"""Read-only research health metadata; absence is never zero or success."""
from datetime import date


def tracking_freshness(data, today=None):
    today = today or date.today()
    value = data.get("tracking_date") or data.get("date")
    try:
        age = (today - date.fromisoformat(str(value)[:10])).days
    except (ValueError, TypeError):
        return {"status": "unknown", "as_of": value, "age_calendar_days": None,
                "reason": "跟踪时间缺失，不能视为实时结果"}
    status = "stale" if age > 7 else "future_invalid" if age < 0 else "recent_unverified"
    return {"status": status, "as_of": value, "age_calendar_days": age,
            "reason": "按自然日粗检；近期记录仍需对照交易日历，不把节假日无报告判成故障"}
