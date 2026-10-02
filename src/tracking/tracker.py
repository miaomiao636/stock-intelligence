# -*- coding: utf-8 -*-
"""Recommendation Tracker - tracks performance of historical recommendations"""

import json
import math
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.data_collectors.trading_calendar import count_trading_days
from src.utils.cost_calculator import calculate_trade_costs


class RecommendationTracker:
    """Track and evaluate historical stock recommendations"""

    TERMINAL_STATUSES = {
        "hit_target",
        "stopped_out",
        "deep_loss",
        "expired",
        "entry_invalidated",
        "expired_untriggered",
    }
    ENTERED_EXECUTION_STATUSES = {"filled", "theoretical_trigger"}

    def __init__(self, data_dir: Path = None, execution_lookup: Callable = None):
        self.data_dir = Path(data_dir or Path(__file__).parent.parent.parent / "data")
        self.tracker_dir = self.data_dir / "tracker"
        self.tracker_dir.mkdir(parents=True, exist_ok=True)
        self.recommendations_dir = self.data_dir / "recommendations"
        self.execution_lookup = execution_lookup or self._lookup_filled_buy

    def _lookup_filled_buy(self, recommendation_id: str, code: str) -> Optional[Dict]:
        """Read the real buy fill without initializing or mutating the ledger."""
        if not recommendation_id:
            return None
        db_path = self.data_dir / "stock_intelligence.db"
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT o.order_id,o.recommendation_id,o.code,o.stop_price,o.target_price,
                           o.instrument_type,t.trade_id,t.quantity,t.price AS fill_price,
                           t.amount AS fill_amount,t.fees AS entry_fees,
                           t.slippage AS entry_slippage,t.executed_at,t.trade_date
                    FROM trading_orders o
                    JOIN trading_trades t ON t.order_id=o.order_id
                    WHERE o.recommendation_id=? AND o.code=?
                      AND o.action='buy' AND o.status='filled'
                    ORDER BY t.executed_at DESC LIMIT 1
                    """,
                    (recommendation_id, code),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    @staticmethod
    def _number(value, default=0):
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_actionable(stock: Dict) -> bool:
        """Only score recommendations that were actually ready to enter.

        Older reports did not always include an action field; those remain
        eligible for backward compatibility. Explicit track/watch ideas are
        observations, not trades, and must not contaminate win-rate metrics.
        """
        action = str(stock.get("action") or "").strip().lower()
        return (
            action in {"", "setup_ready", "buy"}
            and stock.get("trade_eligible", True) is not False
        )

    @staticmethod
    def _is_date_name(value: str) -> bool:
        try:
            return len(value) == 10 and date.fromisoformat(value).isoformat() == value
        except (ValueError, TypeError):
            return False

    def _tracking_files(self):
        return sorted((path for path in self.tracker_dir.glob("*.json")
                       if self._is_date_name(path.stem)), reverse=True)

    @classmethod
    def _safe_diagnostics(cls, values):
        """Only propagate whitelisted relative file identities, never raw errors."""
        result = []
        if not isinstance(values, list):
            return result
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            parts = item["path"].split("/")
            valid = (len(parts) == 2 and parts[0] == "tracker" and parts[1].endswith(".json")
                     and cls._is_date_name(parts[1][:-5])) or (
                         len(parts) == 3 and parts[0] == "recommendations"
                         and cls._is_date_name(parts[1]) and parts[2] == "morning.json")
            if not valid or item.get("kind") not in {"invalid_encoding", "invalid_json", "invalid_schema", "read_error"}:
                continue
            safe = {"path": item["path"], "kind": item["kind"]}
            if isinstance(item.get("offset"), int) and item["offset"] >= 0:
                safe["offset"] = item["offset"]
            if safe not in result:
                result.append(safe)
        return result

    def _read_json(self, path, field, diagnostics):
        """Reject malformed inputs per file; leave the original bytes untouched."""
        def finite_number(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("non-finite JSON number")
            return number

        error = None
        try:
            if path.is_symlink():
                raise OSError("symbolic links are not tracking inputs")
            payload = json.loads(path.read_text(encoding="utf-8"),
                                 parse_constant=finite_number, parse_float=finite_number)
        except UnicodeDecodeError as exc:
            error = {"kind": "invalid_encoding", "offset": exc.start}
        except json.JSONDecodeError as exc:
            error = {"kind": "invalid_json", "offset": exc.pos}
        except ValueError:
            error = {"kind": "invalid_json"}
        except OSError:
            error = {"kind": "read_error"}
        else:
            if (not isinstance(payload, dict) or not isinstance(payload.get(field), list)
                    or any(not isinstance(row, dict) for row in payload[field])
                    or (field == "tracks" and not isinstance(payload.get("summary", {}), dict))):
                error = {"kind": "invalid_schema"}
            else:
                return payload
        diagnostic = {"path": path.relative_to(self.data_dir).as_posix(), **error}
        diagnostics.extend(self._safe_diagnostics([diagnostic]))
        return None

    def _with_quality(self, payload, diagnostics):
        result = dict(payload)
        previous = result.get("data_quality")
        previous = previous if isinstance(previous, dict) else {}
        inherited = previous.get("diagnostics")
        inherited = inherited if isinstance(inherited, list) else []
        issues = self._safe_diagnostics([*inherited, *diagnostics])
        incomplete = bool(issues or previous.get("history_incomplete") or previous.get("status") == "incomplete")
        result["data_quality"] = {"status": "incomplete" if incomplete else "ok",
                                  "history_incomplete": incomplete, "diagnostics": issues,
                                  "scope": "file_integrity_only"}
        result["summary"] = dict(result.get("summary") or {})
        if incomplete:
            result["summary"]["statistics_trustworthy"] = False
        if incomplete and result.get("status") != "error":
            result["status"] = "partial_success"
        else:
            result.setdefault("status", "success")
        return result

    def _load_previous_states(self, date_str: str, diagnostics=None, state_dates=None) -> Dict[str, Dict]:
        """Load the newest known state for every recommendation up to date_str."""
        states: Dict[str, Dict] = {}
        diagnostics = diagnostics if diagnostics is not None else []
        state_dates = state_dates if state_dates is not None else {}
        if not self.tracker_dir.exists():
            return states
        for path in self._tracking_files():
            if path.stem > date_str:
                continue
            payload = self._read_json(path, "tracks", diagnostics)
            if payload is None:
                continue
            quality = payload.get("data_quality")
            if isinstance(quality, dict):
                diagnostics.extend(self._safe_diagnostics(quality.get("diagnostics")))
            for track in payload.get("tracks", []):
                key = f"{track.get('recommendation_date', '')}:{track.get('code', '')}"
                if key not in states:
                    states[key] = track
                    state_dates[key] = path.stem
        return states

    def track_daily(self, date_str: str = None) -> Dict:
        """Run daily tracking for all historical recommendations.

        For each historical recommendation:
        1. Read the exact target-date OHLC, never today's quote for an older date
        2. Prefer the ledger's actual fill; otherwise maintain an explicit trigger state
        3. Compare against entry/target/stop-loss only after an entry exists
        4. Record tracking data using trading-day holding periods
        """
        if date_str is None:
            date_str = date.today().isoformat()

        all_tracks = []
        excluded_non_actionable = 0
        diagnostics, state_dates = [], {}
        previous_states = self._load_previous_states(date_str, diagnostics, state_dates)
        damaged_dates = [item["path"].split("/")[1][:-5] for item in diagnostics
                         if item["path"].startswith("tracker/")]

        # Scan all recommendation dates
        if not self.recommendations_dir.exists():
            return self._with_quality({"status": "no_data", "tracks": []}, diagnostics)

        for rec_dir in sorted(self.recommendations_dir.iterdir()):
            if not rec_dir.is_dir() or rec_dir.is_symlink() or not self._is_date_name(rec_dir.name):
                continue
            rec_date = rec_dir.name
            if rec_date > date_str:
                continue

            # Load morning recommendation
            morning_file = rec_dir / "morning.json"
            if not morning_file.exists():
                continue

            rec = self._read_json(morning_file, "stock_recommendations", diagnostics)
            if rec is None:
                continue

            stocks = rec.get("stock_recommendations", [])
            if not stocks:
                continue

            # Track each stock
            for stock in stocks:
                if not self._is_actionable(stock):
                    excluded_non_actionable += 1
                    continue
                key = f"{rec_date}:{stock.get('code', '')}"
                previous = previous_states.get(key)
                if previous and previous.get("status") in self.TERMINAL_STATUSES:
                    # A terminal outcome is immutable. A later price must not
                    # turn a previous hit/stop back into an active sample.
                    track = dict(previous)
                elif (previous and previous.get("history_incomplete")) or any(
                        rec_date <= day and state_dates.get(key, "") < day for day in damaged_dates):
                    track = self._unscored_track(
                        stock=stock, rec_date=rec_date, today=date_str, status="history_incomplete",
                        execution_status="unknown", planned_entry_price=self._number(stock.get("entry_price")),
                        target_price=self._number(stock.get("target_price")),
                        stop_price=self._number(stock.get("stop_loss_price")),
                        horizon=stock.get("horizon", "short"),
                        horizon_days=max(1, int(self._number(stock.get("horizon_days"), 3) or 3)),
                        reason="历史跟踪文件损坏，无法确认期间触发或终结状态；原件保留，暂不计入收益。")
                    track["history_incomplete"] = True
                else:
                    track = self._track_stock(
                        stock,
                        rec_date,
                        date_str,
                        previous=previous,
                        recommendation=rec,
                    )
                    if track.get("status") in self.TERMINAL_STATUSES:
                        track["terminal_at"] = datetime.now().isoformat()
                all_tracks.append(track)

        # Save tracking results
        result = {
            "tracking_date": date_str,
            "tracked_at": datetime.now().isoformat(),
            "total_tracked": len(all_tracks),
            "excluded_non_actionable": excluded_non_actionable,
            "tracks": all_tracks,
            "summary": self._calculate_summary(all_tracks),
        }

        result = self._with_quality(result, diagnostics)
        # A damaged target file is evidence, not an output slot to overwrite.
        target_path = f"tracker/{date_str}.json"
        result["persisted"] = not any(item["path"] == target_path for item in diagnostics)
        if result["persisted"]:
            self._save_tracking(result, date_str)
        return result

    def _track_stock(
        self,
        stock: Dict,
        rec_date: str,
        today: str,
        previous: Optional[Dict] = None,
        recommendation: Optional[Dict] = None,
    ) -> Dict:
        """Track one recommendation from explicit execution and dated market data."""
        code = stock.get("code", "")
        name = stock.get("name", "")
        sector = stock.get("sector", "")
        action = stock.get("action", "")
        timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
        recommendation = recommendation or {}
        previous = previous or {}
        planned_entry_price = self._number(
            stock.get("entry_price") or timing.get("entry_price") or stock.get("current_price"),
            0,
        )
        planned_target_price = self._number(
            stock.get("target_price") or timing.get("target_observation_price"),
            0,
        )
        planned_stop_price = self._number(
            stock.get("stop_loss_price") or timing.get("stop_loss_price"),
            0,
        )
        target_return = self._number(stock.get("target_return_pct"), 0)
        horizon = stock.get("horizon", "short")
        horizon_days = max(1, int(self._number(stock.get("horizon_days"), 3) or 3))

        recommendation_id = stock.get("recommendation_id")
        if not recommendation_id and recommendation.get("run_id"):
            recommendation_id = f"REC-{recommendation['run_id']}-{code}"
        execution = self.execution_lookup(recommendation_id, code) if recommendation_id else None
        if execution and str(execution.get("trade_date") or "") > today:
            execution = None

        try:
            rec_dt = date.fromisoformat(rec_date)
            today_dt = date.fromisoformat(today)
        except (TypeError, ValueError):
            return self._unscored_track(
                stock=stock,
                rec_date=rec_date,
                today=today,
                status="data_error",
                execution_status="unknown",
                planned_entry_price=planned_entry_price,
                target_price=planned_target_price,
                stop_price=planned_stop_price,
                horizon=horizon,
                horizon_days=horizon_days,
                reason="推荐日期或跟踪日期格式无效",
            )
        tracking_age_days = max(0, count_trading_days(rec_dt, today_dt))
        previous_execution_status = previous.get("execution_status")
        trusted_previous_statuses = self.ENTERED_EXECUTION_STATUSES | {"not_triggered"}
        if (
            not execution
            and rec_dt < today_dt
            and previous_execution_status not in trusted_previous_statuses
        ):
            return self._unscored_track(
                stock=stock,
                rec_date=rec_date,
                today=today,
                status="history_incomplete",
                execution_status="unknown",
                planned_entry_price=planned_entry_price,
                target_price=planned_target_price,
                stop_price=planned_stop_price,
                horizon=horizon,
                horizon_days=horizon_days,
                reason="缺少此前逐日触发状态，不能用当前日线倒推出历史进场日",
                tracking_age_days=tracking_age_days,
            )

        market_data = self._fetch_market_data(code, today)
        if market_data.get("error") or market_data.get("date") != today:
            reason = market_data.get("error") or (
                f"行情日期{market_data.get('date') or '缺失'}与目标日期{today}不一致"
            )
            return self._unscored_track(
                stock=stock,
                rec_date=rec_date,
                today=today,
                status="data_error",
                execution_status=("filled" if execution else previous.get("execution_status", "unknown")),
                planned_entry_price=planned_entry_price,
                target_price=planned_target_price,
                stop_price=planned_stop_price,
                horizon=horizon,
                horizon_days=horizon_days,
                reason=reason,
                tracking_age_days=tracking_age_days,
                data=market_data,
                previous=previous,
            )

        day_open = self._number(market_data.get("open"), 0)
        current_price = self._number(market_data.get("close"), 0)
        day_high = self._number(market_data.get("high"), current_price)
        day_low = self._number(market_data.get("low"), current_price)
        if current_price <= 0 or day_high <= 0 or day_low <= 0:
            return self._unscored_track(
                stock=stock,
                rec_date=rec_date,
                today=today,
                status="data_error",
                execution_status=("filled" if execution else previous.get("execution_status", "unknown")),
                planned_entry_price=planned_entry_price,
                target_price=planned_target_price,
                stop_price=planned_stop_price,
                horizon=horizon,
                horizon_days=horizon_days,
                reason="目标日 OHLC 不完整或价格无效",
                tracking_age_days=tracking_age_days,
                data=market_data,
                previous=previous,
            )

        entry_price = None
        entry_date = None
        target_price = planned_target_price
        stop_price = planned_stop_price
        execution_status = "unknown"
        return_basis = "no_position_no_return"
        order_id = None
        trade_id = None
        actual_return_pct = None

        if execution:
            entry_price = self._number(execution.get("fill_price"), 0)
            entry_date = execution.get("trade_date")
            target_price = self._number(execution.get("target_price"), target_price)
            stop_price = self._number(execution.get("stop_price"), stop_price)
            execution_status = "filled"
            order_id = execution.get("order_id")
            trade_id = execution.get("trade_id")
        elif previous.get("execution_status") in self.ENTERED_EXECUTION_STATUSES:
            entry_price = self._number(previous.get("entry_price"), 0)
            entry_date = previous.get("entry_date") or rec_date
            target_price = self._number(previous.get("target_price"), target_price)
            stop_price = self._number(previous.get("stop_loss_price"), stop_price)
            execution_status = previous["execution_status"]
            order_id = previous.get("order_id")
            trade_id = previous.get("trade_id")
        else:
            if planned_entry_price <= 0:
                return self._unscored_track(
                    stock=stock,
                    rec_date=rec_date,
                    today=today,
                    status="data_error",
                    execution_status="unknown",
                    planned_entry_price=planned_entry_price,
                    target_price=target_price,
                    stop_price=stop_price,
                    horizon=horizon,
                    horizon_days=horizon_days,
                    reason="缺少有效计划进场价",
                    tracking_age_days=tracking_age_days,
                    data=market_data,
                )
            lower = planned_entry_price * 0.99
            upper = planned_entry_price * 1.005
            if day_high < lower:
                return self._unscored_track(
                    stock=stock,
                    rec_date=rec_date,
                    today=today,
                    status="entry_invalidated",
                    execution_status="invalidated_gap_down",
                    planned_entry_price=planned_entry_price,
                    target_price=target_price,
                    stop_price=stop_price,
                    horizon=horizon,
                    horizon_days=horizon_days,
                    reason=(
                        f"目标日最高价¥{day_high:.2f}仍低于安全下限¥{lower:.2f}，"
                        "该推荐需重新评估，未视为成交"
                    ),
                    tracking_age_days=tracking_age_days,
                    data=market_data,
                )
            if day_low > upper:
                status = "expired_untriggered" if tracking_age_days > horizon_days * 2 else "not_triggered"
                return self._unscored_track(
                    stock=stock,
                    rec_date=rec_date,
                    today=today,
                    status=status,
                    execution_status="not_triggered",
                    planned_entry_price=planned_entry_price,
                    target_price=target_price,
                    stop_price=stop_price,
                    horizon=horizon,
                    horizon_days=horizon_days,
                    reason=(
                        f"目标日最低价¥{day_low:.2f}未进入计划价安全上限¥{upper:.2f}"
                    ),
                    tracking_age_days=tracking_age_days,
                    data=market_data,
                )
            reference_open = day_open if day_open > 0 else planned_entry_price
            entry_price = round(max(lower, min(reference_open, upper)), 3)
            entry_date = today
            execution_status = "theoretical_trigger"

        if not entry_price or entry_price <= 0 or not entry_date:
            return self._unscored_track(
                stock=stock,
                rec_date=rec_date,
                today=today,
                status="data_error",
                execution_status=execution_status,
                planned_entry_price=planned_entry_price,
                target_price=target_price,
                stop_price=stop_price,
                horizon=horizon,
                horizon_days=horizon_days,
                reason="成交记录缺少有效成交价或成交日期",
                tracking_age_days=tracking_age_days,
                data=market_data,
            )

        if target_price <= 0:
            target_price = round(
                entry_price * (1 + target_return / 100) if target_return > 0 else entry_price * 1.05,
                2,
            )
        if stop_price <= 0:
            stop_rate = abs(self._number(stock.get("stop_loss_pct"), 5)) / 100
            stop_price = round(entry_price * (1 - stop_rate), 2)
        if target_price > entry_price:
            target_return = round((target_price / entry_price - 1) * 100, 2)

        try:
            entry_dt = date.fromisoformat(str(entry_date))
            holding_days = max(0, count_trading_days(entry_dt, today_dt))
        except (TypeError, ValueError):
            holding_days = 0

        if execution_status == "filled":
            quantity = int(self._number(execution.get("quantity"), 0))
            exit_cost = calculate_trade_costs(
                current_price * quantity,
                "sell",
                execution.get("instrument_type") or "stock",
            )
            exit_value = (
                current_price * exit_cost["effective_price_factor"] * quantity
                - exit_cost["fees"]
            )
            entry_value = self._number(execution.get("fill_amount"), entry_price * quantity)
            entry_outlay = entry_value + self._number(execution.get("entry_fees"), 0)
            actual_return_pct = round(
                (exit_value - entry_outlay) / entry_outlay * 100 if entry_outlay > 0 else 0,
                2,
            )
            return_basis = "actual_fill_to_close_after_estimated_exit_costs"
        else:
            actual_return_pct = round((current_price - entry_price) / entry_price * 100, 2)
            return_basis = "theoretical_bar_trigger_to_close"

        same_entry_day = str(entry_date) == today
        target_touched = (
            current_price >= target_price if same_entry_day else day_high >= target_price
        ) if target_price > 0 else actual_return_pct >= target_return
        stop_touched = (
            current_price <= stop_price if same_entry_day else day_low <= stop_price
        ) if stop_price > 0 else False
        path_ambiguous = bool(not same_entry_day and target_touched and stop_touched)
        status = "active"
        failure_reason = ""
        if path_ambiguous:
            status = "path_ambiguous"
            failure_reason = "同一日线同时触及止盈和止损，缺少分钟级路径，暂不判定输赢"
        elif target_touched:
            status = "hit_target"
        elif stop_touched:
            status = "stopped_out"
            failure_reason = f"触发止损: 日内最低¥{day_low:.2f} ≤ 止损价¥{stop_price:.2f}"
        elif holding_days > horizon_days * 2:
            status = "expired"
            if actual_return_pct < 0:
                failure_reason = f"过期未达标: 持仓{holding_days}个交易日，收益{actual_return_pct}%"
            else:
                status = "active"
        elif actual_return_pct <= -10:
            status = "deep_loss"
            failure_reason = f"深度亏损: 浮亏{actual_return_pct}%"

        feedback = self._generate_feedback(
            status, actual_return_pct, target_return, holding_days,
            horizon_days, entry_price, current_price, target_price, stop_price,
            failure_reason, stock
        )
        failed_statuses = {"stopped_out", "deep_loss", "expired"}
        return {
            "code": code,
            "name": name,
            "sector": sector,
            "action": action,
            "recommendation_id": recommendation_id,
            "recommendation_date": rec_date,
            "tracking_date": today,
            "data_date": market_data.get("date", ""),
            "data_source": market_data.get("_source", ""),
            "horizon": horizon,
            "horizon_days": horizon_days,
            "planned_entry_price": planned_entry_price or None,
            "entry_price": entry_price,
            "entry_date": str(entry_date),
            "target_price": target_price,
            "stop_loss_price": stop_price,
            "current_price": current_price,
            "day_open": day_open,
            "day_high": day_high,
            "day_low": day_low,
            "actual_return_pct": actual_return_pct,
            "target_return_pct": target_return,
            "holding_days": holding_days,
            "tracking_age_days": tracking_age_days,
            "status": status,
            "execution_status": execution_status,
            "return_basis": return_basis,
            "order_id": order_id,
            "trade_id": trade_id,
            "path_ambiguous": path_ambiguous,
            "failure_reason": failure_reason,
            "is_met_expectation": status == "hit_target",
            "is_failed": status in failed_statuses,
            "red_flag": status in failed_statuses,
            "performance_grade": feedback["grade"],
            "feedback": feedback["analysis"],
            "adjustment_suggestion": feedback["suggestion"],
            "recommendation_reason": stock.get("reason", ""),
            "reason_news": stock.get("reason_news", ""),
            "reason_policy": stock.get("reason_policy", ""),
            "reason_technical": stock.get("reason_technical", ""),
            "reason_fund": stock.get("reason_fund", ""),
        }

    def _unscored_track(
        self,
        *,
        stock: Dict,
        rec_date: str,
        today: str,
        status: str,
        execution_status: str,
        planned_entry_price: float,
        target_price: float,
        stop_price: float,
        horizon: str,
        horizon_days: int,
        reason: str,
        tracking_age_days: int = 0,
        data: Optional[Dict] = None,
        previous: Optional[Dict] = None,
    ) -> Dict:
        """Build a non-performance state without fabricating an entry return."""
        data = data or {}
        previous = previous or {}
        entered_before_error = previous.get("execution_status") in self.ENTERED_EXECUTION_STATUSES
        return {
            "code": stock.get("code", ""),
            "name": stock.get("name", ""),
            "sector": stock.get("sector", ""),
            "action": stock.get("action", ""),
            "recommendation_id": stock.get("recommendation_id"),
            "recommendation_date": rec_date,
            "tracking_date": today,
            "data_date": data.get("date", ""),
            "data_source": data.get("_source", ""),
            "horizon": horizon,
            "horizon_days": horizon_days,
            "planned_entry_price": planned_entry_price or None,
            "entry_price": previous.get("entry_price") if entered_before_error else None,
            "entry_date": previous.get("entry_date") if entered_before_error else None,
            "target_price": (
                previous.get("target_price") if entered_before_error else target_price or None
            ),
            "stop_loss_price": (
                previous.get("stop_loss_price") if entered_before_error else stop_price or None
            ),
            "current_price": self._number(data.get("close"), 0) or None,
            "day_open": self._number(data.get("open"), 0) or None,
            "day_high": self._number(data.get("high"), 0) or None,
            "day_low": self._number(data.get("low"), 0) or None,
            "actual_return_pct": None,
            "target_return_pct": self._number(stock.get("target_return_pct"), 0),
            "holding_days": 0,
            "tracking_age_days": tracking_age_days,
            "status": status,
            "execution_status": execution_status,
            "return_basis": "no_position_no_return",
            "order_id": previous.get("order_id") if entered_before_error else None,
            "trade_id": previous.get("trade_id") if entered_before_error else None,
            "path_ambiguous": False,
            "failure_reason": reason,
            "is_met_expectation": False,
            "is_failed": False,
            "red_flag": False,
            "performance_grade": "N/A",
            "feedback": reason,
            "adjustment_suggestion": "等待真实成交或补齐逐日触发证据后再进入收益统计。",
            "recommendation_reason": stock.get("reason", ""),
            "reason_news": stock.get("reason_news", ""),
            "reason_policy": stock.get("reason_policy", ""),
            "reason_technical": stock.get("reason_technical", ""),
            "reason_fund": stock.get("reason_fund", ""),
        }

    def _generate_feedback(
        self, status, actual_return, target_return, holding_days,
        horizon_days, entry_price, current_price, target_price, stop_loss,
        failure_reason, stock
    ) -> Dict:
        """生成推荐反馈分析（符合预期→延伸推荐，不符合→红标+调整建议）"""
        grade = "D"
        analysis = ""
        suggestion = ""

        if status == "hit_target":
            grade = "A"
            analysis = f"✅ 达到预期目标！推荐时预期收益{target_return}%，实际收益{actual_return}%，持仓{holding_days}天。"
            suggestion = f"基于原推荐理由({stock.get('reason','')[:30]})，可考虑：1)止盈离场 2)上调目标价继续持有 3)关注同板块同类机会"

        elif status == "active" and actual_return > 0:
            grade = "B"
            analysis = f"🟡 盈利中但未达目标。当前收益{actual_return}%，目标{target_return}%，已完成{(actual_return/target_return*100 if target_return else 0):.0f}%。"
            suggestion = "继续持有观察，设好移动止盈。若接近目标可适当减仓锁利。"

        elif status == "active" and actual_return <= 0:
            grade = "C"
            analysis = f"🟡 持仓中，当前浮亏{actual_return}%。入场价¥{entry_price}，现价¥{current_price}，止损价¥{stop_loss}。"
            suggestion = "观望为主，若接近止损价果断离场。检查原推荐理由是否仍成立。"

        elif status == "stopped_out":
            grade = "D"
            analysis = f"🔴 止损出局！{failure_reason}。预期收益{target_return}%，实际亏损{actual_return}%。"
            suggestion = f"失败分析：1)入场时机可能偏早 2)止损位可能过紧 3)市场环境可能变化。建议：避免立即追回，等待新信号。"

        elif status == "deep_loss":
            grade = "F"
            analysis = f"🔴 深度亏损！浮亏{actual_return}%，远超止损线。原推荐理由：{stock.get('reason','')[:40]}。"
            suggestion = "立即评估是否割肉。失败原因可能是：1)推荐逻辑错误 2)市场系统性风险 3)个股黑天鹅。后续避免类似逻辑推荐。"

        elif status == "expired":
            grade = "C" if actual_return >= 0 else "D"
            if actual_return >= 0:
                analysis = f"🟡 持仓到期，微利{actual_return}%但未达目标{target_return}%。持仓{holding_days}天。"
                suggestion = "考虑减仓换股，资金效率偏低。原推荐板块可继续关注但换标的。"
            else:
                analysis = f"🔴 持仓到期且亏损{actual_return}%。{failure_reason}。"
                suggestion = "到期止损离场。反思：horizon设置是否合理？市场状态是否误判？"

        elif status == "path_ambiguous":
            grade = "N/A"
            analysis = f"⚪ {failure_reason}"
            suggestion = "补充分钟级行情后再确定当日先触及止盈还是止损，当前样本不判输赢。"

        return {"grade": grade, "analysis": analysis, "suggestion": suggestion}

    def _calculate_summary(self, tracks: List[Dict]) -> Dict:
        """Calculate overall tracking summary"""
        if not tracks:
            return {}

        total = len(tracks)
        scoreable = [
            t for t in tracks
            if t.get("execution_status") in self.ENTERED_EXECUTION_STATUSES
            and isinstance(t.get("actual_return_pct"), (int, float))
        ]
        valid_total = len(scoreable)
        hit = sum(1 for t in scoreable if t.get("status") == "hit_target")
        stopped = sum(1 for t in scoreable if t.get("status") == "stopped_out")
        active = sum(1 for t in scoreable if t.get("status") == "active")
        ambiguous = sum(1 for t in scoreable if t.get("status") == "path_ambiguous")
        deep_loss = sum(1 for t in scoreable if t.get("status") == "deep_loss")
        expired = sum(1 for t in scoreable if t.get("status") == "expired")
        not_triggered = sum(1 for t in tracks if t.get("status") == "not_triggered")
        entry_invalidated = sum(1 for t in tracks if t.get("status") == "entry_invalidated")
        expired_untriggered = sum(1 for t in tracks if t.get("status") == "expired_untriggered")
        history_incomplete = sum(1 for t in tracks if t.get("status") == "history_incomplete")
        data_error = sum(1 for t in tracks if t.get("status") == "data_error")

        returns = [float(t["actual_return_pct"]) for t in scoreable]
        avg_return = sum(returns) / len(returns) if returns else 0

        winners = [r for r in returns if r > 0]
        losers = [r for r in returns if r < 0]
        avg_win = sum(winners) / len(winners) if winners else 0
        avg_loss = sum(losers) / len(losers) if losers else 0

        failed = stopped + deep_loss + expired
        closed = hit + failed
        pending = valid_total - closed
        win_rate = hit / closed * 100 if closed > 0 else 0

        # Sector performance must never include recommendations without an entry.
        sector_stats = {}
        for t in scoreable:
            s = t.get("sector", "未知")
            if s not in sector_stats:
                sector_stats[s] = {"total": 0, "hit": 0, "failed": 0, "returns": []}
            sector_stats[s]["total"] += 1
            if t["is_met_expectation"]:
                sector_stats[s]["hit"] += 1
            if t["is_failed"]:
                sector_stats[s]["failed"] += 1
            sector_stats[s]["returns"].append(float(t["actual_return_pct"]))

        sector_summary = {}
        for s, data in sector_stats.items():
            sector_summary[s] = {
                "total": data["total"],
                "hit": data["hit"],
                "failed": data["failed"],
                "closed": data["hit"] + data["failed"],
                "win_rate": round(data["hit"] / (data["hit"] + data["failed"]) * 100, 1) if data["hit"] + data["failed"] > 0 else 0,
                "avg_return": round(sum(data["returns"]) / len(data["returns"]), 2) if data["returns"] else 0,
            }

        return {
            "total_tracked": total,
            "valid_samples": valid_total,
            "hit_target": hit,
            "stopped_out": stopped,
            "active": active,
            "path_ambiguous": ambiguous,
            "deep_loss": deep_loss,
            "expired": expired,
            "not_triggered": not_triggered,
            "entry_invalidated": entry_invalidated,
            "expired_untriggered": expired_untriggered,
            "history_incomplete": history_incomplete,
            "data_error": data_error,
            "closed_samples": closed,
            "pending_samples": pending,
            "execution_coverage_pct": round(valid_total / total * 100, 1) if total else 0,
            "sample_coverage_pct": round(closed / valid_total * 100, 1) if valid_total else 0,
            "recommendation_closure_pct": round(closed / total * 100, 1) if total else 0,
            "win_rate_pct": round(win_rate, 1),
            "win_rate_basis": "closed_samples_only",
            "return_basis": "entered_samples_only",
            "avg_return_pct": round(avg_return, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "best_return": round(max(returns), 2) if returns else 0,
            "worst_return": round(min(returns), 2) if returns else 0,
            "profit_loss_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
            "sector_summary": sector_summary,
        }

    def _save_tracking(self, result: Dict, date_str: str):
        """保存跟踪结果到文件"""
        import os
        track_file = self.tracker_dir / f"{date_str}.json"
        temp_file = track_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            os.replace(str(temp_file), str(track_file))
        except IOError as e:
            print(f"⚠️ 保存跟踪数据失败: {e}")
            raise

    def get_tracking(self, date_str: str = None) -> Optional[Dict]:
        """Get tracking data for a date"""
        if date_str is None:
            date_str = date.today().isoformat()
        if not self._is_date_name(date_str):
            return None
        track_file = self.tracker_dir / f"{date_str}.json"
        if not track_file.exists():
            return None
        diagnostics = []
        payload = self._read_json(track_file, "tracks", diagnostics)
        return self._with_quality(payload if payload is not None else {
            "status": "error", "tracking_date": date_str, "tracks": [], "summary": {}}, diagnostics)

    def get_latest_tracking(self) -> Optional[Dict]:
        """Get the most recent tracking data"""
        files = self._tracking_files()
        diagnostics = []
        for path in files:
            payload = self._read_json(path, "tracks", diagnostics)
            if payload is not None:
                result = self._with_quality(payload, diagnostics)
                result["requested_tracking_date"] = files[0].stem
                if path != files[0]:
                    result["fallback_from"] = files[0].stem
                return result
        if diagnostics:
            return self._with_quality({"status": "error", "tracking_date": None,
                "requested_tracking_date": files[0].stem, "tracks": [], "summary": {}}, diagnostics)
        return None

    def get_weekly_review(self, weeks: int = 4) -> List[Dict]:
        """Generate weekly performance reviews"""
        reviews = []
        today = date.today()

        for w in range(weeks):
            # Calculate week boundaries (Monday to Friday)
            week_end = today - timedelta(days=w * 7)
            week_start = week_end - timedelta(days=6)

            week_tracks = []
            diagnostics = []

            # Collect all tracks within this week
            for d in range(7):
                day = week_start + timedelta(days=d)
                track_data = self.get_tracking(day.isoformat())
                if track_data:
                    diagnostics.extend(track_data.get("data_quality", {}).get("diagnostics", []))
                if track_data and track_data.get("tracks"):
                    week_tracks.extend(track_data["tracks"])

            if not week_tracks and not diagnostics:
                continue

            # Deduplicate daily snapshots by recommendation identity. The same
            # code may legitimately be recommended more than once in a week.
            seen = {}
            for t in week_tracks:
                key = f"{t.get('recommendation_date', '')}:{t.get('code', '')}"
                seen[key] = t
            unique_tracks = list(seen.values())

            summary = self._calculate_summary(unique_tracks)
            quality = self._with_quality({"summary": summary}, diagnostics)
            summary = quality["summary"]
            summary["data_quality"] = quality["data_quality"]
            summary["week_start"] = week_start.isoformat()
            summary["week_end"] = week_end.isoformat()
            summary["week_label"] = f"{week_start.isoformat()} ~ {week_end.isoformat()}"

            # Top winners and losers
            scored_tracks = [
                t for t in unique_tracks
                if isinstance(t.get("actual_return_pct"), (int, float))
            ]
            sorted_tracks = sorted(
                scored_tracks,
                key=lambda t: t.get("actual_return_pct", 0),
                reverse=True,
            )
            summary["top_winners"] = [
                {"code": t["code"], "name": t["name"], "return_pct": t["actual_return_pct"]}
                for t in sorted_tracks[:3] if t["actual_return_pct"] > 0
            ]
            summary["top_losers"] = [
                {"code": t["code"], "name": t["name"], "return_pct": t["actual_return_pct"],
                 "failure_reason": t.get("failure_reason", "")}
                for t in sorted_tracks[-3:] if t["actual_return_pct"] < 0
            ]

            reviews.append(summary)

        return reviews

    def get_improvement_suggestions(self) -> List[Dict]:
        """Analyze tracking data and suggest improvements"""
        tracking = self.get_latest_tracking()
        if tracking and tracking.get("data_quality", {}).get("history_incomplete"):
            return [{"type": "data_quality", "priority": "high",
                     "message": "跟踪历史不完整，先核查损坏文件和缺失状态；当前不生成基于胜率或收益的调参建议。"}]
        if not tracking or not tracking.get("tracks"):
            return []

        suggestions = []
        summary = tracking.get("summary", {})
        tracks = tracking.get("tracks", [])

        # Win rate check
        win_rate = summary.get("win_rate_pct", 0)
        if summary.get("closed_samples", 0) > 0 and win_rate < 50:
            suggestions.append({
                "type": "win_rate",
                "priority": "high",
                "message": f"胜率偏低({win_rate}%)，建议：1)提高选股门槛 2)减少短线操作 3)增加确认信号",
            })

        # Sector analysis
        sector_summary = summary.get("sector_summary", {})
        for sector, data in sector_summary.items():
            if data.get("win_rate", 100) < 30 and data.get("total", 0) >= 3:
                suggestions.append({
                    "type": "sector_weakness",
                    "priority": "medium",
                    "message": f"板块'{sector}'胜率仅{data['win_rate']}%（{data['total']}只），建议降低该板块权重",
                })

        # Deep loss check
        deep_losses = [t for t in tracks if t["status"] == "deep_loss"]
        if deep_losses:
            codes = [f"{t['name']}({t['code']})" for t in deep_losses]
            suggestions.append({
                "type": "deep_loss",
                "priority": "high",
                "message": f"存在深度亏损股: {', '.join(codes)}，建议严格执行止损",
            })

        # Average return check
        avg_return = summary.get("avg_return_pct", 0)
        if summary.get("valid_samples", 0) > 0 and avg_return < 0:
            suggestions.append({
                "type": "negative_return",
                "priority": "high",
                "message": f"平均收益为负({avg_return}%)，建议：1)优化选股策略 2)缩短持仓周期 3)加强止损执行",
            })

        return suggestions

    def _fetch_market_data(self, code: str, target_date: str) -> Dict:
        """Fetch exact-date OHLC and explicitly reject latest-price fallback."""
        if not code:
            return {"error": "missing_stock_code"}
        from src.data_collectors.market_data import get_stock_data_on
        return get_stock_data_on(code, target_date)
