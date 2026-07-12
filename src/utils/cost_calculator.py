# -*- coding: utf-8 -*-
"""B5: 统一交易费用计算（从 config/cost_model.yaml 读取，消除硬编码）"""

import yaml
from pathlib import Path
from typing import Optional

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "cost_model.yaml"
_cached_config = None


def _load_cost_model():
    """加载成本模型配置（带缓存）"""
    global _cached_config
    if _cached_config is None:
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                _cached_config = yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ 加载 cost_model.yaml 失败，使用默认费率: {e}")
            _cached_config = {
                "cost_components": {
                    "commission": {"rate_bps": 1.5, "min_amount": 5},
                    "stamp_tax": {"rate_bps": 5},
                    "transfer_fee": {"rate_bps": 0.5},
                }
            }
    return _cached_config


def calculate_fees(amount: float, action: str, instrument_type: str = "stock") -> float:
    """计算交易费用

    Args:
        amount: 成交金额
        action: "buy" 或 "sell"
        instrument_type: "stock" 或 "etf"

    Returns:
        总费用（佣金 + 印花税 + 过户费）
    """
    if amount <= 0:
        return 0.0

    config = _load_cost_model()
    components = config.get("cost_components", {})
    instrument_rules = config.get("instrument_rules", {})

    # 1. 佣金（万1.5，最低5元）
    commission_cfg = components.get("commission", {})
    commission_rate = commission_cfg.get("rate_bps", 1.5) / 10000
    min_commission = commission_cfg.get("min_amount", 5)
    commission = max(min_commission, amount * commission_rate)

    # 2. 印花税（万5，仅卖出；ETF免印花税）
    stamp_tax = 0.0
    if action == "sell":
        instrument_rule = instrument_rules.get(instrument_type, {})
        stamp_rate_bps = instrument_rule.get("stamp_tax_sell_bps", 5)
        stamp_tax = amount * stamp_rate_bps / 10000

    # 3. 过户费（万0.5，双向）
    transfer_cfg = components.get("transfer_fee", {})
    transfer_rate = transfer_cfg.get("rate_bps", 0.5) / 10000
    transfer_fee = amount * transfer_rate

    return round(commission + stamp_tax + transfer_fee, 2)


def calculate_trade_costs(amount: float, action: str, instrument_type: str = "stock") -> dict:
    """返回费用、滑点和方向调整后的模拟成交价信息。

    滑点是价格冲击，不是券商费用。为避免回测漏算，它单独记录，同时进入
    `total_cost`。调用方应使用 `effective_price_factor` 调整行情价。
    """
    if amount <= 0:
        return {
            "fees": 0.0,
            "slippage": 0.0,
            "total_cost": 0.0,
            "effective_price_factor": 1.0,
            "effective_price": 0.0,
        }
    config = _load_cost_model()
    slippage_bps = float(
        config.get("cost_components", {}).get("slippage", {}).get("base_bps", 5)
    )
    slippage = round(amount * slippage_bps / 10000, 2)
    fees = calculate_fees(amount, action, instrument_type)
    direction = 1 if action == "buy" else -1
    factor = 1 + direction * slippage_bps / 10000
    return {
        "fees": fees,
        "slippage": slippage,
        "total_cost": round(fees + slippage, 2),
        "effective_price_factor": factor,
        "effective_price": None,
    }
