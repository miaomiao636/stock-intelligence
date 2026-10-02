from copy import deepcopy

import pytest

from src.research.maturity import maturity_review
from tests.test_research_maturity import imported_store, observation


def evidence():
    return {"reference_trade_date": "2026-09-21", "target_trade_date": "2026-09-23",
            "factors": [{"trade_date": "2026-09-21", "adj_factor": 1.5},
                        {"trade_date": "2026-09-23", "adj_factor": 1.5}]}


def review(tmp_path, data):
    store, _, item_id = imported_store(tmp_path)
    return maturity_review(store.get_judgment(item_id), target="2026-09-23",
                           observation=data, as_of="2026-09-23T08:00:00.000000Z")


def test_factor_values_are_retained_not_only_provider_booleans(tmp_path):
    data = observation("600000", "2026-09-23", None)
    data["adjustment_evidence"] = {**evidence(), "unexpected": "not retained"}
    data["adjustment_evidence"]["factors"][0]["unexpected"] = "not retained"
    result = review(tmp_path, data)
    assert result["prediction_status"] == "correct"
    assert result["outcome_evidence"]["adjustment_evidence"] == evidence()


def test_excluded_corporate_action_still_keeps_auditable_evidence(tmp_path):
    data = observation("600000", "2026-09-23", None)
    data["adjustment_evidence"] = evidence()
    data["adjustment_evidence"]["factors"][1]["adj_factor"] = 2.0
    data["corporate_action_detected"] = True
    result = review(tmp_path, data)
    assert result["prediction_status"] == "inconclusive"
    assert result["outcome_evidence"]["close"] == 11
    assert result["outcome_evidence"]["adjustment_evidence"]["factors"][1]["adj_factor"] == 2
    assert result["observed_return_pct"] is None


@pytest.mark.parametrize("bad", [
    {"reference_trade_date": "2026-09-20"}, {"target_trade_date": "2026-09-24"},
    {"factors": []}, {"factors": [{"trade_date": "2026-09-21", "adj_factor": True}]},
    {"factors": [{"trade_date": "2026-09-21", "adj_factor": float("nan")}]},
])
def test_malformed_factors_cannot_hide_behind_checked_boolean(tmp_path, bad):
    data = observation("600000", "2026-09-23", None)
    data["adjustment_evidence"] = {**deepcopy(evidence()), **bad}
    result = review(tmp_path, data)
    assert result["prediction_status"] == "inconclusive"
    assert result["exclusion_reason"] == "price_adjustment_unverified"


def test_factor_change_cannot_be_hidden_by_unchanged_boolean(tmp_path):
    data = observation("600000", "2026-09-23", None)
    data["adjustment_evidence"] = evidence()
    data["adjustment_evidence"]["factors"][1]["adj_factor"] = 2
    result = review(tmp_path, data)
    assert result["prediction_status"] == "inconclusive"
    assert result["exclusion_reason"] == "price_adjustment_unverified"


@pytest.mark.parametrize("invalid_flag", [None, "false", 0, 1])
def test_unknown_adjustment_flag_is_retryable_not_confirmed_corporate_action(tmp_path, invalid_flag):
    data = observation("600000", "2026-09-23", None)
    data["corporate_action_detected"] = invalid_flag
    result = review(tmp_path, data)
    assert result["prediction_status"] == "inconclusive"
    assert result["exclusion_reason"] == "price_adjustment_unverified"


def test_extreme_positive_prices_do_not_crash_or_publish_infinite_returns(tmp_path):
    store, _, item_id = imported_store(tmp_path)
    item = store.get_judgment(item_id)
    item["forecast_spec"]["reference_price"] = 1e-308
    data = {**observation("600000", "2026-09-23", None), "close": 1e308}
    result = maturity_review(item, target="2026-09-23", observation=data,
                             as_of="2026-09-23T08:00:00.000000Z")
    assert result["prediction_status"] == "inconclusive"
    assert result["exclusion_reason"] == "invalid_return"
    assert result["observed_return_pct"] is None
