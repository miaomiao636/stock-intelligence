# -*- coding: utf-8 -*-
"""交易计划模块"""

from datetime import datetime, date
from pathlib import Path
from typing import Dict, List

from src.paper_trading.account import PaperAccount
from src.paper_trading.portfolio import PaperPortfolio
from src.paper_trading.order_manager import OrderManager
from src.paper_trading.risk_manager import RiskManager


class Planner:
    """交易计划"""
    
    def __init__(self):
        self.data_dir = Path(__file__).parent.parent.parent / "data" / "paper_trading"
        self.account = PaperAccount(self.data_dir)
        self.portfolio = PaperPortfolio(self.data_dir)
        self.order_manager = OrderManager(self.data_dir)
    
    def generate_plan(self, recommendation: Dict, date_str: str = None) -> List[Dict]:
        """根据推荐生成交易计划"""
        if date_str is None:
            date_str = date.today().isoformat()
        
        # 获取当前账户和持仓
        account_data = self.account.get_account()
        positions = self.portfolio.get_positions(date_str)
        
        # 创建风控管理器
        risk_manager = RiskManager(account_data, positions)
        
        orders = []
        
        # 处理个股推荐
        for stock in recommendation.get("stock_recommendations", []):
            action = stock.get("action")
            
            # 只有setup_ready才生成买入计划
            if action == "setup_ready":
                order = self._create_buy_order(stock, account_data, risk_manager)
                if order:
                    orders.append(self.order_manager.create_order(order, date_str))
            
            # hold/track/watch 只记录，不生成订单
            # avoid 如有持仓则生成卖出计划
            elif action == "avoid":
                position = self.portfolio.get_position(stock.get("code"), date_str)
                if position:
                    order = self._create_sell_order(stock, position)
                    orders.append(self.order_manager.create_order(order, date_str))
        
        return orders
    
    def _create_buy_order(self, stock: Dict, account: Dict, risk_manager: RiskManager) -> Dict:
        """创建买入订单"""
        
        # 计算建议买入金额（总资产的5%-10%）
        total_equity = account.get("total_equity", 100000)
        suggested_pct = 0.05 if stock.get("horizon") == "short" else 0.08
        suggested_amount = total_equity * suggested_pct
        
        # 估算价格（优先使用entry_price，其次current_price）
        estimated_price = stock.get("entry_price")
        if not estimated_price or estimated_price <= 0:
            estimated_price = stock.get("timing", {}).get("entry_price")
        if not estimated_price or estimated_price <= 0:
            estimated_price = stock.get("current_price")
        if not estimated_price or estimated_price <= 0:
            # 尝试从市场数据获取
            try:
                from src.data_collectors.market_data import get_stock_data
                stock_data = get_stock_data(stock.get("code"))
                if "error" not in stock_data:
                    estimated_price = stock_data.get("close", 0)
            except Exception as e:
                print(f"  [WARN] 获取 {stock.get('code')} 实时价格失败: {e}")

        # If still no price, skip this stock
        if not estimated_price or estimated_price <= 0:
            print(f"  [WARN] 无法获取 {stock.get('code')} ({stock.get('name')}) 价格，跳过")
            return None
        
        # 计算数量（100股整数手）
        quantity = int(suggested_amount / estimated_price / 100) * 100
        if quantity < 100:
            return None
        
        estimated_amount = estimated_price * quantity
        
        # 风控检查
        order_data = {
            "code": stock.get("code"),
            "name": stock.get("name"),
            "sector": stock.get("sector"),
            "action": "buy",
            "quantity": quantity,
            "estimated_price": estimated_price,
            "estimated_amount": estimated_amount,
            "horizon": stock.get("horizon"),
            "planned_holding_days": stock.get("horizon_days", 3),
            "reason": stock.get("reason"),
            "risk_note": f"单票仓位{estimated_amount/account.get('total_equity', 1)*100:.1f}%",
            "requires_user_approval": False,
            "account_id": "default",
            "source_recommendation_id": stock.get("recommendation_id"),
        }
        
        risk_check = risk_manager.check_order(order_data)
        if not risk_check["allowed"]:
            order_data["risk_note"] = risk_check["reason"]
            return None
        
        return order_data
    
    def _create_sell_order(self, stock: Dict, position: Dict) -> Dict:
        """创建卖出订单"""
        return {
            "code": stock.get("code"),
            "name": stock.get("name"),
            "sector": stock.get("sector"),
            "action": "sell",
            "quantity": position.get("quantity", 0),
            "estimated_price": position.get("current_price", 0),
            "estimated_amount": position.get("market_value", 0),
            "horizon": position.get("horizon"),
            "reason": f"推荐avoid，建议卖出",
            "requires_user_approval": False,
            "account_id": "default",
            "source_recommendation_id": stock.get("recommendation_id"),
        }
