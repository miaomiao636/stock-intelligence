# -*- coding: utf-8 -*-
"""13:15盘中复核流程回归测试。"""

from copy import deepcopy

from src.analysis import afternoon_pipeline


def _morning_report():
    return {
        "date": "2026-07-20",
        "type": "morning",
        "market_regime": "range",
        "sector_recommendations": [{"sector_name": "银行", "rating": 3}],
        "stock_recommendations": [{
            "code": "000001",
            "name": "平安银行",
            "sector": "银行",
            "action": "track",
            "confidence": 3,
            "horizon": "short",
            "horizon_days": 3,
            "current_price": 10.0,
            "entry_price": 9.8,
            "target_price": 10.5,
            "stop_loss_price": 9.5,
            "reason": "上午观点",
        }],
    }


def _patch_common(monkeypatch, tmp_path, llm_result):
    morning = _morning_report()
    original = deepcopy(morning)
    monkeypatch.setattr(afternoon_pipeline, "is_trading_day", lambda *_: True)
    monkeypatch.setattr(
        afternoon_pipeline,
        "load_report",
        lambda _, report_type: morning if report_type == "morning" else None,
    )
    monkeypatch.setattr(
        afternoon_pipeline,
        "get_realtime_market_overview",
        lambda: {"indices": {"000001": {"name": "上证指数", "close": 3800, "change_pct": 0.5}}},
    )
    monkeypatch.setattr(
        afternoon_pipeline,
        "fetch_realtime_prices",
        lambda _: {"000001": {"price": 10.2, "change_pct": 1.0}},
    )
    monkeypatch.setattr(afternoon_pipeline, "get_market_news", lambda: [])
    monkeypatch.setattr(afternoon_pipeline, "get_policy_news", lambda: [])

    class FakeTradingService:
        def get_account(self):
            return {"cash": 4000, "total_equity": 4000}

        def get_positions(self, _):
            return []

    monkeypatch.setattr(
        "src.paper_trading.trading_service.TradingService", FakeTradingService
    )
    monkeypatch.setattr(
        afternoon_pipeline.Synthesizer,
        "analyze_afternoon",
        lambda self, **_: llm_result,
    )
    monkeypatch.setattr(afternoon_pipeline, "save_report", lambda *_: tmp_path / "afternoon.json")
    monkeypatch.setattr(afternoon_pipeline, "save_run_artifact", lambda **_: tmp_path)
    return morning, original


def test_afternoon_pipeline_uses_live_price_and_never_trades(monkeypatch, tmp_path):
    morning, original = _patch_common(monkeypatch, tmp_path, {
        "status": "success",
        "source": "llm",
        "diagnostics": [],
        "data": {
            "sector_recommendations": [{"sector_name": "银行", "rating": 4}],
            "stock_recommendations": [{
                "code": "000001",
                "name": "平安银行",
                "action": "setup_ready",
                "confidence": 4,
                "reason": "下午量价改善，但仍只生成研究复核。",
            }],
        },
    })

    result = afternoon_pipeline.run_afternoon_pipeline(
        dry_run=True, force=True, date_str="2026-07-20"
    )

    stock = result["report"]["stock_recommendations"][0]
    assert result["status"] == "success"
    assert result["auto_trade"]["summary"]["total_trades"] == 0
    assert stock["current_price"] == 10.2
    assert stock["morning_price"] == 10.0
    assert stock["afternoon_decision"] == "upgrade"
    assert stock["trade_eligible"] is False
    assert morning == original


def test_afternoon_fallback_is_observation_only(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path, {
        "status": "fallback",
        "source": "afternoon_deterministic_fallback",
        "diagnostics": [],
        "data": {},
    })

    result = afternoon_pipeline.run_afternoon_pipeline(
        dry_run=True, force=True, date_str="2026-07-20"
    )

    stock = result["report"]["stock_recommendations"][0]
    assert result["status"] == "partial_success"
    assert result["report"]["analysis_degraded"] is True
    assert stock["trade_eligible"] is False
    assert stock["afternoon_decision"] == "maintain"
    assert "禁止自动交易" in stock["degraded_reason"]


def test_afternoon_requires_same_day_morning_report(monkeypatch):
    monkeypatch.setattr(afternoon_pipeline, "is_trading_day", lambda *_: True)
    monkeypatch.setattr(afternoon_pipeline, "load_report", lambda *_: None)

    result = afternoon_pipeline.run_afternoon_pipeline(
        dry_run=True, date_str="2026-07-20"
    )

    assert result["status"] == "error"
    assert "盘前报告" in result["reason"]
