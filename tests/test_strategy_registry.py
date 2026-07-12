from src.strategy.registry import StrategyRegistry


def test_challenger_cannot_promote_with_small_sample(tmp_path):
    registry = StrategyRegistry(tmp_path)
    registry.register("v1", "champion", {"factor": 1})
    registry.register("v2", "challenger", {"factor": 2})
    eligibility = registry.promotion_eligibility({
        "closed_trades": 12,
        "shadow_days": 20,
        "net_expectancy": 0.01,
        "profit_factor": 1.3,
        "max_drawdown_pct": 8,
        "outperforms_champion": True,
        "rule_violations": 0,
    })
    assert eligibility["eligible"] is False
    assert eligibility["checks"]["closed_trades"] is False


def test_qualified_challenger_atomically_replaces_champion(tmp_path):
    registry = StrategyRegistry(tmp_path)
    registry.register("v1", "champion", {"factor": 1})
    registry.register("v2", "challenger", {"factor": 2})
    promoted = registry.promote("v2", {
        "closed_trades": 100,
        "shadow_days": 20,
        "net_expectancy": 0.01,
        "profit_factor": 1.2,
        "max_drawdown_pct": 10,
        "outperforms_champion": True,
        "rule_violations": 0,
    })
    assert promoted["role"] == "champion"
    assert registry.get("v1")["role"] == "archived"

