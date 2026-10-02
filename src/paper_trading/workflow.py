# -*- coding: utf-8 -*-
"""09:35自动执行、订单恢复扫描和盘中监控编排。"""

from __future__ import annotations

import os
import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List

from src.data_collectors.realtime_prices import fetch_realtime_prices
from src.data_collectors.trading_calendar import count_trading_days, is_trading_day
from src.paper_trading.entry_plans import EntryPlanStore, PENDING, TERMINAL
from src.notifier.feishu import FeishuNotifier
from src.paper_trading.quality_gate import (
    build_execution_levels,
    calculate_net_reward_risk,
    entry_trigger_error,
    resolve_stop_rate,
)
from src.paper_trading.trading_service import TradingService
from src.strategy.position_limits import (
    get_cash_reserve_pct,
    get_max_stock_price,
    get_paper_trading_config,
    get_position_limit,
    get_regime_limits,
    get_sector_limit,
)
from src.utils.cost_calculator import calculate_trade_costs


DEFENSIVE_SECTORS = {
    "公用事业", "银行", "食品饮料", "医药", "消费", "交通运输", "电力", "煤炭", "金融",
}

# One original order plus two successor attempts per holding/day. Do not retry
# business rejections, uncertain ledger failures, vetoes or price-band failures.
EXIT_ATTEMPT_LIMIT = 3
RETRYABLE_EXIT_QUOTE_ERRORS = frozenset({"行情已过期", "09:40行情获取失败"})


def _is_etf(stock: Dict) -> bool:
    instrument_type = str(stock.get("instrument_type") or "").lower()
    name = str(stock.get("name") or "").upper()
    code = str(stock.get("code") or "")
    return instrument_type == "etf" or "ETF" in name or code.startswith(("15", "16", "50", "51", "52", "56", "58"))


def _is_defensive(stock: Dict) -> bool:
    sector = str(stock.get("sector") or "")
    return _is_etf(stock) or any(keyword in sector for keyword in DEFENSIVE_SECTORS)


class PaperTradingWorkflow:
    def __init__(
        self,
        data_dir: Path = None,
        notifier: FeishuNotifier = None,
        quote_fetcher: Callable[[List[str]], Dict[str, Dict]] = None,
        now_provider=None,
    ):
        self.service = TradingService(data_dir, now_provider=now_provider)
        self.notifier = notifier or FeishuNotifier()
        self.quote_fetcher = quote_fetcher or fetch_realtime_prices
        self.entry_plans = EntryPlanStore(self.service.ledger)

    @staticmethod
    def enabled() -> bool:
        return os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true"

    @staticmethod
    def _source_error(source_status):
        if source_status.get("llm") != "success":
            return "LLM或数据降级，禁止开仓"
        if not str(source_status.get("candidate_universe", "")).startswith("ok_"):
            return "全市场候选池未通过，禁止开仓"
        if not str(source_status.get("recommendation_prices", "")).startswith("ok_"):
            return "推荐价格未全部通过真实行情校验，禁止开仓"
        return None

    def _context_error(self, report, source_status):
        now = self.service.now()
        if not self.enabled():
            return "PAPER_TRADING_ENABLED=false"
        if not is_trading_day(now.date()):
            return "非交易日，禁止开仓"
        if not self.service._is_market_session(now):
            return "不在连续交易时段，等待下一可交易时点"
        if not report or report.get("date") != now.date().isoformat():
            return "当天研究报告缺失或已过期，禁止沿用旧计划"
        if report.get("analysis_degraded") is True:
            return "研究报告处于降级状态，禁止开仓"
        if report.get("type") == "afternoon":
            if (source_status.get("llm") != "success"
                    or source_status.get("market_data") != "ok"
                    or source_status.get("morning_report") != "ok"
                    or not str(source_status.get("realtime_prices", "")).startswith("ok_")):
                return "盘中复核数据降级或缺失，禁止开仓"
            return None
        return self._source_error(source_status)

    @staticmethod
    def _plan_changed(stock, original):
        keys = ("entry_price", "target_price", "stop_loss_price", "target_return_pct", "stop_loss_pct", "timing", "horizon")
        return any(stock.get(key) != original.get(key) for key in keys)

    @staticmethod
    def _candidate_error(stock, regime):
        if not stock.get("code"):
            return "缺少证券代码"
        if stock.get("action") != "setup_ready" or stock.get("trade_eligible") is not True:
            return "推荐仅供观察，未通过交易资格"
        if stock.get("price_validation", {}).get("verified") is not True:
            return "推荐价格未经核验"
        if float(stock.get("confidence") or 0) < float(get_paper_trading_config().get("min_trade_confidence", 4)):
            return "推荐置信度不足"
        policy = get_regime_limits(regime)
        if not (policy.get("allow_etfs", True) if _is_etf(stock) else policy.get("allow_stocks", True)):
            return f"{regime}市场禁止该类标的新开仓"
        if policy.get("defensive_only") and not _is_defensive(stock):
            return f"{regime}市场仅允许防御候选"
        return None

    def prepare_final_orders(self, report: Dict, source_status: Dict = None, notify: bool = True) -> Dict:
        """保存每个候选及未成交原因；未到价的计划保留到当日收盘。"""
        if not self.enabled():
            return {"status": "disabled", "orders": [], "reason": "PAPER_TRADING_ENABLED=false"}
        sources = source_status if source_status is not None else report.get("source_status", {})
        now = self.service.now()
        run_id = report.get("run_id") or f"{now.date().isoformat()}-open"
        plans = []
        for stock in report.get("stock_recommendations", []):
            plan = self.entry_plans.register(stock, run_id=run_id, trade_date=now.date().isoformat(), now=now,
                                             source_status=sources)
            if self._plan_changed(stock, plan["stock"]):
                self.entry_plans.update(plan["plan_id"], "rejected", "同一研究标识的计划已变更，原计划失效", now)
                plan["status"] = "rejected"
            plans.append(plan)
        # Registering a newer same-code plan may supersede a row already in this
        # batch; use persisted states rather than the stale registration return.
        requested_ids = {plan["plan_id"] for plan in plans}
        plans = [plan for plan in self.entry_plans.list_plans(now.date().isoformat(), limit=None)
                 if plan["plan_id"] in requested_ids]
        if not plans:
            source_error = self._source_error(sources)
            return {"status": "safe_mode" if source_error else "no_orders", "orders": [], "rejected": [],
                    "reason": source_error or f"{report.get('market_regime', 'neutral')}市场下没有满足当前仓位与质量规则的候选"}
        return self._process_plans(plans, report, sources, notify=notify)

    def _process_plans(self, plans, report, sources, notify=False):
        now = self.service.now()
        self.entry_plans.reconcile_fills(now)
        requested_ids = {plan["plan_id"] for plan in plans}
        plans = [plan for plan in self.entry_plans.list_plans(limit=None) if plan["plan_id"] in requested_ids]
        context_error = self._context_error(report, sources)
        eligible, rejected = [], []
        for plan in plans:
            if plan["status"] in TERMINAL:
                continue
            expired = now >= datetime.fromisoformat(plan["expires_at"])
            reason = "计划已于当日收盘到期，禁止跨日追单" if expired else context_error
            if not reason:
                reason = self._source_error(plan["stock"].get("_entry_source_status", {}))
            if not reason:
                reason = self._candidate_error(plan["stock"], report.get("market_regime", "neutral"))
            if reason:
                status = "expired" if expired else "rejected"
                if not expired and reason == "不在连续交易时段，等待下一可交易时点":
                    status = plan["status"]
                self.entry_plans.update(plan["plan_id"], status, reason, now)
                rejected.append({"code": plan["code"], "reason": reason})
            else:
                eligible.append(plan)
        if not eligible:
            return {"status": "safe_mode" if context_error else "no_orders", "orders": [],
                    "rejected": rejected, "reason": context_error or "没有待触发的合格计划"}
        # Keep original run/recommendation identities across newer reports and restarts.
        # Process as one group to retain candidate ranking and daily policy limits.
        execution_report = dict(report, stock_recommendations=[dict(p["stock"], _plan_run_id=p["run_id"]) for p in eligible])
        result = self._execute_report(execution_report, sources, notify=notify)
        reasons = {item["code"]: item["reason"] for item in result.get("rejected", [])}
        orders = {order["code"]: order for order in result.get("orders", [])}
        for plan in eligible:
            if plan["code"] in orders:
                self.entry_plans.update(plan["plan_id"], "filled", "通过复核并模拟成交", now, orders[plan["code"]]["order_id"])
                continue
            reason = reasons.get(plan["code"]) or result.get("reason") or "本轮扫描或当日开仓额度已用尽"
            status = "waiting_trigger" if "尚未进入计划价" in reason else "rejected"
            if any(word in reason for word in ("行情不可用", "行情已过期", "行情获取失败")):
                status = "waiting_quote"
            self.entry_plans.update(plan["plan_id"], status, reason, now)
            if plan["code"] not in reasons:
                result.setdefault("rejected", []).append({"code": plan["code"], "reason": reason})
        result.setdefault("rejected", []).extend(rejected)
        self.entry_plans.reconcile_fills(now)
        result["entry_plan_summary"] = self.entry_plans.get_summary(now.date().isoformat())
        return result

    def recheck_entry_plans(self, report: Dict = None, source_status: Dict = None, notify: bool = False) -> Dict:
        """Revalidate pending plans, using today's latest report; no messages by default."""
        plans = self.entry_plans.list_plans(limit=None, statuses=sorted(PENDING))
        if not plans:
            return {"status": "no_orders", "orders": [], "reason": "没有待触发计划"}
        if report is None:
            directory = self.service.data_dir / "recommendations" / self.service.now().date().isoformat()
            for report_type in ("afternoon", "morning"):
                path = directory / f"{report_type}.json"
                if path.exists():
                    try:
                        report = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        report = None
                    break  # A broken newer report must not silently fall back to bullish old evidence.
        report = report or {}
        sources = source_status if source_status is not None else report.get("source_status", {})
        # A current downgrade/removal invalidates the old setup, not its original identity.
        current = {stock.get("code"): stock for stock in report.get("stock_recommendations", [])}
        for plan in plans:
            stock = current.get(plan["code"])
            if stock and report.get("type") == "afternoon" and stock.get("decision_scope") == "advisory_only":
                # Afternoon cannot grant buy authority. It can only leave an
                # already-authorized original setup intact or veto that setup.
                advisory_ok = stock.get("afternoon_decision") in {"maintain", "upgrade"} and not stock.get("degraded_reason")
                stock = dict(stock, trade_eligible=plan["stock"].get("trade_eligible") if advisory_ok else False)
            if report and (stock is None or self._candidate_error(stock, report.get("market_regime", "neutral"))):
                self.entry_plans.update(plan["plan_id"], "rejected", "最新研究已撤回或降级该候选", self.service.now())
            elif stock:
                # New research may lower its target while still marking setup_ready.
                # Do not quietly trade the more optimistic archived target/entry.
                if self._plan_changed(stock, plan["stock"]):
                    self.entry_plans.update(plan["plan_id"], "rejected", "最新研究计划价格或周期已变更，原计划失效", self.service.now())
        plans = self.entry_plans.list_plans(limit=None, statuses=sorted(PENDING))
        return self._process_plans(plans, report, sources, notify=notify)

    def _execute_report(self, report: Dict, source_status: Dict = None, notify: bool = True) -> Dict:
        """用09:35行情重算，通过风控后立即模拟成交并推送结果。"""
        if not self.enabled():
            return {"status": "disabled", "orders": [], "reason": "PAPER_TRADING_ENABLED=false"}
        regime = report.get("market_regime", "neutral")
        config = get_paper_trading_config()
        policy = get_regime_limits(regime)
        min_confidence = float(config.get("min_trade_confidence", 4))
        candidates = [
            stock for stock in report.get("stock_recommendations", [])
            if stock.get("action") == "setup_ready"
            and stock.get("trade_eligible") is True
            and stock.get("price_validation", {}).get("verified") is True
            and float(stock.get("confidence") or 0) >= min_confidence
            and (policy.get("allow_etfs", True) if _is_etf(stock) else policy.get("allow_stocks", True))
            and (not policy.get("defensive_only") or _is_defensive(stock))
        ]
        if policy.get("prefer_etfs"):
            candidates.sort(key=lambda stock: (not _is_etf(stock), -float(stock.get("confidence") or 0)))
        else:
            candidates.sort(key=lambda stock: -float(stock.get("confidence") or 0))
        max_scan = int(config.get("max_candidates_to_scan", 10))
        candidates = candidates[:max_scan]
        if not candidates:
            return {
                "status": "no_orders",
                "orders": [],
                "rejected": [],
                "reason": f"{regime}市场下没有满足当前仓位与质量规则的候选",
            }
        codes = [stock.get("code") for stock in candidates if stock.get("code")]
        try:
            quotes = self.quote_fetcher(codes)
        except Exception:
            return {"status": "no_orders", "orders": [], "reason": "行情获取失败，本轮禁止成交"}
        account = self.service.get_account()
        self.service.record_equity_snapshot(account["total_equity"], at=self.service.now().isoformat())
        run_id = report.get("run_id") or f"{self.service.now().date().isoformat()}-open"
        prepared, rejected, execution_results, notification_results = [], [], [], []
        trade_date = self.service.now().date().isoformat()
        buys_today = {
            trade["code"] for trade in self.service.ledger.list_trades(trade_date=trade_date, limit=1000)
            if trade.get("action") == "buy"
        }
        max_new_positions = int(policy.get("max_new_positions_per_day", 2))
        remaining_new_positions = max(0, max_new_positions - len(buys_today))
        if remaining_new_positions == 0:
            return {
                "status": "no_orders",
                "orders": [],
                "rejected": [],
                "reason": f"今日已达到{max_new_positions}只新开仓上限",
                "account_equity": account["total_equity"],
            }

        for stock in candidates:
            code = stock.get("code")
            quote = quotes.get(code) or {}
            quote_error = self.service._validate_quote({"code": code}, quote, self.service.now())
            if quote_error:
                rejected.append({"code": code, "reason": quote_error})
                continue
            price = float(quote.get("price") or 0)
            if price <= 0:
                rejected.append({"code": code, "reason": "09:35行情不可用"})
                continue
            if not _is_etf(stock) and price > get_max_stock_price():
                rejected.append({"code": code, "reason": f"最新价¥{price:.2f}超过¥{get_max_stock_price():.0f}价格上限"})
                continue
            trigger_error = entry_trigger_error(stock, price, config)
            if trigger_error:
                rejected.append({"code": code, "reason": trigger_error})
                continue

            account = self.service.get_account()
            positions = self.service.get_positions(trade_date)
            if any(position["code"] == code for position in positions):
                rejected.append({"code": code, "reason": "已有持仓，本轮优先增加持仓多样性"})
                continue
            max_positions = int(config.get("max_positions", 5))
            if len(positions) >= max_positions:
                rejected.append({"code": code, "reason": f"已达到{max_positions}只持仓上限"})
                break
            sector = stock.get("sector") or "其他"
            same_sector = [position for position in positions if (position.get("sector") or "其他") == sector]
            same_sector_max = int(config.get("same_sector_max_positions", 2))
            if len(same_sector) >= same_sector_max:
                rejected.append({"code": code, "reason": f"{sector}板块已有{same_sector_max}只持仓"})
                continue

            equity = float(account["total_equity"])
            exposure = sum(float(position.get("market_value") or 0) for position in positions)
            sector_exposure = sum(float(position.get("market_value") or 0) for position in same_sector)
            horizon = stock.get("horizon", "short")
            position_pct = min(
                float(policy.get("max_position_pct", 0.30)),
                float(config.get("max_position_pct", 0.30)),
                get_position_limit(equity, horizon),
            )
            reserve_pct = max(float(config.get("cash_reserve_pct", 0.20)), get_cash_reserve_pct(equity))
            max_amount = min(
                equity * position_pct,
                equity * float(policy.get("max_total_exposure_pct", 0.80)) - exposure,
                equity * get_sector_limit(equity) - sector_exposure,
                float(account["cash"]) - equity * reserve_pct,
            )
            stop = round(price * (1 - resolve_stop_rate(stock, config)), 3)
            instrument_type = "etf" if _is_etf(stock) else str(stock.get("instrument_type") or "stock")
            quantity = self._maximum_quantity(
                price=price,
                stop=stop,
                max_amount=max_amount,
                equity=equity,
                cash=float(account["cash"]),
                reserve_pct=reserve_pct,
                instrument_type=instrument_type,
                max_trade_risk_pct=float(config.get("max_trade_risk_pct", 0.0075)),
                remaining_daily_risk=max(
                    0.0,
                    equity * float(config.get("max_daily_new_risk_pct", 0.015))
                    - self.service.daily_new_risk_amount(trade_date),
                ),
            )
            min_order_amount = float(config.get("min_order_amount", 1000))
            if quantity < 100 or price * quantity < min_order_amount:
                rejected.append({
                    "code": code,
                    "reason": (
                        f"100股需¥{price * 100:,.0f}，当前仓位/现金/风险可用上限¥{max(0, max_amount):,.0f}，"
                        f"或不满足¥{min_order_amount:,.0f}最小订单"
                    ),
                })
                continue
            execution_levels = build_execution_levels(
                stock=stock,
                market_price=price,
                quantity=quantity,
                instrument_type=instrument_type,
                config=config,
            )
            if not execution_levels.get("eligible"):
                rejected.append({"code": code, "reason": execution_levels.get("reason")})
                continue
            recommendation_id = stock.get("recommendation_id") or f"REC-{run_id}-{code}"
            timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
            planned_entry = float(stock.get("entry_price") or timing.get("entry_price"))
            trigger_tolerance = float(config.get("entry_trigger_tolerance_pct", 0.005))
            max_gap_below = float(config.get("entry_max_gap_below_pct", 0.01))
            if self.service.ledger.is_day_paused(trade_date):
                rejected.append({"code": code, "reason": "今日模拟交易已由用户暂停"})
                continue
            order = self.service.propose_order(
                run_id=stock.get("_plan_run_id") or run_id,
                recommendation_id=recommendation_id,
                code=code,
                name=stock.get("name") or code,
                sector=sector,
                action="buy",
                quantity=quantity,
                planned_price=price,
                min_price=round(planned_entry * (1 - max_gap_below), 3),
                max_price=round(planned_entry * (1 + trigger_tolerance), 3),
                stop_price=execution_levels["stop_price"],
                target_price=execution_levels["target_price"],
                instrument_type=instrument_type,
                horizon=horizon,
                reason=stock.get("reason", ""),
                idempotency_key=f"entry:{trade_date}:{code}",
            )
            if order["status"] in {"proposed", "pre_notified"}:
                order = self.service.confirm_automatically(order["order_id"])
            if order["status"] == "filled":
                trade = self.service.ledger.get_trade_by_order(order["order_id"])
                result = {"success": True, "order": order, "trade": trade, "idempotent_replay": True}
            elif order["status"] == "confirmed":
                result = self.service.execute_ready_order(order["order_id"], quote)
            else:
                result = {"success": False, "order": order, "error": f"订单状态不可自动执行: {order['status']}"}
            execution_results.append(result)
            final = result.get("order") or self.service.get_order(order["order_id"])
            status = final.get("status") if final else "unknown"
            if result.get("success") and result.get("trade"):
                trade = result["trade"]
                detail = f"成交价¥{trade['price']:.4f}，数量{trade['quantity']}股，费用¥{trade['fees']:.2f}"
                title = "模拟订单已自动成交"
            else:
                detail = result.get("error") or "自动执行失败"
                title = "模拟订单自动执行失败"
                rejected.append({"code": code, "reason": detail})
            if notify:
                notification_results.append(
                    self.notifier.send_message(
                        title,
                        f"{order['name']}（{order['code']}）\n状态：{status}\n{detail}",
                    )
                )
            if not result.get("success"):
                continue
            prepared.append(final)
            if len(prepared) >= remaining_new_positions:
                break
        failed_notifications = [
            item for item in notification_results if item.get("status") != "success"
        ]
        return {
            "status": "success" if prepared else "no_orders",
            "orders": prepared,
            "rejected": rejected,
            "execution_results": execution_results,
            "notification_result": (
                {
                    "status": "error",
                    "message": failed_notifications[0].get("message")
                    or failed_notifications[0].get("reason")
                    or "自动成交结果未送达飞书",
                }
                if failed_notifications
                else {"status": "success", "data": {}}
            ),
            "account_equity": account["total_equity"],
            "market_regime": regime,
            "applied_policy": policy,
        }

    @staticmethod
    def _maximum_quantity(
        *,
        price: float,
        stop: float,
        max_amount: float,
        equity: float,
        cash: float,
        reserve_pct: float,
        instrument_type: str,
        max_trade_risk_pct: float,
        remaining_daily_risk: float,
    ) -> int:
        """按100股一手向下取整，同时满足资金与风险预算。"""
        if price <= 0 or stop <= 0 or stop >= price or max_amount <= 0 or remaining_daily_risk <= 0:
            return 0
        risk_budget = min(equity * max_trade_risk_pct, remaining_daily_risk)
        quantity = int(max_amount // (price * 100)) * 100
        while quantity >= 100:
            buy_cost = calculate_trade_costs(price * quantity, "buy", instrument_type)["total_cost"]
            risk_amount = calculate_net_reward_risk(
                entry_market_price=price,
                # net_risk is independent of the target leg. The actual research
                # target is checked separately by build_execution_levels below.
                target_market_price=price + (price - stop),
                stop_market_price=stop,
                quantity=quantity,
                instrument_type=instrument_type,
            )["net_risk"]
            if (
                price * quantity + buy_cost <= cash - equity * reserve_pct + 1e-9
                and risk_amount <= risk_budget + 1e-9
            ):
                return quantity
            quantity -= 100
        return 0

    def _intraday_exit_attempt(self, position, price, reason):
        """Keep a stable exit intent; rejected attempts remain immutable history.

        Each scan can create at most one successor. Deterministic attempt keys
        survive restart and concurrent scans; the service still revalidates the
        quote, availability and transaction claim before any simulated fill.
        """
        now = self.service.now()
        trade_date = now.date().isoformat()
        if self.service.ledger.is_day_paused(trade_date):
            return None, "今日模拟交易已由用户暂停，不自动重试"
        prefix = f"exit:{trade_date}:{position['code']}:"
        previous = [order for order in self.service.ledger.list_orders(trade_date=trade_date)
                    if order["action"] == "sell" and order["recommendation_id"] == position["recommendation_id"]
                    and order["idempotency_key"].startswith(prefix)]
        # Also respect legacy reason-based keys. A changed trigger text cannot
        # turn the same holding's veto/terminal rejection into a new exit intent.
        for order in previous:
            if order.get("decision") in {"veto", "pause_day"} or order["status"] in {"cancelled_by_user", "paused_for_day"}:
                return None, f"退出订单已由用户终结（{order['status']}），不自动重试"
            if order["status"] == "rejected" and order.get("reject_reason") not in RETRYABLE_EXIT_QUOTE_ERRORS:
                return None, f"已有退出订单不可自动重试：{order.get('reject_reason') or '未知拒绝原因'}"
        root = next((order for order in previous if ":retry:" not in order["idempotency_key"]), None)
        base_key = root["idempotency_key"] if root else prefix + position["recommendation_id"]
        for attempt in range(EXIT_ATTEMPT_LIMIT):
            key = base_key if attempt == 0 else f"{base_key}:retry:{attempt}"
            existing = self.service.ledger.get_order_by_idempotency(key)
            if existing:
                if existing["status"] == "rejected" and existing.get("reject_reason") in RETRYABLE_EXIT_QUOTE_ERRORS:
                    if attempt == EXIT_ATTEMPT_LIMIT - 1:
                        return None, "临时行情失败已达到当日退出重试上限，需人工核查，持仓未卖出"
                    continue
                if existing["status"] in {"proposed", "confirmed"}:
                    return existing, None
                return None, f"退出订单状态 {existing['status']}，不自动重试：{existing.get('reject_reason') or '等待核查'}"
            quantity = int(position["available_quantity"])
            if root:
                quantity = min(quantity, int(root["quantity"]))
            if quantity <= 0:
                return None, "当前没有可卖数量，不自动重试"
            return self.service.propose_order(
                run_id=root["run_id"] if root else f"{trade_date}-intraday",
                recommendation_id=position["recommendation_id"],
                code=position["code"], name=position["name"], sector=position.get("sector") or "",
                action="sell", quantity=quantity, planned_price=price,
                min_price=round(price * 0.99, 3), max_price=round(price * 1.01, 3),
                instrument_type=position.get("instrument_type", "stock"),
                horizon=position.get("horizon", "short"), reason=root["reason"] if root else reason,
                idempotency_key=key,
            ), None
        return None, "退出重试上限已达到，需人工核查"

    def execute_due_orders(self) -> Dict:
        """恢复扫描：执行已自动确认或遗留确认窗口到期的订单。"""
        if not self.enabled():
            return {"status": "disabled", "results": []}
        candidates = self.service.ledger.list_orders(
            ["confirmed", "final_notified"], self.service.now().date().isoformat()
        )
        now = self.service.now()
        confirmed_orders = [order for order in candidates if order["status"] == "confirmed"]
        timeout_orders = [
            order for order in candidates
            if order["status"] == "final_notified"
            and order.get("veto_deadline")
            and datetime.fromisoformat(order["veto_deadline"]) <= now
        ]
        results = []
        if timeout_orders:
            callback_health = self.notifier.check_callback_reachable()
            if not callback_health.get("reachable"):
                error = "飞书回调不可用，已取消超时自动执行"
                for order in timeout_orders:
                    rejected = self.service.reject_uncertain_callback(order["order_id"], error)
                    results.append({"success": False, "error": error, "order": rejected})
                    self.notifier.send_message(
                        "模拟订单已安全取消",
                        f"{order['name']}（{order['code']}）\n{error}",
                    )
                timeout_orders = []
        orders = confirmed_orders + timeout_orders
        if not orders:
            return {"status": "success", "results": results}
        quotes = self.quote_fetcher([o["code"] for o in orders])
        for order in orders:
            quote = quotes.get(order["code"])
            if not quote:
                self.service.reject_uncertain_callback(order["order_id"], "09:40行情获取失败")
                result = {"success": False, "error": "09:40行情获取失败", "order": self.service.get_order(order["order_id"])}
            else:
                result = self.service.execute_ready_order(order["order_id"], quote)
            results.append(result)
            final = result.get("order") or self.service.get_order(order["order_id"])
            status = final.get("status") if final else "unknown"
            detail = result.get("error") or f"成交价¥{result['trade']['price']:.4f}，费用¥{result['trade']['fees']:.2f}"
            self.notifier.send_message(f"模拟订单结果：{status}", f"{order['name']}（{order['code']}）\n{detail}")
        return {"status": "success", "results": results}

    def intraday_check(self, report: Dict = None, source_status: Dict = None, notify: bool = True) -> Dict:
        """每30分钟盯市；无状态变化不推送。"""
        if not self.enabled():
            return {"status": "disabled", "positions": 0, "alerts": []}
        entry_recheck = self.recheck_entry_plans(report=report, source_status=source_status, notify=False)
        positions = self.service.get_positions()
        if not positions:
            return {"status": "success", "positions": 0, "alerts": [], "entry_recheck": entry_recheck}
        quotes = self.quote_fetcher([p["code"] for p in positions])
        prices = {code: float(info["price"]) for code, info in quotes.items() if info.get("price")}
        self.service.ledger.update_market_prices(prices, self.service.now())
        account = self.service.get_account()
        self.service.record_equity_snapshot(account["total_equity"])
        metrics = self.service.get_performance_metrics()
        alerts = []
        if metrics["max_drawdown_pct"] >= 10:
            alerts.append("最大回撤达到10%，已要求暂停新开仓")

        for position in self.service.get_positions():
            price = prices.get(position["code"])
            if not price:
                alerts.append(f"{position['code']} 行情不可用")
                continue
            levels = self.service.ledger.risk_levels(position["recommendation_id"])
            reason = None
            if levels.get("stop_price") and price <= levels["stop_price"]:
                reason = f"触发止损价¥{levels['stop_price']:.2f}"
            elif levels.get("target_price") and price >= levels["target_price"]:
                reason = f"达到目标观察价¥{levels['target_price']:.2f}"
            if reason is None:
                import yaml
                cfg_path = Path(__file__).parents[2] / "config" / "horizons.yaml"
                with open(cfg_path, encoding="utf-8") as handle:
                    horizons = (yaml.safe_load(handle) or {}).get("horizons", {})
                max_days = horizons.get(position.get("horizon", "short"), {}).get("max_days")
                if max_days and position.get("entry_date"):
                    held_days = count_trading_days(date.fromisoformat(position["entry_date"]), self.service.now().date())
                    if held_days >= int(max_days):
                        reason = f"达到{position.get('horizon', 'short')}周期最长持有{max_days}个交易日"
            if reason:
                if position["available_quantity"] <= 0:
                    alerts.append(f"{position['name']} {reason}，但受T+1限制，下一可卖时点处理")
                else:
                    order, blocked = self._intraday_exit_attempt(position, price, reason)
                    if blocked:
                        alerts.append(f"{position['name']} {reason}，{blocked}")
                        continue
                    if order["status"] == "proposed":
                        order = self.service.confirm_automatically(order["order_id"])
                    if order["status"] == "confirmed":
                        result = self.service.execute_ready_order(order["order_id"], quotes[position["code"]])
                        final = result.get("order") or self.service.get_order(order["order_id"])
                        if result.get("success") and result.get("trade"):
                            trade = result["trade"]
                            alerts.append(
                                f"{position['name']} {reason}，已自动卖出{trade['quantity']}股，"
                                f"成交价¥{trade['price']:.4f}，费用¥{trade['fees']:.2f}"
                            )
                        else:
                            alerts.append(
                                f"{position['name']} {reason}，自动卖出失败："
                                f"{result.get('error') or (final or {}).get('reject_reason') or '未知原因'}"
                            )
        if alerts and notify:
            self.notifier.send_message("盘中风险变化", "\n".join(f"- {a}" for a in alerts))
        return {"status": "success", "positions": len(positions), "alerts": alerts, "metrics": metrics, "entry_recheck": entry_recheck}
