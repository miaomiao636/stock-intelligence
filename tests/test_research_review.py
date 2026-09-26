import pytest

from src.research.review import normalize_review, review_prediction, timestamp


def test_timezone_and_date_only_cutoff():
    assert timestamp("2026-09-24T08:00:00+08:00") == timestamp("2026-09-24T00:00:00Z")
    assert timestamp("2026-09-24", end_of_day=True) == "2026-09-24T15:59:59.999999Z"


def test_prediction_direction_does_not_infer_realized_profit():
    assert review_prediction(predicted_direction="up", observed_return=.01, matured=True) == "correct"
    assert review_prediction(predicted_direction="up", observed_return=.01, matured=False) == "pending"
    assert review_prediction(predicted_direction="up", observed_return=.01, matured=True, history_complete=False) == "history_incomplete"
    assert review_prediction(predicted_direction="up", observed_return=None, matured=True) == "inconclusive"


def test_actual_pnl_requires_trade_references_and_cost_basis():
    base = {"as_of": timestamp("2026-09-24")}
    payload = {"as_of": "2026-09-26", "execution_status": "closed", "net_pnl_status": "realized", "net_pnl_after_costs": -12.5}
    with pytest.raises(ValueError, match="trade_ids"):
        normalize_review(payload, base)
    result = normalize_review({**payload, "trade_ids": ["buy", "sell"], "cost_basis": "ledger after entry and exit fees"}, base)
    assert result["net_pnl_after_costs"] == -12.5
