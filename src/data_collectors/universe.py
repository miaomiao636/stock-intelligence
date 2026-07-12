# -*- coding: utf-8 -*-
"""基于Tushare批量数据的point-in-time A股候选池。"""

from __future__ import annotations

import math
import os
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List


def filter_and_rank_candidates(
    stocks: List[Dict],
    daily: List[Dict],
    daily_basic: List[Dict],
    as_of_date: str,
    limit: int = 50,
) -> List[Dict]:
    """纯函数资格过滤；金额单位按Tushare定义换算为人民币。"""
    daily_map = {row.get("ts_code"): row for row in daily}
    basic_map = {row.get("ts_code"): row for row in daily_basic}
    as_of = date.fromisoformat(as_of_date)
    result = []
    for stock in stocks:
        ts_code = stock.get("ts_code")
        symbol = str(stock.get("symbol") or "")
        name = str(stock.get("name") or "")
        if not ts_code or not symbol or "ST" in name.upper():
            continue
        if symbol.startswith(("30", "68", "8", "4")):
            continue
        try:
            listed = datetime.strptime(str(stock.get("list_date")), "%Y%m%d").date()
            if (as_of - listed).days < 5:
                continue
        except (TypeError, ValueError):
            continue
        quote = daily_map.get(ts_code) or {}
        valuation = basic_map.get(ts_code) or {}
        try:
            price = float(quote.get("close") or 0)
            pct_chg = float(quote.get("pct_chg") or 0)
            amount_cny = float(quote.get("amount") or 0) * 1000
            market_cap_cny = float(valuation.get("total_mv") or 0) * 10000
            turnover = float(valuation.get("turnover_rate") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or price > 12 or abs(pct_chg) >= 9.5:
            continue
        if amount_cny < 50_000_000 or market_cap_cny < 3_000_000_000:
            continue
        score = math.log10(max(amount_cny, 1)) + min(turnover, 15) * 0.03 - abs(pct_chg - 1.0) * 0.08
        result.append({
            "ts_code": ts_code,
            "code": symbol,
            "name": name,
            "sector": stock.get("industry") or "其他",
            "price": price,
            "prev_close": float(quote.get("pre_close") or 0),
            "change_pct": pct_chg,
            "daily_amount": amount_cny,
            "market_cap": market_cap_cny,
            "turnover_rate": turnover,
            "as_of_date": as_of_date,
            "eligibility_score": round(score, 6),
        })
    result.sort(key=lambda item: (-item["eligibility_score"], item["code"]))
    return result[:limit]


def _load_or_fetch(path: Path, fetcher, max_age_seconds: int = None) -> List[Dict]:
    """优先使用有效缓存；接口限频时允许回退到已有旧缓存。"""
    cached = None
    if path.exists():
        try:
            with open(path, encoding="utf-8") as handle:
                cached = json.load(handle)
            if max_age_seconds is None or time.time() - path.stat().st_mtime <= max_age_seconds:
                return cached
        except (OSError, json.JSONDecodeError):
            cached = None
    try:
        records = fetcher().to_dict("records")
        if not records:
            raise RuntimeError("数据源返回空记录")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(records, handle, ensure_ascii=False)
        os.replace(temp, path)
        return records
    except Exception:
        if cached:
            return cached
        raise


def get_ranked_candidates(
    as_of_date: str,
    limit: int = 50,
    cache_dir: Path = None,
    pro_client=None,
) -> List[Dict]:
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN未配置，无法建立全市场候选池")
    if pro_client is None:
        import tushare as ts
        pro_client = ts.pro_api(token)
    pro = pro_client
    trade_date = as_of_date.replace("-", "")
    cache_dir = Path(cache_dir or Path(__file__).parents[2] / "data" / "cache" / "universe")
    stocks = _load_or_fetch(
        cache_dir / "stock_basic.json",
        lambda: pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,symbol,name,industry,list_date,market",
        ),
        max_age_seconds=7 * 24 * 3600,
    )
    daily = _load_or_fetch(
        cache_dir / f"daily_{trade_date}.json",
        lambda: pro.daily(
            trade_date=trade_date,
            fields="ts_code,trade_date,close,pre_close,pct_chg,vol,amount",
        ),
    )
    daily_basic = _load_or_fetch(
        cache_dir / f"daily_basic_{trade_date}.json",
        lambda: pro.daily_basic(
            trade_date=trade_date,
            fields="ts_code,trade_date,turnover_rate,total_mv,circ_mv",
        ),
    )
    if not stocks or not daily or not daily_basic:
        raise RuntimeError(f"Tushare在{as_of_date}返回的全市场批量数据不完整")
    return filter_and_rank_candidates(stocks, daily, daily_basic, as_of_date, limit)
