# -*- coding: utf-8 -*-
"""安全的半自动模拟交易服务。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo

from src.storage.trading_ledger import TradingLedger
from src.utils.cost_calculator import calculate_trade_costs


SHANGHAI = ZoneInfo("Asia/Shanghai")
TERMINAL_STATUSES = {"filled", "rejected", "cancelled_by_user", "paused_for_day", "expired"}


class TradingService:
    """所有模拟成交的唯一入口。

    订单必须先 proposed，再 final_notified，之后由明确确认或五分钟无操作进入
    revalidating。任何行情、回调或账本状态不确定都安全拒绝。
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

    def initialize_account(self, initial_cash: float = 4000, reset: bool = False) -> Dict:
        return self.ledger.initialize_account(initial_cash, reset=reset)

    def get_account(self) -> Dict:
        return self.ledger.get_account()

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
            self.ledger.pause_day(now.date().isoformat(), now)
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
                return "T+1限制：当日买入普通A股不可卖出"
            return None

        risk_state = self.get_risk_state(trade_date)
        if risk_state["max_drawdown_pct"] >= 10:
            return "最大回撤达到10%，新开仓已暂停"
        if risk_state["daily_loss_pct"] >= 1.5:
            return "当日亏损达到1.5%，新开仓已暂停"
        if amount < 1000:
            return "单笔金额低于¥1,000经济订单下限"
        if len(positions) >= 2 and not any(p["code"] == order["code"] for p in positions):
            return "¥4,000账户最多同时持有2只标的"
        total_cost = amount + costs["total_cost"]
        if total_cost > account["cash"]:
            return "现金不足"
        if amount / account["total_equity"] > 0.30 + 1e-9:
            return "单只初始仓位超过30%"
        if account["cash"] - total_cost < account["total_equity"] * 0.30:
            return "成交后现金储备低于30%"
        stop = order.get("stop_price")
        if not stop or stop >= price:
            return "买入订单缺少有效止损价"
        exit_cost = calculate_trade_costs(stop * order["quantity"], "sell", order["instrument_type"])["total_cost"]
        account_risk = ((price - stop) * order["quantity"] + exit_cost) / account["total_equity"]
        if account_risk > 0.01 + 1e-9:
            return f"单笔账户风险{account_risk*100:.2f}%超过1%"
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
                    "SELECT * FROM position_lots WHERE code=? AND quantity>0 AND (instrument_type!='stock' OR acquired_date<?) ORDER BY acquired_date,lot_id",
                    (order["code"], now.date().isoformat()),
                ).fetchall()
                remaining = order["quantity"]
                cost_basis = 0.0
                for lot in lots:
                    used = min(remaining, lot["quantity"])
                    if not used:
                        continue
                    cost_basis += used * lot["avg_cost"]
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
        values = [float(s["total_equity"]) for s in snapshots]
        max_drawdown = 0.0
        peak = values[0] if values else account["initial_cash"]
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
        current = values[-1] if values else account["total_equity"]
        return {
            "initial_cash": account["initial_cash"],
            "total_equity": current,
            "net_return": round(current - account["initial_cash"], 2),
            "net_return_pct": round((current / account["initial_cash"] - 1) * 100, 4),
            "max_drawdown_pct": round(max_drawdown, 4),
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
