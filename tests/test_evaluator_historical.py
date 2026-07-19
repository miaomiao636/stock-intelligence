# -*- coding: utf-8 -*-
"""历史评估必须使用目标交易日数据，且不得混入目标日之后的数据。"""

from src.evaluation import evaluator as evaluator_module
from src.evaluation.evaluator import Evaluator
from src.evaluation.leak_guard import validate_no_future_data


def _recommendation():
    return {
        "date": "2026-07-10",
        "type": "morning",
        "created_at": "2026-07-10T08:30:00+08:00",
        "stock_recommendations": [
            {
                "code": f"00000{i}",
                "name": f"测试{i}",
                "timing": {"entry_price": 10.0},
                "target_return_pct": 5.0,
                "stop_loss_pct": -3.0,
            }
            for i in range(1, 4)
        ],
    }


def test_evaluator_uses_exact_target_date(monkeypatch):
    requested_dates = []

    def stock_on_date(code, target_date):
        requested_dates.append((code, target_date))
        return {
            "code": code,
            "date": target_date,
            "close": 10.6,
            "change_pct": 1.0,
        }

    monkeypatch.setattr(evaluator_module, "get_stock_data_on", stock_on_date)
    monkeypatch.setattr(
        evaluator_module,
        "get_index_data_on",
        lambda code, target_date: {
            "code": code,
            "date": target_date,
            "change_pct": 0.5,
        },
    )

    result = Evaluator().evaluate_recommendation(_recommendation(), "2026-07-13")

    assert result["status"] == "success"
    assert result["leak_guard"]["valid"] is True
    assert requested_dates == [
        ("000001", "2026-07-13"),
        ("000002", "2026-07-13"),
        ("000003", "2026-07-13"),
    ]
    assert {item["data_date"] for item in result["stock_results"]} == {"2026-07-13"}


def test_evaluator_fails_closed_when_source_returns_future_date(monkeypatch):
    monkeypatch.setattr(
        evaluator_module,
        "get_stock_data_on",
        lambda code, target_date: {
            "code": code,
            "date": "2026-07-14",
            "close": 10.6,
            "change_pct": 1.0,
        },
    )
    monkeypatch.setattr(
        evaluator_module,
        "get_index_data_on",
        lambda code, target_date: {
            "code": code,
            "date": target_date,
            "change_pct": 0.5,
        },
    )

    result = Evaluator().evaluate_recommendation(_recommendation(), "2026-07-13")

    assert result["status"] == "error"
    assert result["leak_guard"]["valid"] is False
    assert "晚于目标日期" in result["leak_guard"]["reason"]


def test_single_valid_recommendation_is_not_rejected_by_fixed_minimum(monkeypatch):
    recommendation = _recommendation()
    recommendation["stock_recommendations"] = recommendation["stock_recommendations"][:1]
    monkeypatch.setattr(
        evaluator_module,
        "get_stock_data_on",
        lambda code, target_date: {
            "code": code,
            "date": target_date,
            "close": 10.6,
            "change_pct": 1.0,
        },
    )
    monkeypatch.setattr(
        evaluator_module,
        "get_index_data_on",
        lambda code, target_date: {
            "code": code,
            "date": target_date,
            "change_pct": 0.5,
        },
    )

    result = Evaluator().evaluate_recommendation(recommendation, "2026-07-13")

    assert result["status"] == "success"
    assert result["quality"]["min_valid_required"] == 1


def test_leak_guard_rejects_evaluation_before_recommendation():
    result = validate_no_future_data(
        _recommendation(),
        {
            "date": "2026-07-09",
            "evaluated_at": "2026-07-13T16:00:00+08:00",
            "stock_results": [],
        },
    )

    assert result["valid"] is False
    assert "早于推荐日期" in result["reason"]
