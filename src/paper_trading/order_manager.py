# -*- coding: utf-8 -*-
"""订单管理模块"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional


class OrderManager:
    """订单管理"""
    
    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data" / "paper_trading"
        self.orders_dir = self.data_dir / "orders"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
    
    def create_order(self, order: Dict, date_str: str = None) -> Dict:
        """创建订单"""
        if date_str is None:
            date_str = date.today().isoformat()
        
        # 生成订单ID
        order_id = f"ORD-{date_str.replace('-', '')}-{len(self.get_orders(date_str)) + 1:03d}"
        order["order_id"] = order_id
        order["status"] = "pending"
        order["created_at"] = datetime.now().isoformat()
        
        # 保存订单
        orders = self.get_orders(date_str)
        orders.append(order)
        self._save_orders(orders, date_str)
        
        return order
    
    def get_orders(self, date_str: str = None, status: str = None) -> List[Dict]:
        """获取订单"""
        if date_str is None:
            date_str = date.today().isoformat()
        
        orders_file = self.orders_dir / f"{date_str}.json"
        if not orders_file.exists():
            return []
        
        with open(orders_file) as f:
            orders = json.load(f)
        
        if status:
            orders = [o for o in orders if o.get("status") == status]
        
        return orders
    
    def get_order(self, order_id: str, date_str: str = None) -> Optional[Dict]:
        """获取单个订单"""
        orders = self.get_orders(date_str)
        for order in orders:
            if order.get("order_id") == order_id:
                return order
        return None
    
    def update_order_status(self, order_id: str, status: str, date_str: str = None) -> bool:
        """更新订单状态"""
        if date_str is None:
            date_str = date.today().isoformat()
        
        orders = self.get_orders(date_str)
        for order in orders:
            if order.get("order_id") == order_id:
                order["status"] = status
                order["updated_at"] = datetime.now().isoformat()
                self._save_orders(orders, date_str)
                return True
        return False
    
    def _save_orders(self, orders: List[Dict], date_str: str):
        """保存订单"""
        orders_file = self.orders_dir / f"{date_str}.json"
        with open(orders_file, "w") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
