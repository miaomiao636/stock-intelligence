# -*- coding: utf-8 -*-
"""Real-time stock price fetcher using Tencent finance API"""

import time
import urllib.request
from datetime import datetime
from typing import Dict, List
from zoneinfo import ZoneInfo

# Proxy handler to bypass local proxy
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# C3: 模块级价格缓存（TTL=30秒，避免短时间内重复请求）
_price_cache: Dict[str, tuple] = {}  # code -> (data_dict, timestamp)
_CACHE_TTL = 30


def _get_cached(codes: List[str]) -> tuple:
    """从缓存中获取已缓存且未过期的数据，返回 (cached_result, uncached_codes)"""
    now = time.time()
    result = {}
    uncached = []
    for code in codes:
        entry = _price_cache.get(code)
        if entry and (now - entry[1]) < _CACHE_TTL:
            result[code] = entry[0]
        else:
            uncached.append(code)
    return result, uncached


def _set_cache(data: Dict[str, Dict]):
    """将批量数据写入缓存"""
    now = time.time()
    for code, info in data.items():
        _price_cache[code] = (info, now)


def fetch_realtime_prices(codes: List[str]) -> Dict[str, Dict]:
    """Fetch real-time prices for a list of stock codes.

    Args:
        codes: List of stock codes like ["600036", "002475", "300308"]

    Returns:
        Dict mapping code to {name, price, change_pct, volume, high, low, open}
    """
    if not codes:
        return {}

    # C3: 先查缓存，只请求未缓存的
    cached_result, uncached_codes = _get_cached(codes)
    if not uncached_codes:
        return cached_result  # 全部命中缓存

    # Build Tencent API query（仅请求未缓存的）
    prefixed = []
    for code in uncached_codes:
        if code.startswith(("8", "4")):
            prefixed.append(f"bj{code}")
        elif code.startswith(("6", "5")):
            prefixed.append(f"sh{code}")
        else:
            prefixed.append(f"sz{code}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _opener.open(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"  [WARN] Real-time price fetch failed: {e}")
        return {}

    result = {}
    for line in raw.split(";"):
        line = line.strip()
        if "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 35:
            continue
        try:
            code = parts[2]
            price = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[4]) if parts[4] else 0
            open_price = float(parts[5]) if parts[5] else 0
            volume = float(parts[6]) if parts[6] else 0
            high = float(parts[33]) if parts[33] else 0
            low = float(parts[34]) if parts[34] else 0
            change_pct = float(parts[32]) if parts[32] else 0

            if price > 0:
                fetched_at = datetime.now(ZoneInfo("Asia/Shanghai"))
                source_time = None
                raw_time = parts[30].strip() if len(parts) > 30 else ""
                if len(raw_time) >= 14 and raw_time[:14].isdigit():
                    try:
                        source_time = datetime.strptime(raw_time[:14], "%Y%m%d%H%M%S").replace(
                            tzinfo=ZoneInfo("Asia/Shanghai")
                        )
                    except ValueError:
                        source_time = None
                result[code] = {
                    "code": code,
                    "name": parts[1],
                    "price": price,
                    "prev_close": prev_close,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "change_pct": change_pct,
                    "quote_time": (source_time or fetched_at).isoformat(),
                    "trade_date": (source_time or fetched_at).date().isoformat(),
                    "trade_status": "trading" if source_time else "unknown",
                    "source_time_reliable": source_time is not None,
                    "fetched_at": fetched_at.isoformat(),
                    "source": "tencent",
                }
        except (ValueError, IndexError):
            continue

    # C3: 写入缓存
    _set_cache(result)

    # 合并缓存命中和新获取的数据
    result.update(cached_result)
    return result


def fetch_single_price(code: str) -> float:
    """B4: 单只股票实时价格（复用批量接口，统一入口）"""
    data = fetch_realtime_prices([code])
    if code in data:
        return data[code]["price"]
    return 0.0


def fetch_single_info(code: str) -> Dict:
    """B4: 单只股票实时信息（price + change_pct），统一入口

    替代 auto_trader._fetch_realtime_info 和 tracker._get_current_price 中的重复代码。
    """
    data = fetch_realtime_prices([code])
    if code in data:
        return {
            "price": data[code]["price"],
            "change_pct": data[code].get("change_pct", 0),
        }
    return None


def get_hot_stocks_prices() -> Dict[str, Dict]:
    """Fetch real-time prices for popular A-share stocks across sectors.

    Returns a comprehensive price map for the LLM to reference.
    """
    # Popular stocks across major sectors + low-price alternatives + ETFs
    hot_codes = [
        # AI/算力
        "603019", "000977", "688981", "300308", "002230", "002415",
        # 半导体
        "002371", "688012", "603501", "600584",
        # 消费电子
        "002475", "002241", "000725",
        # 新能源车
        "300750", "002594", "601633",
        # 金融
        "600036", "601318", "601995", "600030",
        # 消费
        "600887", "000858", "603369",
        # 医药
        "600276", "300015", "000538",
        # 能源
        "601088", "601225", "600028",
        # 低价龙头股（适合小资金）
        "000001", "601398", "601288", "601988",  # 银行股
        "600019", "600010", "600005",              # 钢铁
        "601857", "600028", "601800",              # 能源/基建
        "000002", "600048", "001979",              # 地产
        "002304", "000895", "600887",              # 消费
        "600585", "002460", "601012",              # 医药/新能源
        # 热门板块ETF（价格低，适合小资金）
        "515070",  # 人工智能ETF
        "512480",  # 半导体ETF
        "515030",  # 新能源车ETF
        "159928",  # 消费ETF
        "512010",  # 医药ETF
        "510230",  # 金融ETF
        "159732",  # 消费电子ETF
        "510500",  # 中证500ETF
        "510300",  # 沪深300ETF
        "159915",  # 创业板ETF
        "512880",  # 证券ETF
        "512660",  # 军工ETF
        "516160",  # 新能源ETF
        "512690",  # 酒ETF
        "159869",  # 游戏ETF
        "512760",  # 芯片ETF
        "515790",  # 光伏ETF
        "516510",  # 云计算ETF
    ]

    return fetch_realtime_prices(hot_codes)
