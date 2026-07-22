# -*- coding: utf-8 -*-

from src.analysis.price_guard import reconcile_recommendation_prices


def test_price_guard_replaces_model_prices_with_verified_quote():
    recommendations = [{
        "code": "601012",
        "name": "隆基绿能",
        "action": "setup_ready",
        "current_price": 18.5,
        "entry_price": 18.0,
        "target_price": 19.5,
        "stop_loss_price": 17.5,
        "target_return_pct": 5,
        "stop_loss_pct": -3,
    }]
    quotes = {
        "601012": {
            "price": 12.57,
            "quote_time": "2026-07-22T09:35:00+08:00",
            "source": "tencent",
        }
    }

    result = reconcile_recommendation_prices(
        recommendations,
        quotes,
        eligible_codes=["601012"],
        max_price=50,
    )

    stock = recommendations[0]
    assert result["status"] == "ok_1"
    assert result["corrected"] == 1
    assert stock["current_price"] == 12.57
    assert stock["entry_price"] != 18.0
    assert stock["target_price"] != 19.5
    assert stock["stop_loss_price"] != 17.5
    assert stock["price_validation"]["verified"] is True
    assert stock["trade_eligible"] is True


def test_price_guard_blocks_missing_quote_and_clears_model_prices():
    recommendations = [{
        "code": "601012",
        "action": "setup_ready",
        "current_price": 18.5,
        "entry_price": 18.0,
        "target_price": 19.5,
        "stop_loss_price": 17.5,
    }]

    result = reconcile_recommendation_prices(
        recommendations,
        quotes={},
        eligible_codes=["601012"],
        max_price=50,
    )

    stock = recommendations[0]
    assert result["status"] == "error"
    assert stock["action"] == "watch"
    assert stock["trade_eligible"] is False
    assert stock["current_price"] is None
    assert stock["entry_price"] is None
    assert stock["target_price"] is None
    assert stock["stop_loss_price"] is None


def test_price_guard_blocks_stocks_outside_candidate_pool_or_price_limit():
    recommendations = [{
        "code": "600000",
        "action": "setup_ready",
        "current_price": 45,
        "entry_price": 44,
        "target_return_pct": 5,
        "stop_loss_pct": -3,
    }]
    quotes = {
        "600000": {"price": 55, "quote_time": "2026-07-22T09:35:00+08:00", "source": "tencent"}
    }

    result = reconcile_recommendation_prices(
        recommendations,
        quotes,
        eligible_codes=["000001"],
        max_price=50,
    )

    stock = recommendations[0]
    assert result["status"] == "ok_1"
    assert stock["action"] == "track"
    assert stock["trade_eligible"] is False
    assert set(stock["price_validation"]["restrictions"]) == {
        "not_in_candidate_universe",
        "over_max_price",
    }
