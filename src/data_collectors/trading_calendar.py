# -*- coding: utf-8 -*-
"""交易日历模块"""

from datetime import datetime, date
from typing import Optional

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


_TRADE_DATES_CACHE = None


def _trade_dates():
    """Load the exchange calendar once per day; retry after transient failures."""
    global _TRADE_DATES_CACHE
    cache_date = date.today()
    if _TRADE_DATES_CACHE is not None and _TRADE_DATES_CACHE[0] == cache_date:
        return _TRADE_DATES_CACHE[1]
    try:
        df = ak.tool_trade_date_hist_sina()
        trade_dates = set(df["trade_date"].astype(str).tolist())
        _TRADE_DATES_CACHE = (cache_date, trade_dates)
        return trade_dates
    except Exception:
        return None


def is_trading_day(target_date: Optional[date] = None) -> bool:
    """检查是否是交易日"""
    if target_date is None:
        target_date = date.today()
    
    if not AKSHARE_AVAILABLE:
        # 简单判断：周一到周五
        return target_date.weekday() < 5
    
    trade_dates = _trade_dates()
    if trade_dates is None:
        # 降级：周一到周五
        return target_date.weekday() < 5
    return target_date.strftime("%Y-%m-%d") in trade_dates


def get_next_trading_day(target_date: Optional[date] = None) -> date:
    """获取下一个交易日"""
    if target_date is None:
        target_date = date.today()
    
    from datetime import timedelta
    next_day = target_date + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return next_day


def get_previous_trading_day(target_date: Optional[date] = None) -> date:
    """获取上一个交易日"""
    if target_date is None:
        target_date = date.today()

    from datetime import timedelta
    prev_day = target_date - timedelta(days=1)
    while not is_trading_day(prev_day):
        prev_day -= timedelta(days=1)
    return prev_day


def count_trading_days(start_date: date, end_date: date) -> int:
    """计算两个日期间的交易日数（不含 start_date，含 end_date）。

    P2: 供时间止损使用，排除周末/节假日，比自然日更准确。
    """
    from datetime import timedelta
    count = 0
    d = start_date + timedelta(days=1)
    while d <= end_date:
        if is_trading_day(d):
            count += 1
        d += timedelta(days=1)
    return count
