# -*- coding: utf-8 -*-
"""安全的自动模拟交易服务。"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo

from src.data_collectors.trading_calendar import count_trading_days
from src.paper_trading.quality_gate import calculate_net_reward_risk
from src.paper_trading.trade_accounting import calculate_fifo_accounting
from src.storage.trading_ledger import TradingLedger
from src.strategy.position_limits import (
    get_cash_reserve_pct,
    get_initial_cash,
    get_paper_trading_config,
    get_position_limit,
    get_sector_limit,
)
from src.utils.cost_calculator import calculate_trade_costs


SHANGHAI = ZoneInfo("Asia/Shanghai")
TERMINAL_STATUSES = {"filled", "rejected", "cancelled_by_user", "paused_for_day", "expired"}


class TradingService:
    """所有模拟成交的唯一入口。

    新订单可以由唯一执行节点自动确认后进入 revalidating；历史交互订单仍兼容
    final_notified/confirmed 状态。任何行情或账本状态不确定都安全拒绝。
    """

    def __init__(self, data_dir: Path = None, now_provider: Callable[[], datetime] = None):
        self.data_dir = Path(data_dir or Path(__file__).parents[2] / "data")
        self.ledger = TradingLedger(self.data_dir)
        self._now_provider = now_provider or (lambda: datetime.now(SHANGHAI))

    def now(self) -> datetime:
        value = self._now_provider()
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI)
        return value.astimezone(SHANGHAI)

    def initialize_account(self, initial_cash: float = None, reset: bool = False) -> Dict:
        return self.ledger.initialize_account(initial_cash or get_initial_cash(), reset=reset)

    def get_account(self) -> Dict:
        account = self.ledger.get_account()
        summary = self.get_trade_accounting()["summary"]
        account["legacy_realized_pnl"] = account["realized_pnl"]
        account["realized_pnl"] = summary["realized_pnl"]
        account["accounting_status"] = summary["accounting_status"]
        account["unrealized_pnl"] = round(account["unrealized_pnl"] - summary["unallocated_buy_fees"], 2)
        return account

    def get_trade_accounting(self) -> Dict:
        """All-history FIFO, with both-side fees; no LIMIT and no writes."""
        with closing(self.ledger.connect()) as conn:
            trades = [dict(row) for row in conn.execute(
                "SELECT * FROM trading_trades ORDER BY executed_at, rowid"
            )]
        return calculate_fifo_accounting(trades)

    def update_available_cash(self, cash: float) -> Dict:
        return self.ledger.update_available_cash(cash)

    def get_positions(self, as_of_date: str = None):
        return self.ledger.positions(as_of_date or self.now().date().isoformat())

    def get_order(self, order_id: str) -> Optional[Dict]:
        return self.ledger.get_order(order_id)

    def propose_order(
        self,
        *,
        run_id: str,
        recommendation_id: str,
        code: str,
        name: str,
        sector: str,
        action: str,
        quantity: int,
        planned_price: float,
        min_price: float = None,
        max_price: float = None,
        stop_price: float = None,
        target_price: float = None,
        instrument_type: str = "stock",
        horizon: str = "short",
        reason: str = "",
        idempotency_key: str = None,
    ) -> Dict:
        if not run_id or not recommendation_id:
            raise ValueError("run_id 和 recommendation_id 必填")
        if action not in {"buy", "sell"}:
            raise ValueError("action 必须是 buy 或 sell")
        if quantity <= 0 or planned_price <= 0:
            raise ValueError("数量和计划价格必须大于0")
        if action == "buy" and quantity % 100:
            raise ValueError("A股买入数量必须是100股的整数倍")
        if self.ledger.is_day_paused(self.now().date().isoformat()):
            raise ValueError("今日模拟交易已由用户暂停")
        payload = {
            "run_id": run_id,
            "recommendation_id": recommendation_id,
            "code": code,
            "name": name,
            "sector": sector or "",
            "action": action,
            "quantity": quantity,
            "planned_price": planned_price,
            "min_price": min_price,
            "max_price": max_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "instrument_type": instrument_type,
            "horizon": horizon,
            "reason": reason,
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return self.ledger.create_order(payload, self.now())

    def mark_final_notified(
        self, order_id: str, notified_at: datetime = None, veto_minutes: int = 5
    ) -> Dict:
        order = self._require_status(order_id, {"proposed", "pre_notified"})
        notified_at = notified_at or self.now()
        if notified_at.tzinfo is None:
            notified_at = notified_at.replace(tzinfo=SHANGHAI)
        deadline = notified_at + timedelta(minutes=veto_minutes)
        return self.ledger.update_order(
            order["order_id"],
            "final_notified",
            self.now(),
            notified_at=notified_at.isoformat(),
            veto_deadline=deadline.isoformat(),
        )

    def confirm_automatically(self, order_id: str, actor: str = "system_auto") -> Dict:
        """将新订单标记为系统自动确认，不创建人工确认窗口。"""
        order = self._require_status(order_id, {"proposed", "pre_notified", "confirmed"})
        if order["status"] == "confirmed":
            return order
        now = self.now()
        event_id = hashlib.sha256(f"{order_id}:auto_execute:{actor}".encode()).hexdigest()
        # 决策写入和订单状态更新分属两个短事务。即使进程恰好在两者之间
        # 中断，重跑时也必须继续把订单推进到 confirmed，而不能永久卡住。
        self.ledger.record_decision(order_id, "auto_execute", actor, now, event_id)
        # 必须在 UPDATE 中校验状态；先读后无条件写会复活并发否决的订单。
        return self.ledger.confirm_auto_order(order_id, actor, now)

    def record_decision(
        self,
        order_id: str,
        decision: str,
        actor: str = "feishu_user",
        event_id: str = None,
    ) -> Dict:
        order = self._require_status(order_id, {"final_notified", "confirmed"})
        now = self.now()
        deadline = datetime.fromisoformat(order["veto_deadline"])
        statuses = {
            "confirm": "confirmed",
            "veto": "cancelled_by_user",
            "pause_day": "paused_for_day",
        }
        if decision not in statuses:
            raise ValueError("未知飞书操作")
        if now > deadline:
            raise ValueError("交互窗口已结束")
        event_id = event_id or hashlib.sha256(
            f"{order_id}:{decision}:{actor}:{now.isoformat()}".encode()
        ).hexdigest()
        if not self.ledger.record_decision(order_id, decision, actor, now, event_id):
            return self.get_order(order_id)
        if decision == "pause_day":
            self.ledger.pause_day(now.date().isoformat(), now, actor)
            return self.get_order(order_id)
        return self.ledger.update_order(
            order_id,
            statuses[decision],
            now,
            decision=decision,
            decision_actor=actor,
            decided_at=now.isoformat(),
        )

    def execute_ready_order(self, order_id: str, quote: Dict) -> Dict:
        order = self.get_order(order_id)
        if not order:
            return self._failure("订单不存在")
        if order["status"] in TERMINAL_STATUSES:
            return self._failure(f"订单已终结: {order['status']}", order)

        now = self.now()
        if order["status"] == "final_notified":
            deadline = datetime.fromisoformat(order["veto_deadline"])
            if now < deadline:
                return self._failure("等待否决窗口结束", order)
        elif order["status"] != "confirmed":
            return self._failure(f"订单状态不可执行: {order['status']}", order)

        if not self.ledger.claim_order(order_id, order["status"], now):
            current = self.get_order(order_id)
            trade = self.ledger.get_trade_by_order(order_id)
            if current and current["status"] == "filled" and trade:
                return {"success": True, "order": current, "trade": trade, "idempotent_replay": True}
            return self._failure("订单已被其他执行器处理", current)
        error = self._validate_quote(order, quote, now)
        if not error:
            error = self._validate_risk(order, float(quote["price"]), now.date().isoformat())
        if error:
            rejected = self.ledger.update_order(order_id, "rejected", now, reject_reason=error)
            return self._failure(error, rejected)

        try:
            trade = self._fill(order, float(quote["price"]), now)
        except Exception as exc:
            current = self.get_order(order_id)
            trade = self.ledger.get_trade_by_order(order_id)
            if current and current["status"] == "filled" and trade:
                return {"success": True, "order": current, "trade": trade, "idempotent_replay": True}
            rejected = self.ledger.update_order(order_id, "rejected", now, reject_reason=f"账本事务失败: {exc}")
            return self._failure(rejected["reject_reason"], rejected)
        return {"success": True, "order": self.get_order(order_id), "trade": trade}

    def _validate_quote(self, order: Dict, quote: Dict, now: datetime) -> Optional[str]:
        if not self._is_market_session(now):
            return "当前不在A股可撮合交易时段"
        if quote.get("code") != order["code"]:
            return "行情代码与订单不一致"
        if quote.get("trade_status") != "trading":
            return "标的当前不可交易"
        if quote.get("source_time_reliable") is False:
            return "行情缺少可靠的源时间戳"
        if quote.get("trade_date") != now.date().isoformat():
            return "行情交易日期不符"
        try:
            quote_time = datetime.fromisoformat(quote["quote_time"])
            if quote_time.tzinfo is None:
                quote_time = quote_time.replace(tzinfo=SHANGHAI)
            if abs((now - quote_time.astimezone(SHANGHAI)).total_seconds()) > 60:
                return "行情已过期"
            price = float(quote["price"])
        except (KeyError, TypeError, ValueError):
            return "行情字段不完整"
        if price <= 0:
            return "行情价格无效"
        if order.get("min_price") is not None and price < order["min_price"]:
            return "最新价低于允许成交区间"
        if order.get("max_price") is not None and price > order["max_price"]:
            return "最新价高于允许成交区间"
        return None

    @staticmethod
    def _is_market_session(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        local = now.time().replace(tzinfo=None)
        return time(9, 30) <= local <= time(11, 30) or time(13, 0) <= local <= time(15, 0)

    def _validate_risk(self, order: Dict, price: float, trade_date: str) -> Optional[str]:
        account = self.get_account()
        positions = self.get_positions(trade_date)
        amount = round(price * order["quantity"], 2)
        costs = calculate_trade_costs(amount, order["action"], order["instrument_type"])

        if order["action"] == "sell":
            position = next((p for p in positions if p["code"] == order["code"]), None)
            if not position:
                return "没有可卖持仓"
            if position["available_quantity"] < order["quantity"]:
                return "T+1限制或可卖数量不足：未核实T+0资格的证券不可当日买入卖出"
            return None

        risk_state = self.get_risk_state(trade_date)
        if risk_state["max_drawdown_pct"] >= 10:
            return "最大回撤达到10%，新开仓已暂停"
        if risk_state["daily_loss_pct"] >= 1.5:
            return "当日亏损达到1.5%，新开仓已暂停"
        config = get_paper_trading_config()
        equity = float(account["total_equity"])
        min_order_amount = float(config.get("min_order_amount", 1000))
        if amount < min_order_amount:
            return f"单笔金额低于¥{min_order_amount:,.0f}经济订单下限"
        current_position = next((p for p in positions if p["code"] == order["code"]), None)
        max_positions = int(config.get("max_positions", 5))
        if len(positions) >= max_positions and not current_position:
            return f"账户最多同时持有{max_positions}只标的"
        if not current_position:
            buys_today = {
                trade["code"] for trade in self.ledger.list_trades(trade_date=trade_date, limit=1000)
                if trade.get("action") == "buy"
            }
            max_new_positions = int(config.get("max_new_positions_per_day", 2))
            if order["code"] not in buys_today and len(buys_today) >= max_new_positions:
                return f"今日最多新开仓{max_new_positions}只"
            same_sector = [
                position for position in positions
                if (position.get("sector") or "其他") == (order.get("sector") or "其他")
            ]
            same_sector_max = int(config.get("same_sector_max_positions", 2))
            if len(same_sector) >= same_sector_max:
                return f"同一板块最多持有{same_sector_max}只标的"
            cooldown_error = self._reentry_cooldown_error(order["code"], trade_date, config)
            if cooldown_error:
                return cooldown_error
        total_cost = amount + costs["total_cost"]
        if total_cost > account["cash"]:
            return "现金不足"
        current_value = float(current_position.get("market_value") or 0) if current_position else 0.0
        max_position_pct = min(
            float(config.get("max_position_pct", 0.30)),
            get_position_limit(equity, order.get("horizon") or "short"),
        )
        if (current_value + amount) / equity > max_position_pct + 1e-9:
            return f"单只持仓超过{max_position_pct*100:.0f}%上限"
        reserve_pct = max(float(config.get("cash_reserve_pct", 0.20)), get_cash_reserve_pct(equity))
        if account["cash"] - total_cost < equity * reserve_pct:
            return f"成交后现金储备低于{reserve_pct*100:.0f}%"
        total_exposure = sum(float(position.get("market_value") or 0) for position in positions)
        max_total_exposure_pct = float(config.get("max_total_exposure_pct", 0.80))
        if (total_exposure + amount) / equity > max_total_exposure_pct + 1e-9:
            return f"总持仓超过{max_total_exposure_pct*100:.0f}%上限"
        sector_exposure = sum(
            float(position.get("market_value") or 0)
            for position in positions
            if (position.get("sector") or "其他") == (order.get("sector") or "其他")
        )
        sector_limit = get_sector_limit(equity)
        if (sector_exposure + amount) / equity > sector_limit + 1e-9:
            return f"板块持仓超过{sector_limit*100:.0f}%上限"
        stop = order.get("stop_price")
        if not stop or stop >= price:
            return "买入订单缺少有效止损价"
        target = order.get("target_price")
        if not target or target <= price:
            return "买入订单缺少有效目标价"
        reward_risk = calculate_net_reward_risk(
            entry_market_price=price,
            target_market_price=float(target),
            stop_market_price=float(stop),
            quantity=int(order["quantity"]),
            instrument_type=order["instrument_type"],
        )
        min_gross_ratio = float(config.get("min_gross_reward_risk_ratio", 2.0))
        if reward_risk["gross_ratio"] + 1e-9 < min_gross_ratio:
            return (
                f"毛盈亏比{reward_risk['gross_ratio']:.2f}低于"
                f"{min_gross_ratio:.2f}质量门"
            )
        min_net_ratio = float(config.get("min_net_reward_risk_ratio", 1.5))
        if reward_risk["net_ratio"] + 1e-9 < min_net_ratio:
            return (
                f"成本后盈亏比{reward_risk['net_ratio']:.2f}低于"
                f"{min_net_ratio:.2f}质量门"
            )
        risk_amount = reward_risk["net_risk"]
        account_risk = risk_amount / equity
        max_trade_risk_pct = float(config.get("max_trade_risk_pct", 0.0075))
        if account_risk > max_trade_risk_pct + 1e-9:
            return f"单笔账户风险{account_risk*100:.2f}%超过{max_trade_risk_pct*100:.2f}%"
        max_daily_new_risk_pct = float(config.get("max_daily_new_risk_pct", 0.015))
        if self.daily_new_risk_amount(trade_date) + risk_amount > equity * max_daily_new_risk_pct + 1e-9:
            return f"当日新增风险超过{max_daily_new_risk_pct*100:.2f}%"
        return None

    def daily_new_risk_amount(self, trade_date: str) -> float:
        """当日成交买单的止损风险，包含真实买入费用及预计卖出成本。"""
        risk = 0.0
        for order in self.ledger.list_orders(["filled"], trade_date):
            if order.get("action") != "buy" or not order.get("stop_price"):
                continue
            trade = self.ledger.get_trade_by_order(order["order_id"])
            if not trade:
                continue
            price = float(trade["price"])
            stop = float(order["stop_price"])
            if stop >= price:
                continue
            quantity = int(trade["quantity"])
            exit_cost = calculate_trade_costs(
                stop * quantity, "sell", order.get("instrument_type") or "stock"
            )["total_cost"]
            # 成交 price 已含买入滑点，只补实际买入 fees，不能重复估算滑点。
            risk += (price - stop) * quantity + float(trade["fees"]) + exit_cost
        return round(risk, 4)

    def _reentry_cooldown_error(self, code: str, trade_date: str, config: Dict) -> Optional[str]:
        cooldown_days = int(config.get("reentry_cooldown_trading_days", 2))
        if cooldown_days <= 0:
            return None
        sells = [
            trade for trade in self.ledger.list_trades(limit=1000)
            if trade.get("action") == "sell" and trade.get("code") == code
        ]
        if not sells:
            return None
        last_sell = max(str(trade["trade_date"]) for trade in sells)
        elapsed = count_trading_days(date.fromisoformat(last_sell), date.fromisoformat(trade_date))
        if elapsed <= cooldown_days:
            return f"卖出后需冷却{cooldown_days}个交易日，当前仅过{elapsed}个"
        return None

    def _fill(self, order: Dict, market_price: float, now: datetime) -> Dict:
        costs = calculate_trade_costs(
            market_price * order["quantity"], order["action"], order["instrument_type"]
        )
        fill_price = round(market_price * costs["effective_price_factor"], 4)
        amount = round(fill_price * order["quantity"], 2)
        trade_id = f"TRD-{now:%Y%m%d}-{uuid.uuid4().hex[:12]}"
        realized_pnl = None
        with self.ledger.transaction() as conn:
            fresh = self.ledger.get_order(order["order_id"], conn)
            if fresh["status"] != "revalidating":
                raise RuntimeError("订单状态被并发修改")
            account = self.ledger.get_account(conn)
            if order["action"] == "buy":
                debit = amount + costs["fees"]
                if account["cash"] < debit:
                    raise RuntimeError("成交时现金不足")
                conn.execute(
                    "UPDATE trading_accounts SET cash=cash-?,updated_at=? WHERE account_id='default'",
                    (debit, now.isoformat()),
                )
            else:
                lots = conn.execute(
                    "SELECT l.*, t.fees AS buy_fees, t.amount AS buy_amount, t.quantity AS buy_quantity "
                    "FROM position_lots l JOIN trading_trades t ON t.trade_id=l.buy_trade_id "
                    "WHERE l.code=? AND l.quantity>0 AND is_lot_sellable(l.code,l.acquired_date,?) "
                    "ORDER BY t.executed_at,t.rowid",
                    (order["code"], now.date().isoformat()),
                ).fetchall()
                remaining = order["quantity"]
                cost_basis = 0.0
                for lot in lots:
                    used = min(remaining, lot["quantity"])
                    if not used:
                        continue
                    cost_basis += used * ((lot["buy_amount"] + lot["buy_fees"]) / lot["buy_quantity"])
                    conn.execute("UPDATE position_lots SET quantity=quantity-?,updated_at=? WHERE lot_id=?", (used, now.isoformat(), lot["lot_id"]))
                    remaining -= used
                    if remaining == 0:
                        break
                if remaining:
                    raise RuntimeError("T+1可卖数量不足")
                credit = amount - costs["fees"]
                realized_pnl = round(credit - cost_basis, 2)
                conn.execute(
                    "UPDATE trading_accounts SET cash=cash+?,realized_pnl=realized_pnl+?,updated_at=? WHERE account_id='default'",
                    (credit, realized_pnl, now.isoformat()),
                )
            conn.execute(
                "INSERT INTO trading_trades(trade_id,order_id,code,action,quantity,price,amount,fees,slippage,realized_pnl,executed_at,trade_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade_id, order["order_id"], order["code"], order["action"], order["quantity"], fill_price, amount, costs["fees"], costs["slippage"], realized_pnl, now.isoformat(), now.date().isoformat()),
            )
            if order["action"] == "buy":
                conn.execute(
                    "INSERT INTO position_lots(lot_id,buy_trade_id,recommendation_id,code,name,sector,instrument_type,horizon,acquired_date,quantity,avg_cost,current_price,highest_price,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"LOT-{uuid.uuid4().hex[:12]}", trade_id, order["recommendation_id"], order["code"], order["name"], order["sector"], order["instrument_type"], order["horizon"], now.date().isoformat(), order["quantity"], fill_price, fill_price, fill_price, now.isoformat()),
                )
            conn.execute(
                "UPDATE trading_orders SET status='filled',updated_at=? WHERE order_id=?",
                (now.isoformat(), order["order_id"]),
            )
        return {
            "trade_id": trade_id,
            "order_id": order["order_id"],
            "code": order["code"],
            "action": order["action"],
            "quantity": order["quantity"],
            "price": fill_price,
            "amount": amount,
            "fees": costs["fees"],
            "slippage": costs["slippage"],
            "realized_pnl": realized_pnl,
            "executed_at": now.isoformat(),
        }

    def record_equity_snapshot(self, total_equity: float, at: str = None) -> None:
        captured_at = at or self.now().isoformat()
        with self.ledger.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO equity_snapshots(captured_at,total_equity) VALUES(?,?)",
                (captured_at, total_equity),
            )

    def get_performance_metrics(self) -> Dict:
        account = self.get_account()
        snapshots = self.ledger.snapshots()
        cash_adjustments = self.ledger.cash_adjustments()
        live_equity = float(account["total_equity"])
        current = live_equity
        current_at = account["updated_at"]
        account_updated_at = datetime.fromisoformat(account["updated_at"])
        latest_snapshot_at = None
        if snapshots:
            latest_snapshot_at = datetime.fromisoformat(snapshots[-1]["captured_at"])
            if (account_updated_at.tzinfo is None) != (latest_snapshot_at.tzinfo is None):
                account_updated_at = account_updated_at.replace(tzinfo=None)
                latest_snapshot_at = latest_snapshot_at.replace(tzinfo=None)
            # 空仓账户的净值变化只能来自显式快照；初始化时间不能把当天
            # 较早写入的收盘快照错误覆盖为初始资金。若账户/持仓确有变化，
            # 再用更新时间选择账户实时值或更新的快照。
            account_has_live_change = any((
                abs(live_equity - float(account["initial_cash"])) > 1e-9,
                abs(float(account.get("cash") or 0) - float(account["initial_cash"])) > 1e-9,
                abs(float(account.get("market_value") or 0)) > 1e-9,
                abs(float(account.get("realized_pnl") or 0)) > 1e-9,
            ))
            if not account_has_live_change or latest_snapshot_at > account_updated_at:
                current = float(snapshots[-1]["total_equity"])
                current_at = snapshots[-1]["captured_at"]

        def principal_at(captured_at: str) -> float:
            adjustment = sum(
                float(item["amount"])
                for item in cash_adjustments
                if item["created_at"] <= captured_at
            )
            return float(account["initial_cash"]) + adjustment

        current_principal = principal_at(current_at)
        # 以资金流调整后的净值指数计算回撤，充值/取现不会被误算为盈亏。
        values = [1.0]
        values.extend(
            float(snapshot["total_equity"]) / principal_at(snapshot["captured_at"])
            for snapshot in snapshots
        )
        if not snapshots or current == live_equity:
            values.append(current / current_principal)
        max_drawdown = 0.0
        peak = 1.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
        accounting = self.get_trade_accounting()
        trades = accounting["trades"]
        total_fees = round(sum(float(t.get("fees") or 0) for t in trades), 2)
        total_slippage = round(sum(float(t.get("slippage") or 0) for t in trades), 2)
        net_return = round(current - current_principal, 2)
        net_return_pct = round((current / current_principal - 1) * 100, 4)
        return {
            "initial_cash": account["initial_cash"],
            "capital_adjustment": account["net_cash_adjustment"],
            "effective_principal": round(current_principal, 2),
            "total_equity": current,
            "net_return": net_return,
            "net_return_pct": net_return_pct,
            "net_return_after_costs": net_return,
            "net_return_after_costs_pct": net_return_pct,
            "max_drawdown_pct": round(max_drawdown, 4),
            "total_fees": total_fees,
            "total_slippage": total_slippage,
            "total_transaction_costs": round(total_fees + total_slippage, 2),
            "closed_trades": accounting["summary"]["closed_trades"],
            "trade_win_rate_pct": accounting["summary"]["win_rate_pct"],
            "realized_pnl": accounting["summary"]["realized_pnl"],
            "accounting_status": accounting["summary"]["accounting_status"],
            "unmatched_sells": accounting["summary"]["unmatched_sells"],
            "cost_basis": "fees_and_slippage_included",
        }

    def get_risk_state(self, trade_date: str = None) -> Dict:
        trade_date = trade_date or self.now().date().isoformat()
        snapshots = self.ledger.snapshots()
        today_values = [
            float(item["total_equity"])
            for item in snapshots
            if item["captured_at"][:10] == trade_date
        ]
        daily_loss = 0.0
        if len(today_values) >= 2 and today_values[0] > 0:
            daily_loss = max(0.0, (today_values[0] - today_values[-1]) / today_values[0] * 100)
        return {
            "daily_loss_pct": round(daily_loss, 4),
            "max_drawdown_pct": self.get_performance_metrics()["max_drawdown_pct"],
        }

    def reject_uncertain_callback(self, order_id: str, reason: str = "飞书回调状态无法确认") -> Dict:
        return self.ledger.update_order(order_id, "rejected", self.now(), reject_reason=reason)

    def _require_status(self, order_id: str, allowed: set) -> Dict:
        order = self.get_order(order_id)
        if not order:
            raise ValueError("订单不存在")
        if order["status"] not in allowed:
            raise ValueError(f"订单状态不允许此操作: {order['status']}")
        return order

    @staticmethod
    def _failure(error: str, order: Dict = None) -> Dict:
        return {"success": False, "error": error, "order": order}
