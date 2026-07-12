# -*- coding: utf-8 -*-
"""飞书报告模块"""

from typing import Dict, List


def format_plan_report(orders: List[Dict], account: Dict, date_str: str) -> str:
    """格式化交易计划报告"""
    
    lines = []
    lines.append(f"📌 模拟操盘计划 - {date_str}")
    lines.append("")
    lines.append(f"账户总资产: {account.get('total_equity', 0):,.0f}")
    lines.append(f"可用现金: {account.get('cash', 0):,.0f}")
    lines.append(f"当前持仓: {account.get('market_value', 0):,.0f}")
    lines.append("")
    
    # 买入计划
    buy_orders = [o for o in orders if o.get("action") == "buy"]
    if buy_orders:
        lines.append("拟买入:")
        for i, order in enumerate(buy_orders, 1):
            lines.append(f"{i}. {order.get('name')} {order.get('code')}")
            lines.append(f"   类型: {order.get('horizon')}")
            lines.append(f"   计划持有: {order.get('planned_holding_days')}个交易日")
            lines.append(f"   拟买入: {order.get('quantity')}股")
            lines.append(f"   预计金额: {order.get('estimated_amount', 0):,.0f}")
            lines.append(f"   理由: {order.get('reason')}")
            lines.append(f"   风险: {order.get('risk_note')}")
            lines.append("")
    else:
        lines.append("拟买入: 无")
        lines.append("")
    
    # 卖出计划
    sell_orders = [o for o in orders if o.get("action") == "sell"]
    if sell_orders:
        lines.append("拟卖出:")
        for i, order in enumerate(sell_orders, 1):
            lines.append(f"{i}. {order.get('name')} {order.get('code')}")
            lines.append(f"   数量: {order.get('quantity')}股")
            lines.append(f"   理由: {order.get('reason')}")
            lines.append("")
    else:
        lines.append("拟卖出: 无")
        lines.append("")
    
    lines.append("是否确认执行模拟交易？")
    lines.append("回复：确认 / 拒绝 / 修改")
    
    return "\n".join(lines)


def format_execution_report(trades: List[Dict], date_str: str) -> str:
    """格式化成交报告"""
    
    lines = []
    lines.append(f"✅ 模拟成交结果 - {date_str}")
    lines.append("")
    
    if not trades:
        lines.append("今日无成交")
        return "\n".join(lines)
    
    for trade in trades:
        action = "买入" if trade.get("action") == "buy" else "卖出"
        lines.append(f"{action}: {trade.get('name')} {trade.get('code')}")
        lines.append(f"  价格: {trade.get('price', 0):.2f}")
        lines.append(f"  数量: {trade.get('quantity')}股")
        lines.append(f"  金额: {trade.get('amount', 0):,.0f}")
        lines.append(f"  费用: {trade.get('fees', 0):.2f}")
        lines.append("")
    
    return "\n".join(lines)


def format_performance_report(performance: Dict, positions: List[Dict], date_str: str) -> str:
    """格式化收益报告"""
    
    lines = []
    lines.append(f"📊 模拟账户复盘 - {date_str}")
    lines.append("")
    lines.append(f"账户总资产: {performance.get('total_equity', 0):,.0f}")
    lines.append(f"可用现金: {performance.get('cash', 0):,.0f}")
    lines.append(f"持仓市值: {performance.get('market_value', 0):,.0f}")
    lines.append(f"总收益: {performance.get('total_return', 0):,.0f} ({performance.get('total_return_pct', 0):.2f}%)")
    lines.append(f"未实现盈亏: {performance.get('unrealized_pnl', 0):,.0f}")
    lines.append(f"已实现盈亏: {performance.get('realized_pnl', 0):,.0f}")
    lines.append(f"胜率: {performance.get('win_rate', 0):.1f}%")
    lines.append("")
    
    if positions:
        lines.append("当前持仓:")
        for pos in positions:
            pnl_icon = "📈" if pos.get("unrealized_pnl_pct", 0) >= 0 else "📉"
            lines.append(f"  {pnl_icon} {pos.get('name')} {pos.get('code')}")
            lines.append(f"     成本: {pos.get('avg_cost', 0):.2f} | 现价: {pos.get('current_price', 0):.2f}")
            lines.append(f"     盈亏: {pos.get('unrealized_pnl_pct', 0):.2f}%")
            lines.append("")
    
    return "\n".join(lines)
