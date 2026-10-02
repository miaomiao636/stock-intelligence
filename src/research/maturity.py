"""Forward-recorded direction forecasts and deterministic maturity labels.

No text parsing, price-target inference, weekday fallback, model calls or trade
execution. All outcomes concern direction from a frozen observable quote to
the Nth exchange-session close AFTER the judgment's Shanghai calendar day.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from src.research.review import SHANGHAI, review_prediction, timestamp

PROTOCOL_VERSION = "direction_maturity_v1"
DIRECTIONS = {"up", "down", "flat"}


def local_day(value: str) -> str:
    return datetime.fromisoformat(timestamp(value).replace("Z", "+00:00")).astimezone(SHANGHAI).date().isoformat()


def positive_number(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def build_forecast_spec(rec: dict, *, stock_code: str, snapshots: list[dict]) -> dict:
    """Use only an explicit claim and the model-input quote, never a later refresh."""
    result = {"direction": rec.get("forecast_direction"),
              "horizon_sessions": rec.get("forecast_horizon_sessions"),
              "reference_basis": None}
    quotes = []
    for evidence in snapshots:
        snapshot = evidence.get("snapshot") or {}
        if evidence.get("kind") != "market_snapshot" or evidence.get("purpose") != "forecast_input":
            continue
        result["reference_basis"] = "model_input_snapshot"
        if evidence.get("history_incomplete"):
            continue
        quote = (snapshot.get("realtime_stock_prices") or {}).get(stock_code)
        if quote is None and (snapshot.get("stock_code") or snapshot.get("code")) == stock_code:
            quote = snapshot
        if not isinstance(quote, dict) or quote.get("error") or quote.get("source_time_reliable") is not True:
            continue
        price = quote.get("price") if quote.get("price") is not None else quote.get("close")
        quote_time = quote.get("quote_time") or quote.get("as_of") or quote.get("data_as_of")
        if not positive_number(price) or not quote_time:
            continue
        try:
            quotes.append((timestamp(quote_time), float(price), evidence["evidence_id"]))
        except (TypeError, ValueError):
            continue
    if quotes:
        quote_time, price, evidence_id = max(quotes)
        result.update(reference_as_of=quote_time, reference_price=price,
                      reference_evidence_id=evidence_id, price_basis="raw")
    return result


def normalize_forecast_spec(value, *, judgment: dict) -> dict:
    """Mark omissions as exclusions; never reconstruct an older prediction."""
    raw = value if isinstance(value, dict) else {}
    direction = raw.get("direction")
    horizon = raw.get("horizon_sessions")
    result = {"protocol_version": PROTOCOL_VERSION, "status": "excluded",
              "direction": direction if isinstance(direction, str) and direction in DIRECTIONS else None,
              "horizon_sessions": horizon if type(horizon) is int and 1 <= horizon <= 60 else None,
              "reference_price": None, "reference_as_of": None,
              "reference_evidence_id": None, "price_basis": "raw", "probability": None,
              "reference_basis": None,
              "horizon_basis": "nth_exchange_session_after_judgment_day",
              "reason": "explicit_forecast_missing"}
    if result["direction"] is None or result["horizon_sessions"] is None:
        return result
    if raw.get("reference_basis") != "model_input_snapshot":
        return {**result, "reason": "forecast_input_snapshot_missing"}
    result["reference_basis"] = "model_input_snapshot"
    result["reason"] = "reference_quote_missing"
    if not positive_number(raw.get("reference_price")) or not raw.get("reference_evidence_id"):
        return result
    try:
        reference_as_of = timestamp(raw.get("reference_as_of"))
    except (TypeError, ValueError):
        return result
    if reference_as_of > judgment["as_of"]:
        return {**result, "reason": "reference_quote_from_future"}
    if raw.get("price_basis") != "raw":
        return {**result, "reason": "reference_price_basis_unknown"}
    # A later batch import cannot turn a pre-existing report into a live trial.
    if local_day(judgment["known_at"]) != local_day(judgment["as_of"]):
        return {**result, "reason": "not_recorded_on_forecast_day"}
    claimed = datetime.fromisoformat(judgment["as_of"].replace("Z", "+00:00"))
    recorded = datetime.fromisoformat(judgment["known_at"].replace("Z", "+00:00"))
    reference = datetime.fromisoformat(reference_as_of.replace("Z", "+00:00"))
    if not timedelta(0) <= recorded - claimed <= timedelta(minutes=15):
        return {**result, "reason": "not_recorded_at_forecast_time"}
    local_claim, local_ref = claimed.astimezone(SHANGHAI), reference.astimezone(SHANGHAI)
    previous_close_before_open = (local_claim.time() < time(9, 30)
        and local_ref.date() < local_claim.date() and local_ref.time() >= time(15)
        and claimed - reference <= timedelta(days=14))
    if claimed - reference > timedelta(minutes=15) and not previous_close_before_open:
        return {**result, "reason": "reference_quote_stale"}
    result.update(status="registered", reason=None, reference_price=float(raw["reference_price"]),
                  reference_as_of=reference_as_of, reference_evidence_id=raw["reference_evidence_id"],
                  registered_at=judgment["known_at"])
    return result


def cohort_key(judgment: dict) -> tuple | None:
    spec = judgment.get("forecast_spec") or {}
    if spec.get("status") != "registered":
        return None
    return judgment["stock_code"], local_day(judgment["as_of"]), spec["horizon_sessions"]


def maturity_target(judgment: dict, *, as_of: str, sessions) -> tuple[str | None, str]:
    """The provider must return a complete exchange calendar for the interval.

    None/malformed input is unavailable, never guessed from weekdays. A shorter
    valid calendar means pending, including during weekends and holidays.
    """
    if not isinstance(sessions, (list, tuple, set)):
        return None, "calendar_unavailable"
    start, end = local_day(judgment["as_of"]), local_day(as_of)
    try:
        days = sorted({date.fromisoformat(day).isoformat() for day in sessions})
    except (ValueError, TypeError):
        return None, "calendar_unavailable"
    if any(day < start or day > end for day in days):
        return None, "calendar_unavailable"
    after = [day for day in days if start < day <= end]
    horizon = judgment["forecast_spec"]["horizon_sessions"]
    if len(after) < horizon:
        return None, "pending"
    target = after[horizon - 1]
    if timestamp(target + "T15:00:00+08:00") > as_of:
        return target, "pending"
    return target, "matured"


def _adjustment_evidence(value, reference_day, target):
    """Retain only verifiable date/factor pairs, never arbitrary provider data."""
    if (not isinstance(value, dict) or value.get("reference_trade_date") != reference_day
            or value.get("target_trade_date") != target):
        return None
    rows = value.get("factors")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 6000:
        return None
    values = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        day, factor = row.get("trade_date"), row.get("adj_factor")
        try:
            if date.fromisoformat(day).isoformat() != day:
                return None
        except (TypeError, ValueError):
            return None
        if not reference_day <= day <= target or day in values or not positive_number(factor):
            return None
        values[day] = float(factor)
    if reference_day not in values or target not in values:
        return None
    return {"reference_trade_date": reference_day, "target_trade_date": target,
            "factors": [{"trade_date": day, "adj_factor": values[day]} for day in sorted(values)]}


def maturity_review(judgment: dict, *, target: str, observation, as_of: str) -> dict:
    """Build a direction label only from an exact target-date closing quote.

    Version 1 deliberately excludes any interval with corporate actions. The
    adapter must positively verify adjustment-factor equality over the period;
    raw ex-rights moves must not be called predictive successes or failures.
    """
    result = {"review_kind": PROTOCOL_VERSION, "as_of": as_of,
              "target_trade_date": target, "prediction_status": "inconclusive",
              "execution_status": "unknown", "net_pnl_status": "unavailable",
              "net_pnl_after_costs": None, "observed_return_pct": None,
              "actual_direction": None, "exclusion_reason": "target_close_unavailable",
              "forecast_spec": judgment["forecast_spec"], "outcome_evidence": None,
              "limitations": ["仅评估价格方向，不代表可成交、策略收益或账户盈亏。",
                              "LLM 置信评分不是经过校准的上涨概率。"]}
    if not isinstance(observation, dict) or not observation:
        return result
    if observation.get("trade_date") != target:
        return {**result, "exclusion_reason": "target_date_mismatch"}
    if not positive_number(observation.get("close")):
        return {**result, "exclusion_reason": "invalid_close"}
    try:
        quote_time = timestamp(observation.get("as_of"))
        if local_day(quote_time) != target or quote_time < timestamp(target + "T15:00:00+08:00") or quote_time > as_of:
            raise ValueError("not target close")
    except (TypeError, ValueError):
        return {**result, "exclusion_reason": "observation_time_invalid"}
    source = observation.get("source")
    if not isinstance(source, str) or not source.strip():
        return {**result, "exclusion_reason": "observation_source_missing"}
    if (observation.get("price_basis") != "raw" or observation.get("corporate_action_checked") is not True
            or type(observation.get("corporate_action_detected")) is not bool):
        return {**result, "exclusion_reason": "price_adjustment_unverified"}
    evidence = {"source": source.strip(), "trade_date": target,
                "as_of": quote_time, "close": float(observation["close"]),
                "price_basis": "raw", "corporate_action_checked": True,
                "corporate_action_detected": observation.get("corporate_action_detected") is True}
    if "adjustment_evidence" in observation:
        factors = _adjustment_evidence(observation["adjustment_evidence"],
            local_day(judgment["forecast_spec"]["reference_as_of"]), target)
        if factors is None:
            return {**result, "exclusion_reason": "price_adjustment_unverified"}
        changed = len({row["adj_factor"] for row in factors["factors"]}) > 1
        if observation.get("corporate_action_detected") is not changed:
            return {**result, "exclusion_reason": "price_adjustment_unverified"}
        evidence["adjustment_evidence"] = factors
    result["outcome_evidence"] = evidence
    if observation.get("corporate_action_detected") is not False:
        return {**result, "exclusion_reason": "corporate_action_requires_adjusted_reference"}
    change = (float(observation["close"]) / judgment["forecast_spec"]["reference_price"] - 1) * 100
    if not math.isfinite(change):
        return {**result, "exclusion_reason": "invalid_return"}
    result.update(prediction_status=review_prediction(predicted_direction=judgment["forecast_spec"]["direction"],
                  observed_return=change, matured=True), observed_return_pct=change,
                  actual_direction="up" if change > 0 else "down" if change < 0 else "flat",
                  exclusion_reason=None,
                  outcome_evidence=evidence)
    return result
