# -*- coding: utf-8 -*-
"""报告推送回执：为定时漏推补偿提供本地、可审计的成功依据。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict

from src.utils.security import validate_date_str, validate_report_type


DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def delivery_path(date_str: str, report_type: str) -> Path:
    validate_date_str(date_str, "date_str")
    validate_report_type(report_type)
    return DATA_DIR / "notifications" / date_str / f"{report_type}.json"


def load_delivery(date_str: str, report_type: str) -> Dict:
    path = delivery_path(date_str, report_type)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def has_successful_delivery(date_str: str, report_type: str) -> bool:
    delivery = load_delivery(date_str, report_type)
    if delivery.get("status") == "success":
        return True
    attempts = delivery.get("attempts", [])
    return isinstance(attempts, list) and any(
        isinstance(attempt, dict) and attempt.get("status") == "success"
        for attempt in attempts
    )


def record_delivery(date_str: str, report_type: str, result: Dict, source: str) -> Path:
    """原子保存最后状态和有限历史；绝不写入密钥或完整卡片正文。"""
    path = delivery_path(date_str, report_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = load_delivery(date_str, report_type)
    attempts = previous.get("attempts", []) if isinstance(previous.get("attempts"), list) else []
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    attempt = {
        "status": result.get("status", "error"),
        "source": source,
        "attempted_at": datetime.now().astimezone().isoformat(),
        "message_id": data.get("message_id"),
        "error": result.get("message") or result.get("reason"),
    }
    attempts = (attempts + [attempt])[-20:]
    payload = {
        "date": date_str,
        "report_type": report_type,
        "status": attempt["status"],
        "source": source,
        "attempted_at": attempt["attempted_at"],
        "message_id": attempt["message_id"],
        "error": attempt["error"],
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
