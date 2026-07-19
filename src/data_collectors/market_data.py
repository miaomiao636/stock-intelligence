# -*- coding: utf-8 -*-
"""A股市场数据采集"""

import json
import os
import urllib.request
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

# 绕过VPN代理：国内金融API直连（TUN模式下代理无法连接eastmoney/sina等）
_FINANCIAL_NO_PROXY = [
    "eastmoney.com", "push2his.eastmoney.com", "push2.eastmoney.com",
    "quote.eastmoney.com", "datacenter-web.eastmoney.com",
    "sinajs.cn", "hq.sinajs.cn", "finance.sina.com.cn",
    "gtimg.cn", "qt.gtimg.cn",
    "jqdata.cn", "joinquant.com",
    "tushare.pro", "api.tushare.pro",
    "akshare.xyz", "akfamily.github.io",
]

def _bypass_proxy_for_finance():
    """将国内金融数据域名加入NO_PROXY，避免普通HTTP代理拦截"""
    existing = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
    existing_list = [d.strip() for d in existing.split(",") if d.strip()]
    added = []
    for domain in _FINANCIAL_NO_PROXY:
        if not any(domain == e or domain.endswith("." + e.lstrip("*.")) or e == "*"
                   for e in existing_list):
            added.append(domain)
    if added:
        new_list = existing_list + added
        new_value = ",".join(new_list)
        os.environ["NO_PROXY"] = new_value
        os.environ["no_proxy"] = new_value

_bypass_proxy_for_finance()

# 全局urllib opener：空ProxyHandler，绕过TUN/系统代理
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


def _stock_code_to_secid(code: str) -> str:
    """将股票代码转换为东方财富secid格式"""
    if code.startswith("6"):
        return f"1.{code}"  # 上海
    elif code.startswith(("0", "3")):
        return f"0.{code}"  # 深圳
    elif code.startswith(("8", "4")):
        return f"0.{code}"  # 北交所
    else:
        return f"0.{code}"


def _get_stock_data_eastmoney_direct(stock_code: str, days: int = 5) -> Dict:
    """获取个股数据：东方财富K线优先，失败后降级到腾讯/新浪实时行情"""
    # 尝试1: 东方财富K线API（最完整：含OHLC+成交量）
    result = _try_eastmoney_kline(stock_code)
    if "error" not in result:
        return result

    # 尝试2: 腾讯实时行情（已验证可用）
    result2 = _try_tencent_realtime(stock_code)
    if "error" not in result2:
        return result2

    # 尝试3: 新浪实时行情（备选）
    return _try_sina_realtime(stock_code)


def _try_eastmoney_kline(code: str) -> Dict:
    """尝试东方财富K线API（含完整OHLC）"""
    secid = _stock_code_to_secid(code)
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"fields1=f1,f2,f3,f4,f5,f6&"
        f"fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=101&fqt=1&secid={secid}&beg={start_date}&end={end_date}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _NO_PROXY_OPENER.open(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {"error": "eastmoney_kline_failed"}

    if raw.get("code") != 0 or not raw.get("data"):
        return {"error": raw.get("msg", "no data")}

    klines = raw["data"]["klines"]
    if not klines:
        return {"error": "empty klines"}
    parts = klines[-1].split(",")
    if len(parts) < 7:
        return {"error": "bad format"}

    return {
        "code": code, "date": parts[0],
        "open": float(parts[1]), "close": float(parts[2]),
        "high": float(parts[3]), "low": float(parts[4]),
        "volume": float(parts[5]), "amount": float(parts[6]),
        "change_pct": float(parts[7]) if len(parts) > 7 else 0,
    }


def _try_tencent_realtime(code: str) -> Dict:
    """腾讯行情API（与 realtime_prices 同源，已验证可绕过TUN代理）"""
    prefix = "bj" if code.startswith(("8", "4")) else ("sh" if code.startswith("6") else "sz")
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _NO_PROXY_OPENER.open(req, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        return {"error": f"tencent_failed: {e}"}

    # 解析格式: v_sh000001="1~上证指数~...
    for line in raw.strip().split(";"):
        if "~" in line and code in line:
            p = line.split("~")
            if len(p) > 32:
                price = float(p[3]) if p[3] else 0
                prev_close = float(p[4]) if p[4] else price
                change = float(p[32]) if len(p) > 32 and p[32] else 0
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                return {
                    "code": code, "date": date.today().isoformat(),
                    "open": price, "close": price,
                    "high": price, "low": price,
                    "volume": 0, "amount": 0,
                    "change_pct": round(change_pct, 2),
                    "_source": "tencent_realtime",
                }
    return {"error": "tencent_parse_failed"}


def _try_sina_realtime(code: str) -> Dict:
    """新浪行情API（备选数据源）"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"https://hq.sinajs.cn/list={prefix}{code}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    try:
        with _NO_PROXY_OPENER.open(req, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        return {"error": f"sina_failed: {e}"}

    for line in raw.strip().split("\n"):
        if "=" in line and code in line:
            # 格式: var hq_str_sh000001="上证指数,...
            content = line.split('"')[1]
            fields = content.split(",")
            if len(fields) >= 32:
                name = fields[0]
                open_p = float(fields[1]) if fields[1] else 0
                prev_close = float(fields[2]) if fields[2] else 0
                price = float(fields[3]) if fields[3] else 0
                high = float(fields[4]) if fields[4] else price
                low = float(fields[5]) if fields[5] else price
                volume = float(fields[8]) if len(fields) > 8 else 0
                change_pct = float(fields[31]) if len(fields) > 31 and fields[31] else 0
                return {
                    "code": code, "name": name,
                    "date": date.today().isoformat(),
                    "open": open_p, "close": price,
                    "high": high, "low": low,
                    "volume": volume, "amount": 0,
                    "change_pct": round(change_pct, 2),
                    "_source": "sina_realtime",
                }
    return {"error": "sina_parse_failed"}


def get_index_data(index_code: str = "000001") -> Dict:
    """获取指数数据（Tushare优先→akshare→东方财富→腾讯→新浪）"""
    # 优先尝试 Tushare Pro
    result = _try_tushare_index(index_code)
    if "error" not in result:
        return result

    # 其次 akshare
    if AKSHARE_AVAILABLE:
        try:
            # 根据代码前缀判断市场
            if index_code.startswith("0"):
                symbol = f"sh{index_code}"
            elif index_code.startswith("3"):
                symbol = f"sz{index_code}"
            else:
                symbol = f"sh{index_code}"

            df = ak.stock_zh_index_daily(symbol=symbol)
            if not df.empty:
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest

                close = float(latest.get("close", 0))
                prev_close = float(prev.get("close", 0))
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0

                return {
                    "code": index_code,
                    "name": "上证指数",
                    "date": str(latest.get("date", "")),
                    "open": float(latest.get("open", 0)),
                    "close": close,
                    "high": float(latest.get("high", 0)),
                    "low": float(latest.get("low", 0)),
                    "volume": float(latest.get("volume", 0)),
                    "prev_close": prev_close,
                    "change_pct": round(change_pct, 2),
                    "data_as_of": str(latest.get("date", "")),
                }
        except Exception as e:
            pass  # 降级到直连

    # Fallback: 东方财富指数API直连
    return _get_index_data_eastmoney_direct(index_code)


def get_stock_data(stock_code: str) -> Dict:
    """获取个股数据（Tushare优先→akshare→东方财富→腾讯→新浪 五级降级）"""
    # 优先尝试 Tushare Pro（专业数据源，最可靠）
    result = _try_tushare_stock(stock_code)
    if "error" not in result:
        return result

    # 其次尝试 akshare
    if AKSHARE_AVAILABLE:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="qfq")
            if not df.empty:
                latest = df.iloc[-1]
                return {
                    "code": stock_code,
                    "date": str(latest.get("日期", "")),
                    "open": float(latest.get("开盘", 0)),
                    "close": float(latest.get("收盘", 0)),
                    "high": float(latest.get("最高", 0)),
                    "low": float(latest.get("最低", 0)),
                    "volume": float(latest.get("成交量", 0)),
                    "amount": float(latest.get("成交额", 0)),
                    "change_pct": float(latest.get("涨跌幅", 0)),
                }
        except Exception:
            pass

    # Fallback: 东方财富→腾讯→新浪
    return _get_stock_data_eastmoney_direct(stock_code)


def _format_trade_date(value) -> str:
    """将 YYYYMMDD / datetime / date 统一为 YYYY-MM-DD。"""
    text = str(value or "")[:10]
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return text


def _try_tushare_stock_on(code: str, target_date: str) -> Dict:
    """只读取指定交易日，禁止用最新行情替代历史行情。"""
    try:
        import tushare as ts

        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            return {"error": "TUSHARE_TOKEN未配置"}
        ts.set_token(token)
        pro = ts.pro_api()
        suffix = ".SH" if code.startswith("6") else ".BJ" if code.startswith(("8", "4")) else ".SZ"
        compact = target_date.replace("-", "")
        df = pro.daily(ts_code=f"{code}{suffix}", start_date=compact, end_date=compact)
        if df.empty:
            return {"error": "tushare_target_date_no_data"}
        row = df.iloc[0]
        data_date = _format_trade_date(row.get("trade_date", ""))
        return {
            "code": code,
            "date": data_date,
            "open": float(row.get("open", 0)),
            "close": float(row.get("close", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "volume": float(row.get("vol", 0)),
            "amount": float(row.get("amount", 0)),
            "change_pct": float(row.get("pct_chg", 0)),
            "_source": "tushare_pro_historical",
        }
    except Exception as exc:
        return {"error": f"tushare_historical_failed: {exc}"}


def _try_eastmoney_stock_on(code: str, target_date: str) -> Dict:
    """东方财富未复权日线的精确日期降级源。"""
    compact = target_date.replace("-", "")
    secid = _stock_code_to_secid(code)
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        "fields1=f1,f2,f3,f4,f5,f6&"
        "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=101&fqt=0&secid={secid}&beg={compact}&end={compact}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _NO_PROXY_OPENER.open(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        klines = (raw.get("data") or {}).get("klines") or []
        row = next((line for line in klines if line.startswith(target_date + ",")), None)
        if not row:
            return {"error": "eastmoney_target_date_no_data"}
        parts = row.split(",")
        return {
            "code": code,
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
            "change_pct": float(parts[8]) if len(parts) > 8 else 0,
            "_source": "eastmoney_historical",
        }
    except Exception as exc:
        return {"error": f"eastmoney_historical_failed: {exc}"}


def get_stock_data_on(stock_code: str, target_date: str) -> Dict:
    """获取指定交易日的个股 OHLC；找不到时返回错误，不回退到最新价。"""
    try:
        target = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        return {"error": "invalid_target_date"}
    if target > date.today():
        return {"error": "future_target_date_not_allowed"}

    result = _try_tushare_stock_on(stock_code, target_date)
    if "error" not in result and result.get("date") == target_date:
        return result

    if AKSHARE_AVAILABLE:
        try:
            compact = target.strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=compact,
                end_date=compact,
                adjust="",
            )
            if not df.empty:
                row = df.iloc[-1]
                data_date = _format_trade_date(row.get("日期", ""))
                if data_date == target_date:
                    return {
                        "code": stock_code,
                        "date": data_date,
                        "open": float(row.get("开盘", 0)),
                        "close": float(row.get("收盘", 0)),
                        "high": float(row.get("最高", 0)),
                        "low": float(row.get("最低", 0)),
                        "volume": float(row.get("成交量", 0)),
                        "amount": float(row.get("成交额", 0)),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "_source": "akshare_historical",
                    }
        except Exception:
            pass

    return _try_eastmoney_stock_on(stock_code, target_date)


def _try_tushare_index_on(index_code: str, target_date: str) -> Dict:
    try:
        import tushare as ts

        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            return {"error": "TUSHARE_TOKEN未配置"}
        ts.set_token(token)
        pro = ts.pro_api()
        suffix = ".SZ" if index_code.startswith("3") else ".SH"
        target = date.fromisoformat(target_date)
        start = (target - timedelta(days=15)).strftime("%Y%m%d")
        end = target.strftime("%Y%m%d")
        df = pro.index_daily(ts_code=f"{index_code}{suffix}", start_date=start, end_date=end)
        if df.empty:
            return {"error": "tushare_target_index_no_data"}
        rows = df.sort_values("trade_date", ascending=False).reset_index(drop=True)
        row = rows.iloc[0]
        data_date = _format_trade_date(row.get("trade_date", ""))
        if data_date != target_date:
            return {"error": "tushare_target_index_date_mismatch"}
        close = float(row.get("close", 0))
        prev_close = float(row.get("pre_close", 0))
        return {
            "code": index_code,
            "date": data_date,
            "close": close,
            "prev_close": prev_close,
            "change_pct": float(row.get("pct_chg", 0)),
            "_source": "tushare_index_historical",
        }
    except Exception as exc:
        return {"error": f"tushare_index_historical_failed: {exc}"}


def _try_eastmoney_index_on(index_code: str, target_date: str) -> Dict:
    target = date.fromisoformat(target_date)
    start = (target - timedelta(days=15)).strftime("%Y%m%d")
    end = target.strftime("%Y%m%d")
    secid = _stock_code_to_secid(index_code)
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        "fields1=f1,f2,f3,f4,f5,f6&"
        "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=101&fqt=0&secid={secid}&beg={start}&end={end}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _NO_PROXY_OPENER.open(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        klines = (raw.get("data") or {}).get("klines") or []
        rows = [line.split(",") for line in klines]
        index = next((i for i, row in enumerate(rows) if row[0] == target_date), None)
        if index is None:
            return {"error": "eastmoney_target_index_no_data"}
        row = rows[index]
        prev_close = float(rows[index - 1][2]) if index > 0 else float(row[2])
        close = float(row[2])
        return {
            "code": index_code,
            "date": row[0],
            "close": close,
            "prev_close": prev_close,
            "change_pct": round((close - prev_close) / prev_close * 100, 2) if prev_close else 0,
            "_source": "eastmoney_index_historical",
        }
    except Exception as exc:
        return {"error": f"eastmoney_index_historical_failed: {exc}"}


def get_index_data_on(index_code: str, target_date: str) -> Dict:
    """获取指定交易日指数数据，绝不以实时行情替代。"""
    try:
        target = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        return {"error": "invalid_target_date"}
    if target > date.today():
        return {"error": "future_target_date_not_allowed"}
    result = _try_tushare_index_on(index_code, target_date)
    if "error" not in result and result.get("date") == target_date:
        return result

    if AKSHARE_AVAILABLE:
        try:
            symbol = f"sz{index_code}" if index_code.startswith("3") else f"sh{index_code}"
            df = ak.stock_zh_index_daily(symbol=symbol)
            if not df.empty:
                rows = df.copy()
                rows["_date"] = rows["date"].map(_format_trade_date)
                rows = rows.sort_values("_date").reset_index(drop=True)
                matches = rows.index[rows["_date"] == target_date].tolist()
                if matches:
                    idx = matches[-1]
                    row = rows.iloc[idx]
                    prev_close = float(rows.iloc[idx - 1].get("close", 0)) if idx > 0 else float(row.get("close", 0))
                    close = float(row.get("close", 0))
                    return {
                        "code": index_code,
                        "date": target_date,
                        "close": close,
                        "prev_close": prev_close,
                        "change_pct": round((close - prev_close) / prev_close * 100, 2) if prev_close else 0,
                        "_source": "akshare_index_historical",
                    }
        except Exception:
            pass
    return _try_eastmoney_index_on(index_code, target_date)


def _try_tushare_stock(code: str) -> Dict:
    """Tushare Pro 获取个股日线（主数据源）"""
    try:
        import tushare as ts
        import os
        from datetime import timedelta

        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            return {"error": "TUSHARE_TOKEN未配置"}

        ts.set_token(token)
        pro = ts.pro_api()

        # 转换代码格式: 000001 → 000001.SZ, 600010 → 600010.SH
        if code.startswith("6"):
            ts_code = f"{code}.SH"
        elif code.startswith(("0", "3")):
            ts_code = f"{code}.SZ"
        elif code.startswith(("8", "4")):
            ts_code = f"{code}.BJ"
        else:
            ts_code = f"{code}.SZ"

        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df.empty:
            return {"error": "tushare_no_data"}

        latest = df.iloc[0]  # Tushare按日期倒序，第一条是最新
        trade_date = str(latest.get("trade_date", ""))
        # 格式化日期: 20260710 → 2026-07-10
        formatted_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if len(trade_date) == 8 else trade_date

        return {
            "code": code,
            "date": formatted_date,
            "open": float(latest.get("open", 0)),
            "close": float(latest.get("close", 0)),
            "high": float(latest.get("high", 0)),
            "low": float(latest.get("low", 0)),
            "volume": float(latest.get("vol", 0)),
            "amount": float(latest.get("amount", 0)),
            "change_pct": float(latest.get("pct_chg", 0)),
            "_source": "tushare_pro",
        }
    except Exception as e:
        return {"error": f"tushare_failed: {e}"}


def _try_tushare_index(index_code: str = "000001") -> Dict:
    """Tushare Pro 获取指数日线（主数据源）"""
    try:
        import tushare as ts
        from datetime import timedelta

        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            return {"error": "TUSHARE_TOKEN未配置"}

        ts.set_token(token)
        pro = ts.pro_api()

        index_names = {
            "000001": "上证指数", "399001": "深证成指", "399006": "创业板指",
            "000300": "沪深300", "000016": "上证50", "399005": "中小板指",
        }

        if index_code.startswith("0"):
            ts_code = f"{index_code}.SH"
        elif index_code.startswith("3"):
            ts_code = f"{index_code}.SZ"
        else:
            ts_code = f"{index_code}.SH"

        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df.empty or len(df) < 2:
            return {"error": "tushare_no_index_data"}

        latest = df.iloc[0]
        prev = df.iloc[1]
        close = float(latest.get("close", 0))
        prev_close = float(prev.get("close", 0))
        change_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0

        trade_date = str(latest.get("trade_date", ""))
        formatted_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if len(trade_date) == 8 else trade_date

        return {
            "code": index_code,
            "name": index_names.get(index_code, f"指数{index_code}"),
            "date": formatted_date,
            "open": float(latest.get("open", 0)),
            "close": close,
            "high": float(latest.get("high", 0)),
            "low": float(latest.get("low", 0)),
            "volume": float(latest.get("vol", 0)),
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "data_as_of": formatted_date,
            "_source": "tushare_pro",
        }
    except Exception as e:
        return {"error": f"tushare_index_failed: {e}"}


def _get_index_data_eastmoney_direct(index_code: str = "000001") -> Dict:
    """获取指数数据：东方财富→腾讯→新浪 三级降级"""
    index_names = {
        "000001": "上证指数", "000002": "A股指数", "000003": "B股指数",
        "399001": "深证成指", "399006": "创业板指", "399005": "中小板指",
        "000300": "沪深300",
    }

    # 尝试1: 东方财富K线
    secid = _stock_code_to_secid(index_code)
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"fields1=f1,f2,f3,f4,f5,f6&"
        f"fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=101&fqt=1&secid={secid}&beg={start_date}&end={end_date}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _NO_PROXY_OPENER.open(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        if raw.get("code") == 0 and raw.get("data"):
            klines = raw["data"]["klines"]
            if len(klines) >= 2:
                lp, pp = klines[-1].split(","), klines[-2].split(",")
                close, prev_close = float(lp[2]), float(pp[2]) if len(pp) > 2 else float(lp[2])
                return {
                    "code": index_code,
                    "name": index_names.get(index_code, f"指数{index_code}"),
                    "date": lp[0], "open": float(lp[1]),
                    "close": close, "high": float(lp[3]),
                    "low": float(lp[4]), "volume": float(lp[5]),
                    "prev_close": prev_close,
                    "change_pct": round((close - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0,
                }
    except Exception:
        pass

    # 尝试2: 腾讯行情
    tencent_result = _try_tencent_realtime(index_code)
    if "error" not in tencent_result:
        tencent_result["name"] = index_names.get(index_code, f"指数{index_code}")
        return tencent_result

    # 尝试3: 新浪行情
    sina_result = _try_sina_realtime(index_code)
    if "error" not in sina_result:
        sina_result["name"] = sina_result.get("name", index_names.get(index_code))
        return sina_result

    return {"error": "所有指数数据源均不可用"}


def get_market_overview() -> Dict:
    """获取市场概况（C6: 指数查询并行化）"""
    if not AKSHARE_AVAILABLE:
        return {"error": "akshare not installed"}

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        index_list = [("000001", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指")]
        indices = {}

        # C6: 3个指数查询并行执行（原来串行约3秒，并行约1秒）
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(get_index_data, code): (code, name)
                for code, name in index_list
            }
            for future in as_completed(futures):
                code, name = futures[future]
                try:
                    index_data = future.result()
                    if "error" not in index_data:
                        indices[code] = {
                            "name": name,
                            "close": index_data.get("close", 0),
                            "prev_close": index_data.get("prev_close", 0),
                            "change_pct": index_data.get("change_pct", 0),
                            "data_as_of": index_data.get("data_as_of", ""),
                        }
                except Exception as e:
                    print(f"  ⚠️ 获取 {name} 失败: {e}")

        return {
            "date": date.today().isoformat(),
            "indices": indices,
        }
    except Exception as e:
        return {"error": str(e)}
