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


def test_universe_uses_configured_max_price():
    stocks = [
        {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行", "list_date": "19910403"},
        {"ts_code": "600000.SH", "symbol": "600000", "name": "浦发银行", "industry": "银行", "list_date": "19991110"},
    ]
    daily = [
        {"ts_code": "000001.SZ", "close": 10, "pre_close": 9.9, "pct_chg": 1, "amount": 100000},
        {"ts_code": "600000.SH", "close": 45, "pre_close": 44, "pct_chg": 1, "amount": 100000},
    ]
    basics = [
        {"ts_code": code, "total_mv": 500000, "turnover_rate": 2}
        for code in ("000001.SZ", "600000.SH")
    ]

    low_cap = filter_and_rank_candidates(stocks, daily, basics, "2026-07-10", max_price=20)
    high_cap = filter_and_rank_candidates(stocks, daily, basics, "2026-07-10", max_price=50)

    assert {item["code"] for item in low_cap} == {"000001"}
    assert {item["code"] for item in high_cap} == {"000001", "600000"}


def test_universe_cache_avoids_repeated_rate_limited_calls(tmp_path, monkeypatch):
    from src.data_collectors.universe import get_ranked_candidates

    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")

    class Frame:
        def __init__(self, records):
            self.records = records
        def to_dict(self, orient):
            assert orient == "records"
            return self.records

    class Pro:
        calls = 0
        def stock_basic(self, **kwargs):
            self.calls += 1
            return Frame([{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行", "list_date": "19910403"}])
        def daily(self, **kwargs):
            self.calls += 1
            return Frame([{"ts_code": "000001.SZ", "close": 10, "pre_close": 9.9, "pct_chg": 1, "amount": 100000}])
        def daily_basic(self, **kwargs):
            self.calls += 1
            return Frame([{"ts_code": "000001.SZ", "total_mv": 500000, "turnover_rate": 2}])

    pro = Pro()
    first = get_ranked_candidates("2026-07-10", cache_dir=tmp_path, pro_client=pro)
    second = get_ranked_candidates("2026-07-10", cache_dir=tmp_path, pro_client=pro)
    assert first == second
    assert pro.calls == 3


def test_universe_retries_transient_fetch_failure(tmp_path, monkeypatch):
    from src.data_collectors import universe

    calls = {"count": 0}

    class Frame:
        def to_dict(self, orient):
            assert orient == "records"
            return [{"value": "ok"}]

    def fetcher():
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary timeout")
        return Frame()

    monkeypatch.setattr(universe.time, "sleep", lambda _: None)
    result = universe._load_or_fetch(tmp_path / "retry.json", fetcher, attempts=3)

    assert result == [{"value": "ok"}]
    assert calls["count"] == 3


def test_universe_waits_across_tushare_rate_limit_window(tmp_path, monkeypatch):
    from src.data_collectors import universe

    sleeps = []
    calls = {"count": 0}

    class Frame:
        def to_dict(self, orient):
            assert orient == "records"
            return [{"value": "ok"}]

    def fetcher():
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("抱歉，您访问接口频率超限(1次/分钟)")
        return Frame()

    monkeypatch.setattr(universe.time, "sleep", sleeps.append)
    result = universe._load_or_fetch(tmp_path / "rate-limit.json", fetcher, attempts=3)

    assert result == [{"value": "ok"}]
    assert sleeps == [31.0, 31.0]
