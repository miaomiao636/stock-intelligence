# -*- coding: utf-8 -*-
"""历史评估必须使用目标交易日数据，且不得混入目标日之后的数据。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.evaluation import evaluator as evaluator_module
from src.evaluation.evaluator import Evaluator
from src.evaluation.leak_guard import validate_no_future_data
from src.paper_trading.trading_service import TradingService


TZ = ZoneInfo("Asia/Shanghai")


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


def test_missing_entry_price_is_not_replaced_by_daily_change(monkeypatch):
    stock = {
        "code": "000001",
        "name": "测试",
        "target_return_pct": 5.0,
        "stop_loss_pct": -3.0,
    }
    monkeypatch.setattr(
        evaluator_module,
        "get_stock_data_on",
        lambda *_: (_ for _ in ()).throw(AssertionError("missing entry must fail before price lookup")),
    )

    result = Evaluator()._evaluate_stock(stock, "2026-07-13")

    assert result["status"] == "error"
    assert result["return_basis"] == "missing_entry_price"
    assert "拒绝用当日涨跌幅" in result["evaluation_note"]


def test_untriggered_entry_is_a_valid_no_trade_result(monkeypatch, tmp_path):
    recommendation = _recommendation()
    recommendation["stock_recommendations"] = recommendation["stock_recommendations"][:1]
    monkeypatch.setattr(
        evaluator_module,
        "get_stock_data_on",
        lambda code, target_date: {
            "code": code,
            "date": target_date,
            "open": 10.4,
            "high": 10.5,
            "low": 10.2,
            "close": 10.3,
            "change_pct": 1.0,
        },
    )
    monkeypatch.setattr(
        evaluator_module,
        "get_index_data_on",
        lambda code, target_date: {"code": code, "date": target_date, "change_pct": 0.5},
    )

    result = Evaluator(tmp_path).evaluate_recommendation(recommendation, "2026-07-13")

    assert result["status"] == "success"
    assert result["quality"]["not_triggered"] == 1
    assert result["quality"]["valid"] == 0
    assert result["stock_results"][0]["status"] == "not_triggered"
    assert result["metrics"]["valid_recommendations"] == 0
    assert result["metrics"]["avg_return_pct"] == 0


def test_filled_order_is_evaluated_from_actual_fill_and_costs(monkeypatch, tmp_path):
    now = datetime(2026, 7, 13, 9, 35, tzinfo=TZ)
    service = TradingService(tmp_path, now_provider=lambda: now)
    service.initialize_account(20000)
    order = service.propose_order(
        run_id="actual-fill",
        recommendation_id="REC-ACTUAL-FILL",
        code="000001",
        name="测试",
        sector="测试",
        action="buy",
        quantity=100,
        planned_price=10.2,
        min_price=9.9,
        max_price=10.3,
        stop_price=9.8,
        target_price=11.4,
    )
    service.confirm_automatically(order["order_id"])
    filled = service.execute_ready_order(
        order["order_id"],
        {
            "code": "000001",
            "price": 10.2,
            "quote_time": now.isoformat(),
            "trade_date": now.date().isoformat(),
            "trade_status": "trading",
            "source_time_reliable": True,
        },
    )
    assert filled["success"] is True

    monkeypatch.setattr(
        evaluator_module,
        "get_stock_data_on",
        lambda code, target_date: {
            "code": code,
            "date": target_date,
            "open": 10.2,
            "high": 10.3,
            "low": 9.9,
            "close": 10.0,
            "change_pct": -1.0,
        },
    )
    recommendation = {
        "date": "2026-07-13",
        "type": "morning",
        "run_id": "actual-fill",
        "stock_recommendations": [{
            "recommendation_id": "REC-ACTUAL-FILL",
            "code": "000001",
            "name": "测试",
            "action": "setup_ready",
            "trade_eligible": True,
            "entry_price": 9.5,
            "stop_loss_price": 9.2,
            "target_price": 10.1,
            "stop_loss_pct": -3,
            "target_return_pct": 6,
        }],
    }

    result = Evaluator(tmp_path)._evaluate_stock(
        recommendation["stock_recommendations"][0],
        "2026-07-13",
        recommendation,
    )

    assert result["execution_status"] == "filled"
    assert result["entry_price"] == filled["trade"]["price"]
    assert result["planned_entry_price"] == 9.5
    assert result["return_pct"] < 0
    assert result["return_basis"] == "actual_fill_to_close_after_estimated_exit_costs"
    assert result["order_id"] == order["order_id"]


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
