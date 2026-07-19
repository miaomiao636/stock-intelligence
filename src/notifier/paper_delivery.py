# -*- coding: utf-8 -*-
"""09:35模拟交易阶段回执：防漏跑、防重复、保留有限审计记录。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict

from src.utils.security import validate_date_str


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def delivery_path(date_str: str) -> Path:
    validate_date_str(date_str, "date_str")
    return DATA_DIR / "workflow" / date_str / "paper_open.json"


def load_delivery(date_str: str) -> Dict:
    path = delivery_path(date_str)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def has_successful_delivery(date_str: str) -> bool:
    delivery = load_delivery(date_str)
    if delivery.get("status") == "success":
        return True
    attempts = delivery.get("attempts", [])
    return isinstance(attempts, list) and any(
        isinstance(attempt, dict) and attempt.get("status") == "success"
        for attempt in attempts
    )


def record_delivery(
    date_str: str,
    workflow_result: Dict,
    notification_result: Dict,
    source: str,
) -> Path:
    """原子保存阶段结果；不保存卡片正文、密钥或订单完整内容。"""
    path = delivery_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = load_delivery(date_str)
    attempts = previous.get("attempts", []) if isinstance(previous.get("attempts"), list) else []
    data = notification_result.get("data") if isinstance(notification_result.get("data"), dict) else {}
    orders = workflow_result.get("orders") if isinstance(workflow_result.get("orders"), list) else []
    rejected = workflow_result.get("rejected") if isinstance(workflow_result.get("rejected"), list) else []
    attempt = {
        "status": notification_result.get("status", "error"),
        "workflow_status": workflow_result.get("status", "error"),
        "source": source,
        "attempted_at": datetime.now().astimezone().isoformat(),
        "message_id": data.get("message_id"),
        "order_count": len(orders),
        "rejected_count": len(rejected),
        "reason": workflow_result.get("reason"),
        "error": notification_result.get("message") or notification_result.get("reason"),
    }
    attempts = (attempts + [attempt])[-20:]
    payload = {
        "date": date_str,
        **attempt,
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path
