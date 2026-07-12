# -*- coding: utf-8 -*-
"""B6: 核心交易逻辑回归测试

覆盖 risk_manager 的移动止盈/时间止损，以及 executor 的回滚机制。
确保 P0-3 修复和 A2 原子性修复不被回归。
"""

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import sys

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.paper_trading.risk_manager import RiskManager
from src.paper_trading.executor import Executor


class TestTrailingStop:
    """移动止盈测试（保护 P0-3 修复）"""

    def _make_position(self, entry_price=10.0, highest_price=None, period="short", current_price=None):
        if highest_price is None:
            highest_price = entry_price
        if current_price is None:
            current_price = entry_price
        return {
            "code": "600036",
            "name": "招商银行",
            "entry_price": entry_price,  # risk_manager 读这个字段
            "avg_cost": entry_price,
            "current_price": current_price,
            "highest_price": highest_price,
            "horizon": period,
            "period": period,
            "entry_date": date.today().isoformat(),
        }

    def _make_risk_mgr(self):
        """创建测试用 RiskManager（空账户+空持仓）"""
        return RiskManager({"cash": 100000, "total_equity": 100000}, [])

    def test_trailing_stop_triggers_on_drawdown(self):
        """最高价回撤超过阈值时应触发止盈"""
        pos = self._make_position(entry_price=10.0, highest_price=12.0)
        # 12.0 → 10.8 (回撤10%)，trailing_pct 默认 5%
        result = self._make_risk_mgr().check_trailing_stop(pos, current_price=10.8)
        assert result is not None, "回撤10%应触发移动止盈"
        assert "reason" in result

    def test_trailing_stop_not_triggered_below_threshold(self):
        """回撤未超过阈值时不应触发（short: trail_pct=2.0%）"""
        pos = self._make_position(entry_price=10.0, highest_price=12.0)
        # 12.0 → 11.85 (回撤1.25%) < trail_pct 2.0%
        result = self._make_risk_mgr().check_trailing_stop(pos, current_price=11.85)
        assert result is None, "回撤1.25%不应触发移动止盈（阈值2.0%）"

    def test_trailing_stop_not_triggered_without_profit(self):
        """无盈利时不触发移动止盈"""
        pos = self._make_position(entry_price=10.0, highest_price=10.0)
        result = self._make_risk_mgr().check_trailing_stop(pos, current_price=10.0)
        assert result is None, "无盈利不应触发移动止盈"

    def test_trailing_stop_uses_highest_not_current(self):
        """回撤计算应基于 highest_price 而非 current_price"""
        pos = self._make_position(entry_price=10.0, highest_price=15.0)
        # 15.0 → 13.5 (回撤10%)，即使 13.5 > entry_price(10)
        result = self._make_risk_mgr().check_trailing_stop(pos, current_price=13.5)
        assert result is not None, "应基于 highest_price 计算回撤"


class TestTimeStop:
    """时间止损测试"""

    def test_time_stop_triggers_on_long_holding(self):
        """持仓超过计划天数且亏损时应触发时间止损"""
        old_date = (date.today() - timedelta(days=30)).isoformat()
        pos = {
            "code": "600036",
            "name": "招商银行",
            "entry_price": 10.0,  # risk_manager 读这个字段
            "current_price": 9.0,  # 亏损10%
            "entry_date": old_date,
            "period": "short",
            "horizon": "short",
            "planned_holding_days": 3,
        }
        result = RiskManager({"cash": 100000, "total_equity": 100000}, []).check_time_stop(pos)
        assert result is not None, "持仓30天+亏损应触发时间止损"

    def test_time_stop_not_triggered_recent_entry(self):
        """刚建仓的持仓不触发时间止损"""
        pos = {
            "code": "600036",
            "avg_cost": 10.0,
            "entry_date": date.today().isoformat(),
            "period": "short",
            "horizon": "short",
            "planned_holding_days": 3,
        }
        result = RiskManager({"cash": 100000, "total_equity": 100000}, []).check_time_stop(pos)
        assert result is None, "刚建仓不应触发时间止损"


class TestExecutorRollback:
    """A2: 成交记录回滚测试"""

    def test_rollback_trade_removes_record(self):
        """rollback_trade 应从成交记录中移除指定 trade"""
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = Executor(Path(tmpdir))
            date_str = date.today().isoformat()

            # 创建一笔交易
            order = {"code": "600036", "name": "招商银行", "action": "buy", "quantity": 100}
            trade = executor.execute_order(order, 10.0, date_str)
            trade_id = trade["trade_id"]

            # 确认交易存在
            trades = executor.get_trades(date_str)
            assert len(trades) == 1

            # 回滚
            success = executor.rollback_trade(trade_id, date_str)
            assert success is True, "回滚应成功"

            # 确认交易已移除
            trades = executor.get_trades(date_str)
            assert len(trades) == 0, "回滚后交易记录应为空"

    def test_rollback_nonexistent_trade_returns_false(self):
        """回滚不存在的 trade_id 应返回 False"""
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = Executor(Path(tmpdir))
            date_str = date.today().isoformat()
            success = executor.rollback_trade("TRD-NONEXISTENT-001", date_str)
            assert success is False, "回滚不存在的trade应返回False"


class TestCostCalculator:
    """B5: 费率计算测试"""

    def test_buy_fees_include_commission_and_transfer(self):
        """买入费用 = 佣金(最低5元) + 过户费"""
        from src.utils.cost_calculator import calculate_fees
        # 10000元买入：佣金 max(5, 10000*0.00015)=5, 过户费 10000*0.00005=0.5
        fees = calculate_fees(10000, "buy")
        assert fees > 0, "买入费用应大于0"
        assert fees >= 5, "佣金最低5元"

    def test_sell_fees_include_stamp_tax(self):
        """卖出费用 = 佣金 + 印花税 + 过户费"""
        from src.utils.cost_calculator import calculate_fees
        buy_fees = calculate_fees(10000, "buy")
        sell_fees = calculate_fees(10000, "sell")
        assert sell_fees > buy_fees, "卖出费用应高于买入（含印花税）"

    def test_etf_no_stamp_tax(self):
        """ETF 卖出免印花税"""
        from src.utils.cost_calculator import calculate_fees
        stock_sell = calculate_fees(10000, "sell", "stock")
        etf_sell = calculate_fees(10000, "sell", "etf")
        assert etf_sell < stock_sell, "ETF卖出费用应低于股票（免印花税）"


class TestPositionLimits:
    """B7: 初始资金配置测试"""

    def test_get_initial_cash_returns_value(self):
        """get_initial_cash 应返回 strategy.yaml 中的值"""
        from src.strategy.position_limits import get_initial_cash
        cash = get_initial_cash()
        assert cash > 0, "初始资金应大于0"
        assert cash == 4000, "默认模拟资金应为4000"

    def test_tier_boundary_is_inclusive(self):
        """恰好位于分档上限时仍属于当前档。"""
        from src.strategy.position_limits import get_position_limit
        assert get_position_limit(10000, "short") == 0.30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
