from src.data_collectors.universe import filter_and_rank_candidates


def test_universe_applies_point_in_time_hard_filters():
    stocks = [
        {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行", "list_date": "19910403"},
        {"ts_code": "300001.SZ", "symbol": "300001", "name": "创业板", "industry": "科技", "list_date": "20091030"},
        {"ts_code": "600001.SH", "symbol": "600001", "name": "ST测试", "industry": "工业", "list_date": "20000101"},
        {"ts_code": "000002.SZ", "symbol": "000002", "name": "低流动", "industry": "地产", "list_date": "19910101"},
    ]
    daily = [
        {"ts_code": "000001.SZ", "close": 10, "pre_close": 9.9, "pct_chg": 1, "amount": 100000},
        {"ts_code": "300001.SZ", "close": 10, "pre_close": 10, "pct_chg": 1, "amount": 100000},
        {"ts_code": "600001.SH", "close": 5, "pre_close": 5, "pct_chg": 1, "amount": 100000},
        {"ts_code": "000002.SZ", "close": 8, "pre_close": 8, "pct_chg": 1, "amount": 1000},
    ]
    basics = [
        {"ts_code": code, "total_mv": 500000, "turnover_rate": 2}
        for code in ("000001.SZ", "300001.SZ", "600001.SH", "000002.SZ")
    ]
    result = filter_and_rank_candidates(stocks, daily, basics, "2026-07-10")
    assert [item["code"] for item in result] == ["000001"]

