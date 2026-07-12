# -*- coding: utf-8 -*-
"""市场状态检测模块

根据指数涨跌、成交量、板块轮动等判断当前市场状态：
- bullish（牛市）：指数20日线上行，板块普涨
- bearish（熊市）：指数20日线下行，板块普跌
- range（震荡）：指数区间波动，板块分化
- high_volatility（高波动）：日内振幅大，趋势不明

市场状态影响推荐策略：
- bullish: 可适当追强，setup_ready占比可高
- bearish: 保守为主，setup_ready最多2只
- range: 均衡配置，注重低吸
- high_volatility: 控制仓位，setup_ready最多3只
"""

from datetime import date
from typing import Dict


def detect_market_regime(market_data: Dict) -> str:
    """检测市场状态

    Args:
        market_data: 含indices字段的行情数据

    Returns:
        'bullish' | 'bearish' | 'range' | 'high_volatility' | 'neutral'
    """
    indices = market_data.get("indices", {})
    if not indices:
        return "neutral"

    # 收集所有指数的涨跌幅
    changes = []
    for code, idx in indices.items():
        if isinstance(idx, dict):
            change = idx.get("change_pct", 0)
            if change != 0:
                changes.append(change)

    if not changes:
        return "neutral"

    avg_change = sum(changes) / len(changes)
    max_change = max(abs(c) for c in changes)
    # 方向一致性：所有指数同方向？
    all_up = all(c > 0 for c in changes)
    all_down = all(c < 0 for c in changes)

    # 高波动：任一指数涨跌幅>2%
    if max_change > 2.0:
        return "high_volatility"

    # 牛市：平均涨幅>0.5%且方向一致向上
    if all_up and avg_change > 0.5:
        return "bullish"

    # 熊市：平均跌幅>0.5%且方向一致向下
    if all_down and avg_change < -0.5:
        return "bearish"

    # 震荡：有涨有跌或涨跌幅小
    return "range"


def get_action_limits(regime: str) -> Dict:
    """根据市场状态获取action数量限制

    Returns:
        {"max_setup_ready": int, "max_track": int, "max_watch": int}
    """
    limits = {
        "bullish": {"max_setup_ready": 6, "max_track": 2, "max_watch": 1},
        "bearish": {"max_setup_ready": 2, "max_track": 3, "max_watch": 3},
        "range": {"max_setup_ready": 4, "max_track": 3, "max_watch": 2},
        "high_volatility": {"max_setup_ready": 3, "max_track": 3, "max_watch": 2},
        "neutral": {"max_setup_ready": 4, "max_track": 2, "max_watch": 2},
    }
    return limits.get(regime, limits["neutral"])


def get_risk_multiplier(regime: str) -> float:
    """根据市场状态获取风险系数倍数

    bullish: 1.0（正常仓位）
    bearish: 0.5（半仓操作）
    range: 0.8（略降仓位）
    high_volatility: 0.6（大幅降仓）
    neutral: 1.0
    """
    multipliers = {
        "bullish": 1.0,
        "bearish": 0.5,
        "range": 0.8,
        "high_volatility": 0.6,
        "neutral": 1.0,
    }
    return multipliers.get(regime, 1.0)
