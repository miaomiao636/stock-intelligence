# -*- coding: utf-8 -*-
"""飞书卡片动作的共享业务处理。"""

from __future__ import annotations

from typing import Callable, Dict, Optional


ALLOWED_TRADE_ACTIONS = {"confirm", "veto", "pause_day"}


def process_card_action(
    action: Dict,
    *,
    service=None,
    execute_confirm: Optional[Callable[[], Dict]] = None,
) -> Dict:
    """处理已完成传输层鉴权和解析的卡片动作。"""
    action_name = action.get("action")
    if action_name == "ping":
        return {"toast": {"type": "success", "content": "飞书回调链路正常"}}
    if not action.get("order_id"):
        return {"toast": {"type": "warning", "content": "回调已收到，但未解析到订单号"}}
    if action_name not in ALLOWED_TRADE_ACTIONS:
        return {
            "toast": {
                "type": "warning",
                "content": f"回调已收到，未知动作: {action_name or 'unknown'}",
            }
        }

    if service is None:
        from src.paper_trading.trading_service import TradingService

        service = TradingService()
    try:
        order = service.record_decision(
            action["order_id"],
            action_name,
            actor=action.get("actor") or "feishu_user",
            event_id=action.get("event_id"),
        )
    except ValueError as exc:
        return {
            "toast": {"type": "warning", "content": f"操作未执行：{exc}"},
            "execution": None,
        }

    execution = None
    if action_name == "confirm":
        if execute_confirm is None:
            from src.paper_trading.workflow import PaperTradingWorkflow

            execute_confirm = lambda: PaperTradingWorkflow().execute_due_orders()
        execution = execute_confirm()
    return {
        "toast": {"type": "success", "content": f"订单已更新为 {order['status']}"},
        "execution": execution,
    }
