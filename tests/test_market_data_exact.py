# -*- coding: utf-8 -*-
"""精确收盘行情与实时回退的单元测试。"""

from datetime import date

from src.data_collectors import market_data


def _final_quote(code: str, target_date: str):
    return {
        "code": code,
        "date": target_date,
        "close": 10.5,
        "open": 10.0,
        "high": 10.6,
        "low": 9.9,
        "prev_close": 10.0,
        "change_pct": 5.0,
        "quote_time": f"{target_date}T15:00:03",
        "_source": "tencent_realtime",
    }


def test_index_market_mapping_distinguishes_shanghai_and_shenzhen():
    assert market_data._index_code_to_secid("000300") == "1.000300"
    assert market_data._index_code_to_secid("000001") == "1.000001"
    assert market_data._index_code_to_secid("399001") == "0.399001"
    assert market_data._index_code_to_symbol("000300") == "sh000300"
    assert market_data._index_code_to_symbol("399006") == "sz399006"


def test_current_day_index_can_use_final_tencent_quote(monkeypatch):
    target = date.today().isoformat()
    monkeypatch.setattr(
        market_data,
        "_try_tencent_index_quote",
        lambda code: _final_quote(code, target),
    )

    result = market_data._try_tencent_index_on("000300", target)

    assert result["date"] == target
    assert result["_source"] == "tencent_index_final"


def test_intraday_quote_is_not_accepted_as_closing_data(monkeypatch):
    target = date.today().isoformat()
    quote = _final_quote("000300", target)
    quote["quote_time"] = f"{target}T13:15:00"
    monkeypatch.setattr(market_data, "_try_tencent_index_quote", lambda _: quote)

    result = market_data._try_tencent_index_on("000300", target)

    assert result["error"] == "tencent_target_index_not_final"


def test_historical_date_never_fetches_realtime_quote(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "_try_tencent_index_quote",
        lambda _: (_ for _ in ()).throw(AssertionError("不应请求实时行情")),
    )

    result = market_data._try_tencent_index_on("000300", "2026-07-10")

    assert result["error"] == "tencent_realtime_not_allowed_for_historical_date"


def test_etf_current_day_uses_shanghai_tencent_symbol(monkeypatch):
    target = date.today().isoformat()
    captured = {}

    def fake_fetch(symbol, code):
        captured["symbol"] = symbol
        return _final_quote(code, target)

    monkeypatch.setattr(market_data, "_fetch_tencent_quote", fake_fetch)

    result = market_data._try_tencent_stock_on("516150", target)

    assert captured["symbol"] == "sh516150"
    assert result["_source"] == "tencent_stock_final"


def test_exact_market_overview_reports_partial_errors(monkeypatch):
    target = "2026-07-20"

    def fake_index(code, _):
        if code == "399006":
            return {"error": "source_unavailable"}
        return _final_quote(code, target)

    monkeypatch.setattr(market_data, "get_index_data_on", fake_index)

    result = market_data.get_market_overview_on(target)

    assert set(result["indices"]) == {"000001", "399001"}
    assert result["errors"] == {"399006": "source_unavailable"}
    assert "error" not in result
