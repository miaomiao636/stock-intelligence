# -*- coding: utf-8 -*-
"""09:35自动执行、订单恢复扫描和盘中监控编排。"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List

from src.data_collectors.realtime_prices import fetch_realtime_prices
from src.notifier.feishu import FeishuNotifier
from src.paper_trading.trading_service import TradingService


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

    @staticmethod
    def enabled() -> bool:
        return os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true"

    def prepare_final_orders(self, report: Dict, source_status: Dict = None) -> Dict:
        """用09:35行情重算，通过风控后立即模拟成交并推送结果。"""
        if not self.enabled():
            return {"status": "disabled", "orders": [], "reason": "PAPER_TRADING_ENABLED=false"}
        source_status = source_status or {}
        if source_status.get("llm") in {"fallback", "error", "degraded"}:
            return {"status": "safe_mode", "orders": [], "reason": "LLM或数据降级，禁止开仓"}
        if not str(source_status.get("candidate_universe", "")).startswith("ok_"):
            return {"status": "safe_mode", "orders": [], "reason": "全市场候选池未通过，禁止开仓"}
        if not str(source_status.get("recommendation_prices", "")).startswith("ok_"):
            return {"status": "safe_mode", "orders": [], "reason": "推荐价格未全部通过真实行情校验，禁止开仓"}
        account = self.service.get_account()
        regime = report.get("market_regime", "neutral")
        if regime in {"bearish", "high_volatility"}:
            return {
                "status": "safe_mode",
                "orders": [],
                "reason": f"市场状态{regime}，¥{account['total_equity']:,.0f}账户暂停新开仓",
            }
        candidates = [
            stock for stock in report.get("stock_recommendations", [])
            if stock.get("action") == "setup_ready"
            and stock.get("trade_eligible") is True
            and stock.get("price_validation", {}).get("verified") is True
            and float(stock.get("confidence") or 0) >= 4
        ]
        codes = [stock.get("code") for stock in candidates if stock.get("code")]
        quotes = self.quote_fetcher(codes)
        self.service.record_equity_snapshot(account["total_equity"], at=self.service.now().isoformat())
        run_id = report.get("run_id") or f"{self.service.now().date().isoformat()}-open"
        prepared, rejected, execution_results, notification_results = [], [], [], []

        for stock in candidates[:4]:
            code = stock.get("code")
            quote = quotes.get(code) or {}
            price = float(quote.get("price") or 0)
            if price <= 0:
                rejected.append({"code": code, "reason": "09:35行情不可用"})
                continue
            position_pct = 0.25 if regime == "range" else 0.30
            max_amount = min(account["total_equity"] * position_pct, account["cash"] - account["total_equity"] * 0.30)
            quantity = int(max_amount // (price * 100)) * 100
            if quantity < 100 or price * quantity < 1000:
                rejected.append({
                    "code": code,
                    "reason": (
                        f"100股需¥{price * 100:,.0f}，超过当前单票可用上限¥{max_amount:,.0f}，"
                        "或不满足¥1,000最小订单"
                    ),
                })
                continue
            recommendation_id = stock.get("recommendation_id") or f"REC-{run_id}-{code}"
            stop = stock.get("stop_loss_price") or stock.get("timing", {}).get("stop_loss_price")
            target = stock.get("target_price") or stock.get("timing", {}).get("target_observation_price")
            order = self.service.propose_order(
                run_id=run_id,
                recommendation_id=recommendation_id,
                code=code,
                name=stock.get("name") or code,
                sector=stock.get("sector") or "其他",
                action="buy",
                quantity=quantity,
                planned_price=price,
                min_price=round(price * 0.99, 3),
                max_price=round(price * 1.01, 3),
                stop_price=float(stop or price * 0.97),
                target_price=float(target or price * 1.06),
                instrument_type=stock.get("instrument_type", "stock"),
                horizon=stock.get("horizon", "short"),
                reason=stock.get("reason", ""),
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
            notification_results.append(
                self.notifier.send_message(
                    title,
                    f"{order['name']}（{order['code']}）\n状态：{status}\n{detail}",
                )
            )
            if not result.get("success"):
                continue
            prepared.append(final)
            if len(prepared) >= 2:
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
        }

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

    def intraday_check(self) -> Dict:
        """每30分钟盯市；无状态变化不推送。"""
        if not self.enabled():
            return {"status": "disabled", "positions": 0, "alerts": []}
        positions = self.service.get_positions()
        if not positions:
            return {"status": "success", "positions": 0, "alerts": []}
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
                    held_days = (date.fromisoformat(self.service.now().date().isoformat()) - date.fromisoformat(position["entry_date"])).days
                    if held_days >= int(max_days):
                        reason = f"达到{position.get('horizon', 'short')}周期最长持有{max_days}天"
            if reason:
                if position["available_quantity"] <= 0:
                    alerts.append(f"{position['name']} {reason}，但受T+1限制，下一可卖时点处理")
                else:
                    order = self.service.propose_order(
                        run_id=f"{self.service.now().date().isoformat()}-intraday",
                        recommendation_id=position["recommendation_id"],
                        code=position["code"],
                        name=position["name"],
                        sector=position.get("sector") or "",
                        action="sell",
                        quantity=int(position["available_quantity"]),
                        planned_price=price,
                        min_price=round(price * 0.99, 3),
                        max_price=round(price * 1.01, 3),
                        instrument_type=position.get("instrument_type", "stock"),
                        horizon=position.get("horizon", "short"),
                        reason=reason,
                        idempotency_key=f"exit:{self.service.now().date().isoformat()}:{position['code']}:{reason}",
                    )
                    if order["status"] == "proposed":
                        order = self.service.confirm_automatically(order["order_id"])
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
        if alerts:
            self.notifier.send_message("盘中风险变化", "\n".join(f"- {a}" for a in alerts))
        return {"status": "success", "positions": len(positions), "alerts": alerts, "metrics": metrics}
