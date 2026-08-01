# -*- coding: utf-8 -*-
"""仓位风控配置读取（P1-4: 单一来源）

auto_trader 与 risk_manager 共用本模块，消除原先三处不一致的仓位上限。
配置位于 config/strategy.yaml 的 position_limits。
"""

from pathlib import Path
from typing import Optional

import yaml

_CFG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
_CFG_CACHE = None
_CFG_MTIME = None


def _load_config() -> dict:
    """加载 strategy.yaml，文件变更时自动刷新缓存。"""
    global _CFG_CACHE, _CFG_MTIME
    mtime = _CFG_PATH.stat().st_mtime if _CFG_PATH.exists() else 0
    if _CFG_CACHE is None or mtime != _CFG_MTIME:
        with open(_CFG_PATH, encoding="utf-8") as f:
            _CFG_CACHE = yaml.safe_load(f) or {}
        _CFG_MTIME = mtime
    return _CFG_CACHE


def _pick_tier(total_equity: float, tiers: list) -> float:
    """按总资产从分档列表中选取上限。max_equity=None 为兜底档。"""
    if not tiers:
        return 0.15
    for tier in tiers:
        max_eq = tier.get("max_equity")
        if max_eq is None or total_equity <= max_eq:
            return float(tier.get("limit", 0.15))
    return float(tiers[-1].get("limit", 0.15))


def get_position_limit(total_equity: float, horizon: str = "short") -> float:
    """获取单票仓位上限。

    Args:
        total_equity: 账户总资产
        horizon: 持仓周期 short/medium/long
    """
    cfg = _load_config()
    limits = cfg.get("position_limits", {})
    tiers = limits.get(horizon, limits.get("short", []))
    return _pick_tier(total_equity, tiers)


def get_sector_limit(total_equity: float) -> float:
    """获取板块集中度上限。"""
    cfg = _load_config()
    tiers = cfg.get("position_limits", {}).get("sector", [])
    return _pick_tier(total_equity, tiers)


def get_cash_reserve_pct(total_equity: float) -> float:
    """获取现金保留比例。"""
    cfg = _load_config()
    tiers = cfg.get("position_limits", {}).get("cash_reserve", [])
    return _pick_tier(total_equity, tiers)


def get_initial_cash() -> float:
    """获取新建模拟账户的默认本金。"""
    cfg = _load_config()
    return float(cfg.get("initial_cash", 20000))


def get_paper_trading_config() -> dict:
    """返回模拟交易配置副本，供选仓和最终成交风控共用。"""
    cfg = _load_config().get("paper_trading", {})
    return dict(cfg) if isinstance(cfg, dict) else {}


def get_regime_limits(market_regime: str) -> dict:
    """返回指定市场状态的仓位规则，并与全局规则合并。"""
    cfg = get_paper_trading_config()
    regimes = cfg.get("regime_limits", {})
    regime = market_regime if market_regime in regimes else "neutral"
    merged = {
        "max_position_pct": float(cfg.get("max_position_pct", 0.30)),
        "max_total_exposure_pct": float(cfg.get("max_total_exposure_pct", 0.80)),
        "max_new_positions_per_day": int(cfg.get("max_new_positions_per_day", 2)),
        "allow_stocks": True,
        "allow_etfs": True,
    }
    selected = regimes.get(regime, {})
    if isinstance(selected, dict):
        merged.update(selected)
    return merged


def get_max_stock_price() -> float:
    """获取策略允许的最高股票价格。"""
    cfg = _load_config().get("custom_params", {})
    return float(cfg.get("max_stock_price", 50))
