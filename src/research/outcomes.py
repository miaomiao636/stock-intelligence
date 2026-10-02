"""Bounded closing-data adapter for direction reviews, never order execution.

Uses the existing Tushare account only when explicitly run by the closing job
or CLI. HTTPS failure/permission gaps remain unavailable; no HTTP fallback,
weekday calendar approximation, live-price substitution or paid model calls.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re
import time

from src.research.maturity import local_day


class OutcomeBudgetExhausted(Exception):
    """A deferred task, not an unavailable market observation or failed label."""


class OutcomeProvider:
    def __init__(self, *, max_requests=40, budget_seconds=45):
        self.max_requests = max_requests
        self.deadline = time.monotonic() + budget_seconds
        self.diagnostics = Counter()
        self._cache = {}

    def _query(self, api, **params):
        if api not in {"trade_cal", "daily", "adj_factor"}:
            return None
        key = (api, tuple(sorted(params.items())))
        if key in self._cache:
            return self._cache[key]
        remaining = self.deadline - time.monotonic()
        if self.diagnostics["requests"] >= self.max_requests or remaining <= 0:
            self.diagnostics["budget_exhausted"] += 1
            raise OutcomeBudgetExhausted()
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            self.diagnostics["not_configured"] += 1
            return None
        import requests
        self.diagnostics["requests"] += 1
        fields = {"trade_cal": "cal_date,is_open", "daily": "ts_code,trade_date,close",
                  "adj_factor": "ts_code,trade_date,adj_factor"}[api]
        result = None
        try:
            response = requests.post("https://api.tushare.pro",
                json={"api_name": api, "token": token, "params": params, "fields": fields},
                timeout=min(8, remaining), allow_redirects=False)
            if response.status_code != 200:
                raise ValueError("provider HTTP failure")
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0:
                raise ValueError("provider data unavailable")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("invalid provider data")
            names, rows = data.get("fields"), data.get("items")
            if (not isinstance(names, list) or not all(isinstance(n, str) for n in names)
                    or len(set(names)) != len(names) or not set(fields.split(",")).issubset(names)
                    or not isinstance(rows, list) or len(rows) > 6000
                    or any(not isinstance(row, list) or len(row) != len(names) for row in rows)):
                raise ValueError("invalid provider rows")
            result = [dict(zip(names, row)) for row in rows]
        except Exception:
            # Provider errors may echo authentication inputs. Only retain counts.
            self.diagnostics["fetch_failed"] += 1
        self._cache[key] = result
        return result

    def calendar(self, start_date, end_date):
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if end < start or (end - start).days > 730:
            return None
        rows = self._query("trade_cal", exchange="SSE", start_date=start.strftime("%Y%m%d"),
                           end_date=end.strftime("%Y%m%d"))
        if not isinstance(rows, list):
            return None
        expected = {(start + timedelta(days=i)).strftime("%Y%m%d") for i in range((end - start).days + 1)}
        values = {}
        for row in rows:
            day, opened = row.get("cal_date"), row.get("is_open")
            if day not in expected or day in values or type(opened) is bool or str(opened) not in {"0", "1"}:
                return None
            values[day] = str(opened)
        # Requiring closed days too detects truncated calendars (not just a list
        # of weekdays); incomplete coverage must not bring a due date forward.
        if set(values) != expected:
            return None
        return sorted(datetime.strptime(day, "%Y%m%d").date().isoformat()
                      for day, opened in values.items() if opened == "1")

    @staticmethod
    def _positive(value):
        return type(value) in {int, float} and math.isfinite(value) and value > 0

    def observation(self, stock_code, target_date, reference_as_of):
        # Initial protocol supports A shares only, not ETFs or unverified codes.
        if not isinstance(stock_code, str) or not re.fullmatch(r"[036489]\d{5}", stock_code):
            self.diagnostics["unsupported_instrument"] += 1
            return None
        suffix = "SH" if stock_code.startswith("6") else "BJ" if stock_code[0] in "489" else "SZ"
        symbol = stock_code + "." + suffix
        target = date.fromisoformat(target_date).strftime("%Y%m%d")
        reference = date.fromisoformat(local_day(reference_as_of)).strftime("%Y%m%d")
        if reference > target:
            return None
        rows = self._query("daily", ts_code=symbol, start_date=target, end_date=target)
        if not isinstance(rows, list) or len(rows) != 1:
            return None
        row = rows[0]
        if row.get("ts_code") != symbol or row.get("trade_date") != target or not self._positive(row.get("close")):
            return None
        factors = self._query("adj_factor", ts_code=symbol, start_date=reference, end_date=target)
        valid, values = isinstance(factors, list), {}
        for factor in factors or []:
            day, value = factor.get("trade_date"), factor.get("adj_factor")
            if (not isinstance(day, str) or not reference <= day <= target or day in values
                    or factor.get("ts_code") != symbol or not self._positive(value)):
                valid = False
                break
            values[day] = value
        checked = valid and reference in values and target in values
        changed = any(value != values[reference] for value in values.values()) if checked else None
        result = {"trade_date": target_date, "close": row["close"],
                  "as_of": target_date + "T15:00:00+08:00", "source": "tushare:daily+adj_factor",
                  "price_basis": "raw", "corporate_action_checked": bool(checked),
                  "corporate_action_detected": changed}
        if checked:
            result["adjustment_evidence"] = {
                "reference_trade_date": date.fromisoformat(local_day(reference_as_of)).isoformat(),
                "target_trade_date": target_date,
                "factors": [{"trade_date": datetime.strptime(day, "%Y%m%d").date().isoformat(),
                             "adj_factor": value} for day, value in sorted(values.items())],
            }
        return result


def run_maturity_review(data_dir, *, as_of=None):
    """Bounded job. Does not create a new ledger or alter trading records."""
    path = Path(data_dir) / "stock_intelligence.db"
    if not path.exists():
        return {"status": "unavailable", "reason": "research_database_missing", "affects_trading": False}
    from src.research.store import ResearchStore
    provider = OutcomeProvider()
    result = ResearchStore(path).review_matured_predictions(
        as_of=as_of or datetime.now(timezone.utc).isoformat(),
        calendar_provider=provider.calendar, observation_provider=provider.observation)
    incomplete = (result.get("calendar_unavailable_count", 0) + result.get("unavailable_count", 0)
                  + result.get("deferred_count", 0))
    return {**result, "status": "degraded" if incomplete else "ok",
            "provider_diagnostics": dict(provider.diagnostics)}
