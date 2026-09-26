import pytest

from src.research.experiments import compare_net_baselines, evaluate_experiment, factor_rank_ic, walk_forward_splits


def rows():
    return [{"stock_code": code, "as_of": "2026-09-01T08:45:00+08:00", "available_at": "2026-08-31T18:00:00+08:00", "entry_at": "2026-09-01T09:35:00+08:00", "exit_at": "2026-09-02T09:35:00+08:00", "label_known_at": "2026-09-02T09:36:00+08:00", "can_execute": True, "cost_bps": 20, "benchmark_return": 0.005, "factor_value": factor, "forward_return": ret, "data_version": "fixture-v1", "strategy_version": "s1"} for code, factor, ret in [("600000", 1, -0.01), ("600001", 2, 0.01), ("600002", 3, 0.02)]]


def test_walk_forward_has_gaps_and_no_future_training():
    folds = walk_forward_splits([f"2026-09-{day:02d}" for day in range(1, 15)], train_size=4, validation_size=2, test_size=2, gap=1)
    assert folds[0] == {"train": ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"], "validation": ["2026-09-06", "2026-09-07"], "test": ["2026-09-09", "2026-09-10"]}
    assert all(max(fold["train"]) < min(fold["test"]) for fold in folds)


def test_rank_ic_and_net_cost_baseline_are_deterministic():
    assert factor_rank_ic(rows())["mean_rank_ic"] == pytest.approx(1.0)
    result = compare_net_baselines(rows(), top_k=1)
    assert result["factor"]["net_return"] == pytest.approx(0.018)
    assert result["factor"]["excess_return"] == pytest.approx(0.013)
    assert result["equal_weight"]["net_return"] == pytest.approx((-.01 + .01 + .02) / 3 - .002)
    assert result["auto_promoted"] is False


def test_unexecutable_candidate_stays_cash_and_is_not_filled_at_assumed_price():
    sample = rows()
    sample[2]["can_execute"] = False
    result = compare_net_baselines(sample, top_k=1)
    assert result["factor"]["net_return"] == 0
    assert result["factor"]["unexecutable_count"] == 1


@pytest.mark.parametrize("field", ["as_of", "can_execute", "cost_bps", "benchmark_return", "data_version", "available_at", "label_known_at"])
def test_missing_required_data_blocks_experiment(field):
    sample = rows()
    sample[0].pop(field)
    result = evaluate_experiment({"observations": sample, "name": "candidate", "protocol_version": "p1"})
    assert result["status"] == "blocked"
    assert field in " ".join(result["errors"])
    assert result["auto_promoted"] is False


def test_future_features_or_overlapping_returns_rejected():
    sample = rows()
    sample[0]["available_at"] = "2026-09-02"
    with pytest.raises(ValueError, match="future"):
        factor_rank_ic(sample)
    sample = rows()
    other = [{**row, "as_of": "2026-09-02T08:45:00+08:00", "entry_at": "2026-09-02T09:30:00+08:00", "exit_at": "2026-09-03T09:35:00+08:00", "label_known_at": "2026-09-03T10:00:00+08:00"} for row in rows()]
    with pytest.raises(ValueError, match="overlap"):
        compare_net_baselines(sample + other)


def test_sample_metrics_do_not_claim_validated_model():
    result = evaluate_experiment({"observations": rows(), "name": "candidate", "protocol_version": "p1"})
    assert result["status"] == "completed"
    assert result["validation_status"] == "descriptive_only"
    assert result["auto_promoted"] is False
    assert result["warnings"]
