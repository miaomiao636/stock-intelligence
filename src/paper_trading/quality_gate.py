# -*- coding: utf-8 -*-
"""模拟交易的入场触发与成本后盈亏比质量门。"""

from __future__ import annotations

import math
from typing import Dict, Optional

from src.utils.cost_calculator import calculate_trade_costs


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _rate(value, default: float) -> float:
    """兼容 ``3``（3%）和 ``0.03`` 两种比例写法。"""
    result = abs(_number(value, default))
    return result / 100 if result > 1 else result


def entry_trigger_error(stock: Dict, market_price: float, config: Dict) -> Optional[str]:
    """校验 09:35 行情是否真正进入推荐的计划进场区间。"""
    timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
    planned_entry = _number(stock.get("entry_price") or timing.get("entry_price"))
    if planned_entry <= 0:
        return "推荐缺少有效计划进场价，禁止自动成交"

    tolerance_above = max(0.0, _number(config.get("entry_trigger_tolerance_pct"), 0.005))
    max_gap_below = max(0.0, _number(config.get("entry_max_gap_below_pct"), 0.01))
    lower = planned_entry * (1 - max_gap_below)
    upper = planned_entry * (1 + tolerance_above)
    if market_price > upper:
        return (
            f"最新价¥{market_price:.2f}尚未进入计划价¥{planned_entry:.2f}"
            f"（上限¥{upper:.2f}）"
        )
    if market_price < lower:
        return (
            f"最新价¥{market_price:.2f}低于计划进场安全区间¥{lower:.2f}，"
            "需重新评估而非自动抄底"
        )
    return None


def resolve_stop_rate(stock: Dict, config: Dict) -> float:
    """从推荐相对参数解析止损比例，并限制在项目允许范围内。"""
    timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
    planned_entry = _number(stock.get("entry_price") or timing.get("entry_price"))
    planned_stop = _number(stock.get("stop_loss_price") or timing.get("stop_loss_price"))
    inferred = (planned_entry - planned_stop) / planned_entry if planned_entry > planned_stop > 0 else 0.03
    rate = _rate(stock.get("stop_loss_pct"), inferred)
    minimum = max(0.001, _number(config.get("min_stop_loss_pct"), 0.02))
    maximum = max(minimum, _number(config.get("max_stop_loss_pct"), 0.05))
    return max(minimum, min(maximum, rate))


def calculate_net_reward_risk(
    *,
    entry_market_price: float,
    target_market_price: float,
    stop_market_price: float,
    quantity: int,
    instrument_type: str,
) -> Dict[str, float]:
    """按统一费用与滑点模型估算止盈收益、止损风险和盈亏比。"""
    if not (entry_market_price > stop_market_price > 0 and target_market_price > entry_market_price):
        return {"gross_ratio": 0.0, "net_ratio": 0.0, "net_reward": 0.0, "net_risk": 0.0}
    if quantity <= 0:
        return {"gross_ratio": 0.0, "net_ratio": 0.0, "net_reward": 0.0, "net_risk": 0.0}

    entry_cost = calculate_trade_costs(entry_market_price * quantity, "buy", instrument_type)
    target_cost = calculate_trade_costs(target_market_price * quantity, "sell", instrument_type)
    stop_cost = calculate_trade_costs(stop_market_price * quantity, "sell", instrument_type)
    entry_fill = entry_market_price * entry_cost["effective_price_factor"]
    target_fill = target_market_price * target_cost["effective_price_factor"]
    stop_fill = stop_market_price * stop_cost["effective_price_factor"]

    gross_reward = (target_market_price - entry_market_price) * quantity
    gross_risk = (entry_market_price - stop_market_price) * quantity
    net_reward = (target_fill - entry_fill) * quantity - entry_cost["fees"] - target_cost["fees"]
    net_risk = (entry_fill - stop_fill) * quantity + entry_cost["fees"] + stop_cost["fees"]
    return {
        "gross_ratio": gross_reward / gross_risk if gross_risk > 0 else 0.0,
        "net_ratio": net_reward / net_risk if net_risk > 0 else 0.0,
        "net_reward": net_reward,
        "net_risk": net_risk,
    }


def build_execution_levels(
    *,
    stock: Dict,
    market_price: float,
    quantity: int,
    instrument_type: str,
    config: Dict,
) -> Dict:
    """检验原始研究目标，绝不抬高目标价来凑足盈亏比。"""
    stop_rate = resolve_stop_rate(stock, config)
    stop_price = round(market_price * (1 - stop_rate), 3)
    timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
    planned_entry = _number(stock.get("entry_price") or timing.get("entry_price"))
    target_price = _number(stock.get("target_price") or timing.get("target_price"))
    if not target_price and stock.get("target_return_pct") is not None and planned_entry > 0:
        if _number(stock["target_return_pct"]) <= 0:
            return {"eligible": False, "reason": "目标收益必须是有效正值"}
        target_price = planned_entry * (1 + _rate(stock["target_return_pct"], 0))
    if target_price <= market_price or market_price <= 0:
        return {"eligible": False, "reason": "缺少有效研究目标价或目标已低于当前价", "target_price": target_price}
    min_gross_ratio = max(0.0, _number(config.get("min_gross_reward_risk_ratio"), 2.0))
    min_net_ratio = max(0.0, _number(config.get("min_net_reward_risk_ratio"), 1.5))
    max_target_rate = _number(config.get("max_target_return_pct"), 0.15)
    metrics = calculate_net_reward_risk(
        entry_market_price=market_price,
        target_market_price=target_price,
        stop_market_price=stop_price,
        quantity=quantity,
        instrument_type=instrument_type,
    )
    reason = None
    if target_price / market_price - 1 > max_target_rate + 1e-9:
        reason = "研究目标超出允许的目标收益范围，需重新评估"
    elif metrics["gross_ratio"] + 1e-9 < min_gross_ratio:
        reason = f"原始目标毛盈亏比{metrics['gross_ratio']:.2f}低于{min_gross_ratio:.2f}"
    elif metrics["net_ratio"] + 1e-9 < min_net_ratio:
        reason = f"原始目标成本后盈亏比{metrics['net_ratio']:.2f}低于{min_net_ratio:.2f}"
    return {
        "eligible": reason is None,
        "reason": reason,
        "entry_price": round(market_price, 3),
        "stop_price": stop_price,
        "target_price": target_price,
        "stop_loss_pct": round(-stop_rate * 100, 2),
        "target_return_pct": round((target_price / market_price - 1) * 100, 2),
        "gross_reward_risk_ratio": round(metrics["gross_ratio"], 3),
        "net_reward_risk_ratio": round(metrics["net_ratio"], 3),
        "net_reward": round(metrics["net_reward"], 2),
        "net_risk": round(metrics["net_risk"], 2),
    }
