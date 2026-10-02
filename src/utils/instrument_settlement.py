"""保守的证券可卖批次规则，不信任推荐中的 instrument_type。

股票 ETF 为 T+1，只有部分债券、黄金、跨境和货币 ETF 支持 T+0：
https://www.sse.com.cn/assortment/fund/etf/question/c/c_20240118_5734755.shtml
（2026-10-02 核实）。不能仅凭 ETF 大类、名称或代码前缀授权当日卖出。
"""

from datetime import date


# 当前没有经过逐证券核实的 T+0 元数据，所有标的保守按 T+1。
# 将来仅在核实具体代码、交易所/基金合同依据及适用日期后扩展此白名单；
# 不从行情、LLM 推荐、订单 instrument_type 或普通请求参数注入例外。
VERIFIED_SAME_DAY_SELL_CODES: frozenset[str] = frozenset()


def is_lot_sellable(code: str, acquired_date: str, as_of_date: str) -> bool:
    """批次结算日检查，供持仓显示与成交事务共同使用。

    日期不明、非标准日期或未来买入批次拒绝卖出；交易时段检查由执行服务负责。
    这里的 T+1 表示不能在买入当日卖出，而不是授权非交易日成交。
    """
    try:
        acquired = date.fromisoformat(acquired_date)
        as_of = date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        return False
    if acquired.isoformat() != acquired_date or as_of.isoformat() != as_of_date:
        return False
    if acquired < as_of:
        return True
    return acquired == as_of and code in VERIFIED_SAME_DAY_SELL_CODES
