"""Explicit prediction / execution / account-result review contracts."""

from __future__ import annotations

import math
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
PREDICTION_STATUSES = {"pending", "correct", "incorrect", "inconclusive", "history_incomplete"}
EXECUTION_STATUSES = {"unknown", "not_triggered", "rejected", "cancelled", "filled", "closed", "history_incomplete"}
PNL_STATUSES = {"unavailable", "unrealized", "realized", "history_incomplete"}


def timestamp(value: str | datetime, *, end_of_day: bool = False) -> str:
    """Canonical UTC ordering. Date-only cutoffs mean end of Shanghai day."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        value = value.strip()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10 and end_of_day:
            parsed = datetime.combine(parsed.date(), time.max)
    else:
        raise ValueError("valid ISO timestamp is required")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_review(payload: dict, judgment: dict) -> dict:
    """No execution means no invented position PnL, even if price went up."""
    result = dict(payload)
    result["as_of"] = timestamp(payload.get("as_of"))
    if result["as_of"] < judgment["as_of"]:
        raise ValueError("review cannot predate judgment")
    prediction = payload.get("prediction_status", "pending")
    execution = payload.get("execution_status", "unknown")
    pnl_status = payload.get("net_pnl_status", "unavailable")
    if prediction not in PREDICTION_STATUSES:
        raise ValueError("invalid prediction_status")
    if execution not in EXECUTION_STATUSES:
        raise ValueError("invalid execution_status")
    if pnl_status not in PNL_STATUSES:
        raise ValueError("invalid net_pnl_status")
    pnl = payload.get("net_pnl_after_costs")
    if pnl is not None:
        if execution not in {"filled", "closed"}:
            raise ValueError("net PnL requires actual execution")
        if isinstance(pnl, bool) or not isinstance(pnl, (int, float)) or not math.isfinite(pnl):
            raise ValueError("net PnL must be finite")
        if pnl_status not in {"unrealized", "realized"}:
            raise ValueError("net PnL requires explicit accounting status")
        if not payload.get("trade_ids") or not payload.get("cost_basis"):
            raise ValueError("net PnL requires trade_ids and cost_basis")
    elif pnl_status in {"unrealized", "realized"}:
        raise ValueError("accounting result requires net_pnl_after_costs")
    if pnl_status == "realized" and execution != "closed":
        raise ValueError("realized PnL requires closed execution")
    result.update(prediction_status=prediction, execution_status=execution,
                  net_pnl_status=pnl_status, net_pnl_after_costs=pnl)
    return result


def review_prediction(*, predicted_direction: str, observed_return: float | None,
                      matured: bool, history_complete: bool = True) -> str:
    """Direction-only observation, explicitly independent of execution/PnL."""
    if not history_complete:
        return "history_incomplete"
    if not matured:
        return "pending"
    if observed_return is None:
        return "inconclusive"
    if not math.isfinite(observed_return):
        raise ValueError("observed_return must be finite")
    if predicted_direction not in {"up", "down", "flat"}:
        raise ValueError("predicted_direction must be up, down, or flat")
    actual = "up" if observed_return > 0 else "down" if observed_return < 0 else "flat"
    return "correct" if actual == predicted_direction else "incorrect"
