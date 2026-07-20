# -*- coding: utf-8 -*-
"""盘后流程不得再调用旧 JSON AutoTrader。"""

from datetime import date

from src.evaluation import closing_pipeline


def test_closing_pipeline_uses_sqlite_workflow(monkeypatch):
    called = {"intraday": 0, "tracked": 0}

    class FakeEvaluator:
        def evaluate_recommendation(self, report, target_date):
            return {
                "status": "degraded",
                "quality": {"error_rate": 0},
                "stock_results": [],
                "metrics": {},
            }

        def save_evaluation(self, evaluation):
            return "unused"

    class FakeWorkflow:
        def intraday_check(self):
            called["intraday"] += 1
            return {
                "status": "success",
                "positions": 1,
                "alerts": ["测试退出计划已进入飞书5分钟窗口"],
                "metrics": {"max_drawdown_pct": 1.2},
            }

    class FakeTracker:
        def track_daily(self, target_date):
            called["tracked"] += 1
            return {"summary": {"win_rate_pct": 50.0}}

    monkeypatch.setattr(closing_pipeline, "is_trading_day", lambda _: True)
    monkeypatch.setattr(closing_pipeline, "load_report", lambda *_: {"date": date.today().isoformat()})
    monkeypatch.setattr(closing_pipeline, "get_market_overview_on", lambda _: {"indices": {}})
    monkeypatch.setattr(closing_pipeline, "Evaluator", FakeEvaluator)
    monkeypatch.setattr("src.reporting.report_store.save_report", lambda *_: None)
    monkeypatch.setattr("src.paper_trading.workflow.PaperTradingWorkflow", FakeWorkflow)
    monkeypatch.setattr("src.tracking.tracker.RecommendationTracker", FakeTracker)

    result = closing_pipeline.run_closing_pipeline(date_str=date.today().isoformat())

    assert called == {"intraday": 1, "tracked": 1}
    assert result["trading_status"] == "ok"
    assert result["exit_alerts"] == ["测试退出计划已进入飞书5分钟窗口"]
    assert result["auto_sell_trades"] == []


def test_historical_closing_run_has_no_live_trading_side_effect(monkeypatch):
    class FakeEvaluator:
        def evaluate_recommendation(self, report, target_date):
            return {
                "status": "error",
                "quality": {"error_rate": 1},
                "stock_results": [],
                "metrics": {},
            }

    monkeypatch.setattr(closing_pipeline, "is_trading_day", lambda _: True)
    monkeypatch.setattr(closing_pipeline, "load_report", lambda *_: {"date": "2026-07-10"})
    monkeypatch.setattr(closing_pipeline, "get_market_overview_on", lambda _: {"indices": {}})
    monkeypatch.setattr(closing_pipeline, "Evaluator", FakeEvaluator)
    monkeypatch.setattr("src.reporting.report_store.save_report", lambda *_: None)

    result = closing_pipeline.run_closing_pipeline(date_str="2026-07-10")

    assert result["source_status"]["position_update"] == "skipped_historical_run"
    assert result["source_status"]["tracking"] == "skipped_historical_run"


def test_benchmark_only_degradation_has_specific_warning(monkeypatch):
    class FakeEvaluator:
        def evaluate_recommendation(self, report, target_date):
            return {
                "status": "degraded",
                "quality": {"total": 3, "valid": 3, "error": 0, "error_rate": 0},
                "stock_results": [],
                "metrics": {},
                "benchmark_status": "error",
                "benchmark_error": "tencent_target_index_not_final",
            }

        def save_evaluation(self, evaluation):
            return "unused"

    monkeypatch.setattr(closing_pipeline, "is_trading_day", lambda _: True)
    monkeypatch.setattr(closing_pipeline, "load_report", lambda *_: {"date": "2026-07-10"})
    monkeypatch.setattr(closing_pipeline, "get_market_overview_on", lambda _: {"indices": {}})
    monkeypatch.setattr(closing_pipeline, "Evaluator", FakeEvaluator)
    monkeypatch.setattr("src.reporting.report_store.save_report", lambda *_: None)

    result = closing_pipeline.run_closing_pipeline(date_str="2026-07-10")

    assert result["warnings"] == [
        "沪深300基准数据不可用: tencent_target_index_not_final"
    ]
    assert all("error_rate=0.0%" not in warning for warning in result["warnings"])
