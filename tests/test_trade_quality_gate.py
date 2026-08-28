import pytest

from src.paper_trading.quality_gate import (
    build_execution_levels,
    calculate_net_reward_risk,
    entry_trigger_error,
)


CONFIG = {
    "entry_trigger_tolerance_pct": 0.005,
    "entry_max_gap_below_pct": 0.01,
    "min_stop_loss_pct": 0.02,
    "max_stop_loss_pct": 0.05,
    "min_gross_reward_risk_ratio": 2.0,
    "min_net_reward_risk_ratio": 1.5,
    "max_target_return_pct": 0.15,
}


def test_entry_trigger_requires_the_planned_price_zone():
    stock = {"entry_price": 10.0}

    assert entry_trigger_error(stock, 10.04, CONFIG) is None
    assert "尚未进入计划价" in entry_trigger_error(stock, 10.06, CONFIG)
    assert "重新评估" in entry_trigger_error(stock, 9.89, CONFIG)
    assert "缺少有效计划进场价" in entry_trigger_error({}, 10.0, CONFIG)


def test_execution_levels_raise_target_until_cost_adjusted_ratio_passes():
    levels = build_execution_levels(
        stock={"entry_price": 10.0, "stop_loss_pct": -3, "target_return_pct": 6},
        market_price=10.0,
        quantity=100,
        instrument_type="stock",
        config=CONFIG,
    )

    assert levels["eligible"] is True
    assert levels["stop_price"] == pytest.approx(9.7)
    assert levels["target_price"] > 10.6
    assert levels["gross_reward_risk_ratio"] >= 2.0
    assert levels["net_reward_risk_ratio"] >= 1.5

    metrics = calculate_net_reward_risk(
        entry_market_price=10.0,
        target_market_price=levels["target_price"],
        stop_market_price=levels["stop_price"],
        quantity=100,
        instrument_type="stock",
    )
    assert metrics["net_ratio"] >= 1.5
