# -*- coding: utf-8 -*-
"""Auto Trader - connects morning pipeline to paper trading"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.paper_trading.planner import Planner
from src.paper_trading.executor import Executor
from src.paper_trading.account import PaperAccount
from src.paper_trading.portfolio import PaperPortfolio
from src.paper_trading.risk_manager import RiskManager
from src.reporting.report_store import load_report


class AutoTrader:
    """Automatic paper trading after morning analysis"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data"
        self.trade_log_dir = self.data_dir / "paper_trading" / "auto_trades"
        self.trade_log_dir.mkdir(parents=True, exist_ok=True)

    def execute_morning_trades(self, recommendation: Dict, date_str: str = None) -> Dict:
        """Execute trades based on morning recommendation.

        Flow:
        1. Load account & current positions
        2. Generate buy orders from setup_ready stocks
        3. Check risk constraints (position/sector/cash limits)
        4. Execute orders at entry_price (or current_price)
        5. Update account cash and positions
        6. Log all trades
        """
        raise RuntimeError("旧AutoTrader成交路径已停用；请使用PaperTradingWorkflow")
        if date_str is None:
            date_str = date.today().isoformat()

        account = PaperAccount()
        portfolio = PaperPortfolio()
        executor = Executor()
        planner = Planner()

        account_data = account.get_account()
        total_equity = account_data.get("total_equity", account_data.get("cash", 4000))
        available_cash = account_data.get("cash", 0)

        # 统一从 strategy.yaml 读取现金保留比例（与 risk_manager 一致）
        from src.strategy.position_limits import get_cash_reserve_pct, get_position_limit
        cash_reserve_pct = get_cash_reserve_pct(total_equity)
        cash_reserve = total_equity * cash_reserve_pct
        tradeable_cash = max(0, available_cash - cash_reserve)

        stocks = recommendation.get("stock_recommendations", [])
        sectors = recommendation.get("sector_recommendations", [])

        # Filter stocks worth trading: setup_ready, has price, within price threshold
        setup_stocks = [s for s in stocks if s.get("action") == "setup_ready"]

        # Also consider track stocks if we have few setup_ready
        if len(setup_stocks) < 3:
            track_stocks = [s for s in stocks if s.get("action") == "track"
                           and s.get("entry_price", 0) > 0]
            setup_stocks.extend(track_stocks[:2])

        # Confidence filter: auto-trade stocks with confidence >= 2
        # （LLM推荐通常>=3，fallback推荐=2，都允许交易）
        min_confidence = int(__import__('os').getenv("MIN_TRADE_CONFIDENCE", "2"))
        setup_stocks = [s for s in setup_stocks if s.get("confidence", 0) >= min_confidence]

        trades_executed = []
        orders_skipped = []

        for stock in setup_stocks:
            code = stock.get("code", "")
            name = stock.get("name", "")

            # Skip if already holding this stock
            existing_pos = portfolio.get_position(code, date_str)
            if existing_pos:
                orders_skipped.append({
                    "code": code, "name": name,
                    "reason": "已有持仓"
                })
                continue

            # Skip if confidence below threshold (double-check after pre-filter)
            confidence = stock.get("confidence", 0)
            if confidence < min_confidence:
                orders_skipped.append({
                    "code": code, "name": name,
                    "reason": f"置信度不足({confidence}/5, 需>={min_confidence})"
                })
                continue

            entry_price = self._get_entry_price(stock)

            if not entry_price or entry_price <= 0:
                orders_skipped.append({
                    "code": code, "name": name,
                    "reason": "无法获取有效价格"
                })
                continue

            # 追高过滤：当日涨幅过大不追（涨停≈10%，设9%阈值避免追涨停）
            realtime_info = self._fetch_realtime_info(code)
            if realtime_info:
                change_pct = realtime_info.get("change_pct", 0)
                if change_pct > 9:
                    orders_skipped.append({
                        "code": code, "name": name,
                        "reason": f"当日涨幅过大({change_pct:.1f}%)，追高风险"
                    })
                    continue

            # Calculate position size (dynamic based on account size)
            horizon = stock.get("horizon", "short")
            # P1-4: 仓位上限从 strategy.yaml 单一来源读取（与 risk_manager 一致）
            position_pct = get_position_limit(total_equity, horizon)
            target_amount = total_equity * position_pct

            # Respect available cash
            if target_amount > tradeable_cash:
                target_amount = tradeable_cash

            # Round to 100-share lots
            quantity = int(target_amount / entry_price / 100) * 100
            if quantity < 100:
                # Target amount too small for 100 shares at this price
                # Try minimum lot if we have enough cash
                min_cost = entry_price * 100
                if min_cost <= tradeable_cash and min_cost <= total_equity * position_pct:
                    quantity = 100
                else:
                    orders_skipped.append({
                        "code": code, "name": name,
                        "reason": f"仓位不足: 100股需¥{min_cost:.0f}，超过单票{position_pct*100:.0f}%限额"
                    })
                    continue

            cost = entry_price * quantity

            # Risk check
            risk_mgr = RiskManager(account_data, portfolio.get_positions(date_str))
            order_data = {
                "code": code,
                "name": name,
                "sector": stock.get("sector", ""),
                "action": "buy",
                "quantity": quantity,
                "estimated_price": entry_price,
                "estimated_amount": cost,
                "horizon": horizon,
                "planned_holding_days": stock.get("horizon_days", 3),
            }
            risk_check = risk_mgr.check_order(order_data)
            if not risk_check.get("allowed", False):
                orders_skipped.append({
                    "code": code, "name": name,
                    "reason": f"风控拒绝: {risk_check.get('reason', '未知')}"
                })
                continue

            # Execute trade (A2: 原子交易 — 失败自动回滚)
            trade = executor.execute_order(order_data, entry_price, date_str)
            trade_id = trade.get("trade_id", "")

            try:
                # Update account
                total_cost = trade.get("amount", 0) + trade.get("fees", 0)
                if not account.deduct_cash(total_cost):
                    executor.rollback_trade(trade_id, date_str)  # A2: 回滚成交记录
                    orders_skipped.append({
                        "code": code, "name": name,
                        "reason": "扣款失败（已回滚成交记录）"
                    })
                    continue

                # Add position (recalculate target/stop based on REAL entry price)
                target_pct = stock.get("target_return_pct", 5)  # 默认5%（原8%）
                stop_pct = abs(stock.get("stop_loss_pct", 5))
                # P4: 止损校验——至少5%空间，防止止损过紧或异常
                if stop_pct < 3:
                    stop_pct = 3  # 最低3%止损空间
                real_target = round(entry_price * (1 + target_pct / 100), 2)
                real_stop = round(entry_price * (1 - stop_pct / 100), 2)

                portfolio.add_position({
                    "code": code,
                    "name": name,
                    "sector": stock.get("sector", ""),
                    "quantity": quantity,
                    "avg_cost": entry_price,
                    "current_price": entry_price,
                    "highest_price": entry_price,  # P0-3: 移动止盈跟踪最高价（建仓初始化）
                    "market_value": trade.get("amount", 0),
                    "unrealized_pnl_pct": 0,
                    "horizon": horizon,
                    "period": horizon,  # P0-3: 风控读取的字段，与 horizon 对齐
                    "planned_holding_days": stock.get("horizon_days", 3),
                    "entry_reason": stock.get("reason", ""),
                    "target_price": real_target,
                    "stop_loss_price": real_stop,
                    "entry_date": date_str,
                    "source_recommendation_id": stock.get("recommendation_id", ""),
                    "status": "active",
                }, date_str)

            except Exception as e:
                # A2: add_position 失败 → 回滚扣款 + 成交记录 + 清理半写入持仓
                print(f"  ❌ 买入失败，回滚中: {name}({code}) - {e}")
                try:
                    account.add_cash(total_cost)
                    executor.rollback_trade(trade_id, date_str)
                    portfolio.remove_position(code, date_str)
                except Exception as rollback_err:
                    print(f"  ❌ 回滚也失败! 请手动检查: {rollback_err}")
                orders_skipped.append({
                    "code": code, "name": name,
                    "reason": f"加持仓失败已回滚: {e}"
                })
                continue

            # Update tradeable cash (re-read account to get updated cash)
            account_data = account.get_account()
            available_cash = account_data.get("cash", 0)
            tradeable_cash = max(0, available_cash - cash_reserve)
            trades_executed.append({
                "trade_id": trade.get("trade_id", ""),
                "code": code,
                "name": name,
                "sector": stock.get("sector", ""),
                "action": "buy",
                "price": entry_price,
                "quantity": quantity,
                "amount": cost,
                "fees": trade.get("fees", 0),
                "horizon": horizon,
                "reason": stock.get("reason", ""),
                "target_price": stock.get("target_price", 0),
                "stop_loss_price": stock.get("stop_loss_price", 0),
            })

            print(f"  ✅ 买入 {name}({code}) {quantity}股 @ ¥{entry_price:.2f} = ¥{cost:.0f}")

        # Log the auto-trade session
        result = {
            "date": date_str,
            "executed_at": datetime.now().isoformat(),
            "account_before": {
                "cash": available_cash,
                "total_equity": account_data.get("total_equity", 0),
            },
            "trades_executed": trades_executed,
            "orders_skipped": orders_skipped,
            "summary": {
                "total_trades": len(trades_executed),
                "total_invested": sum(t["amount"] for t in trades_executed),
                "total_fees": sum(t["fees"] for t in trades_executed),
                "stocks_skipped": len(orders_skipped),
            },
        }

        # 交易后更新账户总资产（现金 + 持仓市值）
        try:
            final_positions = portfolio.get_positions(date_str)
            total_market_value = sum(p.get("market_value", p.get("quantity",0) * p.get("avg_cost",0)) for p in final_positions)
            account.update_equity(total_market_value, 0)
        except Exception as e:
            print(f"  ⚠️ 更新总资产失败: {e}")

        self._save_trade_log(result, date_str)
        return result

    def _get_entry_price(self, stock: Dict) -> Optional[float]:
        """Get the best available entry price for a stock.

        Priority: real-time Tencent API > current_price from data > entry_price (LLM estimate)
        Always prefer real market data over LLM predictions for actual trading.
        """
        code = stock.get("code", "")

        # 1. Always try real-time price first (most accurate for trading)
        if code:
            real_price = self._fetch_current_price(code)
            if real_price and real_price > 0:
                return real_price

        # 2. Fallback to current_price from recommendation data
        price = stock.get("current_price")
        if price and price > 0:
            return price

        # 3. Last resort: LLM entry_price estimate (least reliable)
        price = stock.get("entry_price")
        if price and price > 0:
            return price

        timing = stock.get("timing", {})
        price = timing.get("entry_price")
        if price and price > 0:
            return price

        return None

    def update_positions(self, date_str: str = None) -> Dict:
        """Update all positions with current prices (mark-to-market)"""
        if date_str is None:
            date_str = date.today().isoformat()

        account = PaperAccount()
        portfolio = PaperPortfolio()

        positions = portfolio.get_positions(date_str)
        if not positions:
            return {"status": "no_positions", "updated": 0}

        # C1: 批量获取所有持仓的实时价格（替代循环内逐只查询）
        from src.data_collectors.realtime_prices import fetch_realtime_prices
        all_codes = [pos.get("code", "") for pos in positions if pos.get("code")]
        price_map = fetch_realtime_prices(all_codes) if all_codes else {}

        updated = 0
        total_market_value = 0
        total_cost = 0

        for pos in positions:
            code = pos.get("code", "")
            # C1: 从批量结果中取价格，未命中的 fallback 到单只查询
            price_data = price_map.get(code)
            current_price = price_data["price"] if price_data else self._fetch_current_price(code)

            if current_price and current_price > 0:
                quantity = pos.get("quantity", 0)
                avg_cost = pos.get("avg_cost", 0)
                market_value = current_price * quantity
                pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0

                pos["current_price"] = current_price
                pos["market_value"] = market_value
                pos["unrealized_pnl_pct"] = round(pnl_pct, 2)
                # P0-3: 更新移动止盈跟踪的最高价（午间盯市即落盘，避免回撤恒为0）
                ref_price = avg_cost if avg_cost > 0 else pos.get("entry_price", 0)
                prev_high = pos.get("highest_price", ref_price)
                if current_price > prev_high:
                    pos["highest_price"] = current_price
                updated += 1
                total_market_value += market_value
                total_cost += avg_cost * quantity
            else:
                total_market_value += pos.get("market_value", 0)
                total_cost += pos.get("avg_cost", 0) * pos.get("quantity", 0)

        portfolio.save_positions(positions, date_str)

        # Update account
        unrealized_pnl = total_market_value - total_cost
        account.update_equity(total_market_value, unrealized_pnl)

        # Save performance snapshot
        from src.paper_trading.performance import Performance
        perf = Performance()
        perf.calculate_performance(
            account.get_account(), positions,
            Executor().get_trades(date_str), date_str
        )

        result = {
            "status": "ok",
            "date": date_str,
            "updated": updated,
            "total_positions": len(positions),
            "total_market_value": total_market_value,
            "unrealized_pnl": round(unrealized_pnl, 2),
        }
        print(f"  📊 持仓更新: {updated}/{len(positions)}只, 市值¥{total_market_value:.0f}, 浮盈¥{unrealized_pnl:.0f}")
        return result

    def check_sell_signals(self, date_str: str = None) -> List[Dict]:
        """Check existing positions for sell signals

        Checks in order:
        1. Stop-loss trigger
        2. Target price hit
        3. Trailing stop (移动止盈) - via RiskManager
        4. Time stop (时间止损) - via RiskManager
        5. Expiry (planned holding days)
        6. Deep loss (-8%)
        """
        if date_str is None:
            date_str = date.today().isoformat()

        portfolio = PaperPortfolio()
        account = PaperAccount()
        positions = portfolio.get_positions(date_str)

        # Build RiskManager for trailing/time stop checks
        from src.paper_trading.risk_manager import RiskManager
        risk_mgr = RiskManager(account.get_account(), positions)

        sell_signals = []
        dirty = False  # P0-3: 跟踪 highest_price 是否被更新，需落盘
        for pos in positions:
            code = pos.get("code", "")
            current_price = self._fetch_current_price(code) or pos.get("current_price", 0)
            avg_cost = pos.get("avg_cost", 0)

            if current_price <= 0 or avg_cost <= 0:
                continue

            pnl_pct = (current_price - avg_cost) / avg_cost * 100
            target_price = pos.get("target_price", 0)
            stop_loss_price = pos.get("stop_loss_price", 0)

            reason = None

            # Check stop-loss
            if stop_loss_price > 0 and current_price <= stop_loss_price:
                reason = f"触发止损: 现价¥{current_price:.2f} ≤ 止损价¥{stop_loss_price:.2f}"

            # Check target hit
            elif target_price > 0 and current_price >= target_price:
                reason = f"达到目标: 现价¥{current_price:.2f} ≥ 目标价¥{target_price:.2f}"

            # Check trailing stop (移动止盈)
            if reason is None:
                pos_for_check = {
                    "code": code,
                    "name": pos.get("name", ""),
                    "entry_price": avg_cost,
                    "highest_price": pos.get("highest_price", avg_cost),
                    "period": pos.get("period") or pos.get("horizon", "short"),
                }
                trailing_signal = risk_mgr.check_trailing_stop(pos_for_check, current_price)
                # P0-3: 无论是否触发止盈，都要回写并持久化 highest_price（跟踪最高价是持续行为）
                if pos_for_check.get("highest_price", 0) > pos.get("highest_price", 0):
                    pos["highest_price"] = pos_for_check["highest_price"]
                    dirty = True
                if trailing_signal:
                    reason = trailing_signal["reason"]

            # Check time stop (时间止损)
            if reason is None:
                pos_for_time = {
                    "code": code,
                    "name": pos.get("name", ""),
                    "entry_date": pos.get("entry_date"),
                    "entry_price": avg_cost,
                    "current_price": current_price,
                    "period": pos.get("period") or pos.get("horizon", "short"),
                }
                time_signal = risk_mgr.check_time_stop(pos_for_time)
                if time_signal:
                    reason = time_signal["reason"]

            # Check expiry (holding days exceeded)
            if reason is None and pos.get("entry_date") and pos.get("planned_holding_days"):
                try:
                    entry = date.fromisoformat(pos["entry_date"])
                    planned = pos.get("planned_holding_days", 3)
                    if (date.fromisoformat(date_str) - entry).days > planned:
                        reason = f"持仓超期: 已持仓{(date.fromisoformat(date_str) - entry).days}天，计划{planned}天"
                except (ValueError, TypeError):
                    pass

            # Generic stop-loss at -8%
            if reason is None and pnl_pct <= -8:
                reason = f"深度亏损: 浮亏{pnl_pct:.1f}%，建议止损"

            if reason:
                sell_signals.append({
                    "code": code,
                    "name": pos.get("name", ""),
                    "sector": pos.get("sector", ""),
                    "action": "sell",
                    "quantity": pos.get("quantity", 0),
                    "current_price": current_price,
                    "avg_cost": avg_cost,
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": reason,
                })

        # P0-3: 持久化 highest_price 更新，否则移动止盈回撤恒为0（死代码）
        if dirty:
            portfolio.save_positions(positions, date_str)

        return sell_signals

    def execute_sell_orders(self, sell_signals: List[Dict], date_str: str = None) -> List[Dict]:
        """Execute sell orders for triggered signals"""
        raise RuntimeError("旧AutoTrader卖出路径已停用；请使用飞书否决工作流")
        if date_str is None:
            date_str = date.today().isoformat()

        account = PaperAccount()
        portfolio = PaperPortfolio()
        executor = Executor()

        trades = []
        for signal in sell_signals:
            code = signal.get("code", "")
            price = signal.get("current_price", 0)
            quantity = signal.get("quantity", 0)

            if quantity <= 0 or price <= 0:
                continue

            order_data = {
                "code": code,
                "name": signal.get("name", ""),
                "sector": signal.get("sector", ""),
                "action": "sell",
                "quantity": quantity,
                "estimated_price": price,
                "estimated_amount": price * quantity,
                "reason": signal.get("reason", ""),
            }

            trade = executor.execute_order(order_data, price, date_str)
            trade_id = trade.get("trade_id", "")

            # A2: 卖出原子操作 — 失败自动回滚
            try:
                # Update account (add proceeds minus fees)
                proceeds = trade.get("amount", 0) - trade.get("fees", 0)
                account.add_cash(proceeds)

                # Remove position
                portfolio.remove_position(code, date_str)

            except Exception as e:
                # 回滚：退回资金 + 回滚成交记录
                print(f"  ❌ 卖出失败，回滚中: {code} - {e}")
                try:
                    account.deduct_cash(proceeds)  # 退回刚加的钱
                    executor.rollback_trade(trade_id, date_str)
                except Exception as rollback_err:
                    print(f"  ❌ 回滚也失败! 请手动检查: {rollback_err}")
                continue

            trades.append({
                "trade_id": trade.get("trade_id", ""),
                "code": code,
                "name": signal.get("name", ""),
                "action": "sell",
                "price": price,
                "quantity": quantity,
                "amount": trade.get("amount", 0),
                "fees": trade.get("fees", 0),
                "pnl_pct": signal.get("pnl_pct", 0),
                "reason": signal.get("reason", ""),
            })

            print(f"  🔴 卖出 {signal.get('name', '')}({code}) {quantity}股 @ ¥{price:.2f} | {signal.get('reason', '')}")

        return trades

    def _fetch_current_price(self, code: str) -> Optional[float]:
        """Fetch current price (B4: 复用统一接口)"""
        from src.data_collectors.realtime_prices import fetch_single_price
        price = fetch_single_price(code)
        return price if price > 0 else None

    def _fetch_realtime_info(self, code: str) -> Optional[Dict]:
        """Fetch real-time info (B4: 复用统一接口，消除重复代码)"""
        if not code:
            return None
        from src.data_collectors.realtime_prices import fetch_single_info
        return fetch_single_info(code)

    def _save_trade_log(self, result: Dict, date_str: str):
        """Save auto-trade session log"""
        log_file = self.trade_log_dir / f"{date_str}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def get_trade_log(self, date_str: str = None) -> Optional[Dict]:
        """Get auto-trade log for a date"""
        from src.utils.security import validate_date_str
        if date_str is None:
            date_str = date.today().isoformat()
        validate_date_str(date_str, "date_str")  # P0-4: 防路径穿越
        log_file = self.trade_log_dir / f"{date_str}.json"
        if not log_file.exists():
            return None
        try:
            with open(log_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def get_all_trade_logs(self, limit: int = 30) -> List[Dict]:
        """Get all auto-trade logs"""
        logs = []
        for f in sorted(self.trade_log_dir.glob("*.json"), reverse=True):
            if len(logs) >= limit:
                break
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    logs.append(data)
            except (json.JSONDecodeError, IOError):
                continue
        return logs
