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
    """B7: 获取初始资金（单一来源，消除8处硬编码100000）"""
    cfg = _load_config()
    return float(cfg.get("initial_cash", 4000))
