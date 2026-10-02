"""Real producer -> archive tests; no model, market network or live ledger."""

from datetime import date, datetime

from src.reporting import formatter
from src.research.store import ResearchStore


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime.fromisoformat("2026-09-21T10:03:00+08:00")
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def quote(price, at="2026-09-21T10:00:00+08:00"):
    return {"code": "600000", "price": price, "quote_time": at,
            "source_time_reliable": True, "source": "synthetic"}


def recommendation():
    return {"code": "600000", "name": "合成样本", "reason": "Explicit upward forecast",
            "action": "setup_ready", "confidence": 4, "current_price": 10,
            "entry_price": 9.8, "target_price": 11, "stop_loss_price": 9.5,
            "forecast_direction": "up", "forecast_horizon_sessions": 2}


def archive(tmp_path, report):
    store = ResearchStore(tmp_path / "synthetic.db",
                          clock=lambda: "2026-09-21T10:04:00+08:00")
    result = store.import_report(report, "synthetic:input-snapshot")
    return store, store.get_judgment(result["judgment_ids"][0])


def test_morning_forecast_keeps_model_input_while_trade_plan_uses_refreshed_quote(tmp_path, monkeypatch):
    from src import orchestrator
    from src.data_collectors import realtime_prices, universe

    monkeypatch.setattr(formatter, "datetime", FrozenDatetime)
    monkeypatch.setattr(orchestrator, "is_trading_day", lambda *_: True)
    monkeypatch.setattr(orchestrator, "get_previous_trading_day", lambda *_: date(2026, 9, 18))
    monkeypatch.setattr(orchestrator, "load_report", lambda *_: None)
    monkeypatch.setattr(orchestrator, "get_market_overview", lambda: {"indices": {}})
    monkeypatch.setattr(orchestrator, "get_market_news", lambda: [])
    monkeypatch.setattr(orchestrator, "get_policy_news", lambda: [])
    monkeypatch.setattr(universe, "get_ranked_candidates", lambda *_, **__: [
        {"code": "600000", "sector": "test", "daily_amount": 10000000, "market_cap": 100000000}])
    prices = iter([{"600000": quote(10)}, {"600000": quote(12, "2026-09-21T10:02:00+08:00")}])
    monkeypatch.setattr(realtime_prices, "fetch_realtime_prices", lambda *_: next(prices))

    class FakeTradingService:
        def get_account(self):
            return {"cash": 20000, "total_equity": 20000}

    class FakeSynthesizer:
        def analyze(self, **kwargs):
            assert kwargs["market_data"]["realtime_stock_prices"]["600000"]["price"] == 10
            return {"status": "success", "source": "synthetic", "data": {
                "stock_recommendations": [recommendation()], "sector_recommendations": []}}

    monkeypatch.setattr("src.paper_trading.trading_service.TradingService", FakeTradingService)
    monkeypatch.setattr(orchestrator, "Synthesizer", FakeSynthesizer)
    report = orchestrator.run_morning_pipeline(dry_run=True, date_str="2026-09-21")["report"]
    assert report["stock_recommendations"][0]["current_price"] == 12
    assert report["price_validation"]["verified"] == 1
    store, item = archive(tmp_path, report)
    assert item["forecast_spec"]["status"] == "registered"
    assert item["forecast_spec"]["reference_price"] == 10
    assert item["forecast_spec"]["reference_as_of"] == "2026-09-21T02:00:00.000000Z"
    assert item["forecast_spec"]["reference_basis"] == "model_input_snapshot"
    reference = store.get_evidence(item["forecast_spec"]["reference_evidence_id"])
    assert reference["purpose"] == "forecast_input"
    assert reference["snapshot"]["realtime_stock_prices"]["600000"]["price"] == 10
    assert store.get_prediction_scorecard()["summary"]["eligible"] == 1


def test_formatter_without_explicit_input_does_not_relabel_final_quote_as_input(tmp_path, monkeypatch):
    monkeypatch.setattr(formatter, "datetime", FrozenDatetime)
    report = formatter.format_json_report("2026-09-21", "morning",
        {"realtime_stock_prices": {"600000": quote(12)}}, [], [], [recommendation()])
    _, item = archive(tmp_path, report)
    assert item["forecast_spec"]["status"] == "excluded"
    assert item["forecast_spec"]["reason"] == "forecast_input_snapshot_missing"


def test_legacy_unattributed_snapshot_is_archived_but_not_scored(tmp_path):
    from tests.test_research_maturity import synthetic_report
    report = synthetic_report()
    for evidence in report["evidence_snapshots"]:
        evidence.pop("purpose", None)
    _, item = archive(tmp_path, report)
    assert item["forecast_spec"]["status"] == "excluded"
    assert item["forecast_spec"]["reason"] == "forecast_input_snapshot_missing"


def test_formatter_freezes_input_even_if_caller_mutates_market_later(tmp_path, monkeypatch):
    monkeypatch.setattr(formatter, "datetime", FrozenDatetime)
    input_market = {"realtime_stock_prices": {"600000": quote(10)}}
    report = formatter.format_json_report("2026-09-21", "morning",
        {"realtime_stock_prices": {"600000": quote(12)}}, [], [], [recommendation()],
        forecast_input_market_data=input_market)
    input_market["realtime_stock_prices"]["600000"]["price"] = 99
    _, item = archive(tmp_path, report)
    assert item["forecast_spec"]["reference_price"] == 10


def test_unknown_publication_still_excludes_entire_report_not_silently_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(formatter, "datetime", FrozenDatetime)
    market = {"realtime_stock_prices": {"600000": quote(10)}}
    news = [{"title": "Unknown publication fixture", "source": "synthetic",
             "published_at": None, "available_at": "2026-09-21T10:01:00+08:00",
             "fetched_at": "2026-09-21T10:01:00+08:00", "event_eligible": False}]
    report = formatter.format_json_report("2026-09-21", "morning", market, news, [],
        [recommendation()], forecast_input_market_data=market)
    store, item = archive(tmp_path, report)
    assert item["history_incomplete"] is True
    card = store.get_prediction_scorecard()
    assert card["summary"]["eligible"] == 0
    assert card["exclusion_reasons"] == {"history_incomplete": 1}


def test_afternoon_records_its_own_model_input_not_morning_reference(tmp_path, monkeypatch):
    from src.analysis import afternoon_pipeline
    from tests.test_afternoon_pipeline import _patch_common

    monkeypatch.setattr(formatter, "datetime", FrozenDatetime)
    _patch_common(monkeypatch, tmp_path, {"status": "success", "source": "synthetic",
        "data": {"stock_recommendations": [{**recommendation(), "code": "000001"}]}})
    monkeypatch.setattr(afternoon_pipeline, "fetch_realtime_prices", lambda *_: {
        "000001": {**quote(10.5), "code": "000001"}})
    report = afternoon_pipeline.run_afternoon_pipeline(dry_run=True, force=True,
                                                       date_str="2026-09-21")["report"]
    _, item = archive(tmp_path, report)
    assert item["forecast_spec"]["status"] == "registered"
    assert item["forecast_spec"]["reference_price"] == 10.5
    assert item["forecast_spec"]["reference_basis"] == "model_input_snapshot"
