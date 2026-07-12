# -*- coding: utf-8 -*-
"""模拟成交模块"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional


class Executor:
    """模拟成交"""
    
    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data" / "paper_trading"
        self.trades_dir = self.data_dir / "trades"
        self.trades_dir.mkdir(parents=True, exist_ok=True)
    
    def execute_order(self, order: Dict, price: float, date_str: str = None) -> Dict:
        """执行订单"""
        if date_str is None:
            date_str = date.today().isoformat()
        
        # 计算成交数量（100股整数手）
        quantity = order.get("quantity", 0)
        if quantity % 100 != 0:
            quantity = (quantity // 100) * 100
        
        # 计算成交金额
        amount = price * quantity

        # B5: 交易费用从 cost_model.yaml 读取（原为硬编码）
        from src.utils.cost_calculator import calculate_fees
        fees = calculate_fees(amount, order.get("action", "buy"))
        
        # 创建成交记录
        trade = {
            "trade_id": f"TRD-{date_str.replace('-', '')}-{len(self.get_trades(date_str)) + 1:03d}",
            "order_id": order.get("order_id"),
            "account_id": order.get("account_id", "default"),
            "action": order.get("action"),
            "code": order.get("code"),
            "name": order.get("name"),
            "price": price,
            "quantity": quantity,
            "amount": amount,
            "fees": fees,
            "horizon": order.get("horizon"),
            "reason": order.get("reason"),
            "executed_at": datetime.now().isoformat(),
            "date": date_str,
        }
        
        # 保存成交记录
        trades = self.get_trades(date_str)
        trades.append(trade)
        self._save_trades(trades, date_str)
        
        return trade
    
    def get_trades(self, date_str: str = None) -> List[Dict]:
        """获取成交记录"""
        if date_str is None:
            date_str = date.today().isoformat()

        trades_file = self.trades_dir / f"{date_str}.json"
        if not trades_file.exists():
            return []

        try:
            with open(trades_file, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️ 成交记录文件损坏: {trades_file}, 错误: {e}")
            return []
        except IOError as e:
            print(f"⚠️ 读取成交记录失败: {trades_file}, 错误: {e}")
            return []
    
    def rollback_trade(self, trade_id: str, date_str: str = None) -> bool:
        """回滚成交记录（A2: 交易原子性保障）

        当扣款/加持仓失败时，从成交记录中移除指定的 trade，保持数据一致。
        Returns: True=回滚成功, False=未找到该trade
        """
        if date_str is None:
            date_str = date.today().isoformat()

        trades = self.get_trades(date_str)
        original_len = len(trades)
        trades = [t for t in trades if t.get("trade_id") != trade_id]

        if len(trades) < original_len:
            self._save_trades(trades, date_str)
            print(f"  ⚠️ 已回滚成交记录: {trade_id}")
            return True
        return False

    def _save_trades(self, trades: List[Dict], date_str: str):
        """保存成交记录（原子操作）"""
        import os

        trades_file = self.trades_dir / f"{date_str}.json"
        temp_file = trades_file.with_suffix('.tmp')

        try:
            # 先写入临时文件
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(trades, f, ensure_ascii=False, indent=2)

            # 原子替换原文件
            os.replace(str(temp_file), str(trades_file))
        except IOError as e:
            print(f"⚠️ 保存成交记录失败: {trades_file}, 错误: {e}")
            # 清理临时文件
            if temp_file.exists():
                temp_file.unlink()
            raise
