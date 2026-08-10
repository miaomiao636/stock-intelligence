# -*- coding: utf-8 -*-

from src.evaluation.metrics import calculate_metrics, calculate_win_rate


def test_win_rate_excludes_active_and_exposes_progress_separately():
    results = [
        {"status": "hit", "return_pct": 6.0},
        {"status": "stopped", "return_pct": -3.0},
        {"status": "near_target", "return_pct": 4.0},
        {"status": "active", "return_pct": -1.0},
    ]

    assert calculate_win_rate(results) == 50.0
    metrics = calculate_metrics(results)
    assert metrics["closed_recommendations"] == 2
    assert metrics["active_recommendations"] == 2
    assert metrics["sample_coverage_pct"] == 50.0
    assert metrics["win_rate_pct"] == 50.0
    assert metrics["progress_score_pct"] != metrics["win_rate_pct"]
    assert metrics["valid_recommendations"] == 4
    assert metrics["winning_recommendations"] == 1
    assert metrics["losing_recommendations"] == 1
    assert metrics["return_basis"] == "recommendation_entry_to_close"
