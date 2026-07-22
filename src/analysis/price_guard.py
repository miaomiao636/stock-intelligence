# -*- coding: utf-8 -*-
"""用真实行情约束模型价格，防止旧价格或臆造价格进入模拟交易。"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _price_range(price: float) -> str:
    return f"{price * 0.99:.2f}-{price * 1.01:.2f}元区间"


def reconcile_recommendation_prices(
    recommendations: List[Dict],
    quotes: Dict[str, Dict],
    eligible_codes: Iterable[str] = (),
    max_price: float = 500.0,
) -> Dict:
    """覆盖模型绝对价格，并把无法验证的信号强制降为不可交易。

    相对收益/止损参数只作为有限范围内的策略意图使用；所有绝对价格都由
    行情重新计算。返回结构可直接写入 ``source_status`` 和报告诊断信息。
    """
    allowed: Set[str] = {str(code) for code in eligible_codes if code}
    verified = 0
    blocked = 0
    corrected = 0
    details = []

    for stock in recommendations:
        code = str(stock.get("code") or "")
        quote = quotes.get(code) if isinstance(quotes, dict) else None
        live_price = _number((quote or {}).get("price"))
        previous_model_price = _number(stock.get("current_price"))
        timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
        stock["timing"] = timing

        if live_price <= 0:
            stock["action"] = "watch"
            stock["trade_eligible"] = False
            stock["degraded_reason"] = "真实行情校验失败，禁止自动交易"
            stock["price_validation"] = {"verified": False, "reason": "quote_missing"}
            for field in ("current_price", "entry_price", "target_price", "stop_loss_price"):
                stock[field] = None
            timing.update({
                "entry_price": None,
                "entry_price_range": None,
                "target_observation_price": None,
                "stop_loss_price": None,
                "entry_rule": "observe_only",
            })
            blocked += 1
            details.append({"code": code, "status": "blocked", "reason": "quote_missing"})
            continue

        verified += 1
        if previous_model_price <= 0 or abs(previous_model_price / live_price - 1) > 0.005:
            corrected += 1

        old_entry = _number(stock.get("entry_price"))
        discount_pct = (
            (1 - old_entry / previous_model_price) * 100
            if previous_model_price > 0 and old_entry > 0
            else 3.0
        )
        discount_pct = _clamp(discount_pct, 2.0, 5.0)
        stop_pct = _clamp(abs(_number(stock.get("stop_loss_pct"), 3.0)), 2.0, 5.0)
        requested_target = _clamp(_number(stock.get("target_return_pct"), 5.0), 3.0, 10.0)
        target_pct = max(requested_target, stop_pct * 2)

        entry_price = round(live_price * (1 - discount_pct / 100), 2)
        target_price = round(entry_price * (1 + target_pct / 100), 2)
        stop_price = round(entry_price * (1 - stop_pct / 100), 2)
        stock.update({
            "current_price": round(live_price, 3),
            "latest_price": round(live_price, 3),
            "entry_price": entry_price,
            "target_price": target_price,
            "stop_loss_price": stop_price,
            "target_return_pct": round(target_pct, 2),
            "stop_loss_pct": round(-stop_pct, 2),
            "latest_quote_time": quote.get("quote_time"),
            "latest_quote_source": quote.get("source"),
        })
        timing.update({
            "entry_price": entry_price,
            "entry_price_range": _price_range(entry_price),
            "target_observation_price": target_price,
            "stop_loss_price": stop_price,
        })

        reasons = []
        if not allowed:
            reasons.append("candidate_universe_unavailable")
        elif code not in allowed:
            reasons.append("not_in_candidate_universe")
        if max_price > 0 and live_price > max_price:
            reasons.append("over_max_price")

        verified_for_trade = not reasons
        stock["trade_eligible"] = bool(stock.get("action") == "setup_ready" and verified_for_trade)
        stock["price_validation"] = {
            "verified": True,
            "source": quote.get("source"),
            "quote_time": quote.get("quote_time"),
            "model_price": previous_model_price or None,
            "corrected": previous_model_price <= 0 or abs(previous_model_price / live_price - 1) > 0.005,
            "trade_eligible": stock["trade_eligible"],
            "restrictions": reasons,
        }
        if reasons and stock.get("action") == "setup_ready":
            stock["action"] = "track"
            stock["trade_eligible"] = False
            stock["track_trigger"] = (
                "真实行情已校正；该标的未通过全市场候选资格或超过价格上限，"
                "重新进入合格候选池后再评估。"
            )
            blocked += 1
        details.append({"code": code, "status": "verified", "restrictions": reasons})

    total = len(recommendations)
    status = f"ok_{verified}" if total > 0 and verified == total else (
        f"partial_{verified}_of_{total}" if verified else "error"
    )
    return {
        "status": status,
        "total": total,
        "verified": verified,
        "corrected": corrected,
        "blocked": blocked,
        "details": details,
    }
