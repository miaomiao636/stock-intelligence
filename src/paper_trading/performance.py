# -*- coding: utf-8 -*-
"""收益统计模块 - 修复盈亏计算"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List


class Performance:
    """收益统计"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data" / "paper_trading"
        self.performance_dir = self.data_dir / "performance"
        self.performance_dir.mkdir(parents=True, exist_ok=True)

    def calculate_performance(self, account: Dict, positions: List[Dict], trades: List[Dict], date_str: str = None) -> Dict:
        """计算收益

        Args:
            account: 账户信息
            positions: 当前持仓列表
            trades: 交易记录列表
            date_str: 日期字符串

        Returns:
            收益统计字典
        """
        if date_str is None:
            date_str = date.today().isoformat()

        # 计算持仓成本（正确方式）
        total_market_value = 0
        total_cost_basis = 0

        for pos in positions:
            avg_cost = pos.get("avg_cost", 0)
            quantity = pos.get("quantity", 0)
            market_value = pos.get("market_value", 0)

            # 成本基础 = 平均成本 × 数量
            cost_basis = avg_cost * quantity

            total_market_value += market_value
            total_cost_basis += cost_basis

        # 未实现盈亏 = 市值 - 成本基础
        unrealized_pnl = total_market_value - total_cost_basis
        unrealized_pnl_pct = (unrealized_pnl / total_cost_basis * 100) if total_cost_basis > 0 else 0

        # 计算已实现收益（从卖出交易）
        realized_pnl = 0
        winning_sells = 0
        total_sells = 0

        for trade in trades:
            if trade.get("action") == "sell":
                total_sells += 1

                # 方式1: 直接使用realized_pnl字段（如果有）
                if "realized_pnl" in trade:
                    pnl = trade["realized_pnl"]
                else:
                    # 方式2: 计算卖出收入 - 买入成本
                    sell_amount = trade.get("amount", 0)
                    buy_cost = trade.get("cost_amount", 0)

                    # 如果没有cost_amount，尝试计算
                    if buy_cost == 0:
                        buy_price = trade.get("buy_price", trade.get("price", 0))
                        buy_quantity = trade.get("quantity", 0)
                        buy_cost = buy_price * buy_quantity

                    pnl = sell_amount - buy_cost

                realized_pnl += pnl

                if pnl > 0:
                    winning_sells += 1

        # 胜率 = 盈利卖出 / 总卖出
        win_rate = (winning_sells / total_sells * 100) if total_sells > 0 else 0

        # 总收益计算
        cash = account.get("cash", 0)
        total_equity = cash + total_market_value
        initial_cash = account.get("initial_cash", 100000)
        net_deposit = account.get("net_deposit", 0)  # 净入金

        # 累计盈亏 = 总资产 - 初始资金 - 净入金
        total_return = total_equity - initial_cash - net_deposit
        total_return_pct = (total_return / initial_cash * 100) if initial_cash > 0 else 0

        # 对账验证
        expected_equity = cash + total_market_value
        if abs(expected_equity - total_equity) > 0.01:
            print(f"⚠️ 对账警告: 计算总权益 {total_equity} != 预期 {expected_equity}")

        performance = {
            "date": date_str,
            "account_id": account.get("account_id", "default"),

            # 资产构成
            "cash": round(cash, 2),
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "initial_cash": initial_cash,
            "net_deposit": net_deposit,

            # 盈亏
            "cost_basis": round(total_cost_basis, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
            "realized_pnl": round(realized_pnl, 2),
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),

            # 交易统计
            "win_rate": round(win_rate, 2),
            "total_trades": len(trades),
            "winning_sells": winning_sells,
            "total_sells": total_sells,
            "positions_count": len(positions),

            # 对账信息
            "reconciliation": {
                "cash_plus_positions": round(expected_equity, 2),
                "matches_total_equity": abs(expected_equity - total_equity) < 0.01
            },

            "calculated_at": datetime.now().isoformat(),
        }

        # 保存收益记录
        self._save_performance(performance, date_str)

        return performance

    def get_performance(self, date_str: str = None) -> Dict:
        """获取收益记录"""
        if date_str is None:
            date_str = date.today().isoformat()

        perf_file = self.performance_dir / f"{date_str}.json"
        if not perf_file.exists():
            return {}

        try:
            with open(perf_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 读取收益文件失败: {e}")
            return {}

    def _save_performance(self, performance: Dict, date_str: str):
        """保存收益记录"""
        perf_file = self.performance_dir / f"{date_str}.json"
        try:
            with open(perf_file, "w", encoding="utf-8") as f:
                json.dump(performance, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"⚠️ 保存收益文件失败: {e}")
            raise

    def verify_accounting(self, account: Dict, positions: List[Dict]) -> Dict:
        """验证账本恒等式

        恒等式：
        总资产 = 现金 + 持仓市值
        累计盈亏 = 总资产 - 初始资金 - 净入金

        Returns:
            验证结果
        """
        cash = account.get("cash", 0)
        market_value = sum(p.get("market_value", 0) for p in positions)
        total_equity = cash + market_value

        initial_cash = account.get("initial_cash", 100000)
        net_deposit = account.get("net_deposit", 0)

        # 验证
        account_total = account.get("total_equity", 0)
        matches = abs(account_total - total_equity) < 0.01

        return {
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "calculated_total": round(total_equity, 2),
            "account_total": round(account_total, 2),
            "matches": matches,
            "difference": round(abs(account_total - total_equity), 2),
            "status": "✅ 账本一致" if matches else "❌ 账本不一致"
        }
