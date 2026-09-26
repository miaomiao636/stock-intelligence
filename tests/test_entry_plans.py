from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.paper_trading.workflow import PaperTradingWorkflow

TZ = ZoneInfo("Asia/Shanghai")
SOURCES = {"llm": "success", "candidate_universe": "ok_50", "recommendation_prices": "ok_1"}


class SilentNotifier:
    def send_message(self, *args):
        raise AssertionError("Rechecks must not send notifications by default")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    monkeypatch.setattr("src.paper_trading.workflow.is_trading_day", lambda d: d.weekday() < 5, raising=False)
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ), "price": 10.2, "age": 0}
    def quotes(codes):
        return {code: {"code": code, "price": clock["price"], "trade_status": "trading",
                       "source_time_reliable": True, "trade_date": clock["now"].date().isoformat(),
                       "quote_time": (clock["now"] - timedelta(seconds=clock["age"])).isoformat()}
                for code in codes}
    flow = PaperTradingWorkflow(tmp_path, notifier=SilentNotifier(), quote_fetcher=quotes,
                                now_provider=lambda: clock["now"])
    flow.service.initialize_account(20000)
    report = {"date": "2026-07-13", "run_id": "2026-07-13-morning", "market_regime": "bullish",
              "source_status": SOURCES, "stock_recommendations": [{"code": "000001", "name": "平安银行",
                "sector": "金融", "recommendation_id": "REC-WAIT", "action": "setup_ready",
                "trade_eligible": True, "confidence": 4, "price_validation": {"verified": True},
                "entry_price": 10, "stop_loss_price": 9.7, "target_price": 10.9, "horizon": "short"}]}
    return flow, clock, report, quotes


def test_waiting_entry_survives_restart_fills_on_intraday_and_cannot_duplicate(setup, tmp_path):
    flow, clock, report, quotes = setup
    flow.prepare_final_orders(report, SOURCES)
    assert flow.entry_plans.list_plans()[0]["status"] == "waiting_trigger"
    clock.update(now=datetime(2026, 7, 13, 10, 5, tzinfo=TZ), price=10)
    restarted = PaperTradingWorkflow(tmp_path, notifier=SilentNotifier(), quote_fetcher=quotes,
                                     now_provider=lambda: clock["now"])
    result = restarted.intraday_check(report=report, source_status=SOURCES, notify=False)
    assert result["entry_recheck"]["orders"][0]["status"] == "filled"
    assert restarted.entry_plans.list_plans()[0]["status"] == "filled"
    restarted.recheck_entry_plans(report=report, source_status=SOURCES)
    assert len(restarted.service.ledger.list_trades()) == 1


@pytest.mark.parametrize("condition", ["expired", "degraded", "bearish", "stale_quote", "disabled", "holiday", "stale_report", "missing_report"])
def test_recheck_rejects_unsafe_context_and_records_reason(setup, monkeypatch, condition):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    clock["price"] = 10
    sources = dict(SOURCES)
    if condition == "expired": clock["now"] += timedelta(days=1)
    if condition == "degraded": sources["llm"] = "fallback"
    if condition == "bearish": report["market_regime"] = "bearish"
    if condition == "stale_quote": clock["age"] = 120
    if condition == "disabled": monkeypatch.setenv("PAPER_TRADING_ENABLED", "false")
    if condition == "holiday": monkeypatch.setattr("src.paper_trading.workflow.is_trading_day", lambda _: False)
    if condition == "stale_report": report["date"] = "2026-07-10"
    result = flow.recheck_entry_plans(report=None if condition == "missing_report" else report, source_status=sources)
    assert result.get("orders", []) == []
    assert flow.service.ledger.list_trades() == []
    assert flow.entry_plans.list_plans()[0]["reason"]


def test_recheck_reuses_daily_limit_and_keeps_original_plan_identity(setup):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    plan_id = flow.entry_plans.list_plans()[0]["plan_id"]
    clock["price"] = 10
    report["run_id"] = "2026-07-13-afternoon"
    report["stock_recommendations"][0]["recommendation_id"] = "REC-AFTERNOON"
    result = flow.recheck_entry_plans(report=report, source_status=SOURCES)
    assert len(result["orders"]) == 1
    assert flow.entry_plans.list_plans()[0]["plan_id"] == plan_id
    assert result["orders"][0]["recommendation_id"] == "REC-WAIT"


def test_holding_deadline_uses_trading_days_not_weekend_calendar_days(setup, monkeypatch):
    flow, clock, report, _ = setup
    clock["price"] = 10
    flow.prepare_final_orders(report, SOURCES, notify=False)
    clock["now"] = datetime(2026, 7, 20, 10, 5, tzinfo=TZ)
    monkeypatch.setattr("src.paper_trading.workflow.count_trading_days", lambda start, end: 4, raising=False)
    flow.intraday_check(notify=False)
    assert len(flow.service.get_positions()) == 1


def test_lunch_scan_does_not_destroy_a_same_day_waiting_plan(setup):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    clock["now"] = datetime(2026, 7, 13, 12, 5, tzinfo=TZ)
    assert flow.recheck_entry_plans(report, SOURCES)["orders"] == []
    assert flow.entry_plans.list_plans()[0]["status"] == "waiting_trigger"
    clock.update(now=datetime(2026, 7, 13, 13, 5, tzinfo=TZ), price=10)
    assert len(flow.recheck_entry_plans(report, SOURCES)["orders"]) == 1


def test_changed_research_target_cannot_execute_an_obsolete_waiting_plan(setup):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    clock["price"] = 10
    report["stock_recommendations"][0]["target_price"] = 10.3
    result = flow.recheck_entry_plans(report, SOURCES)
    assert result["orders"] == []
    assert flow.entry_plans.list_plans()[0]["status"] == "rejected"


def test_waiting_plans_do_not_bypass_two_new_positions_daily_limit(setup):
    flow, clock, report, _ = setup
    first = report["stock_recommendations"][0]
    report["stock_recommendations"] = [dict(first, code=code, sector=sector, recommendation_id=f"REC-{code}")
                                       for code, sector in [("000001", "金融"), ("600002", "煤炭"), ("600003", "医药")]]
    flow.prepare_final_orders(report, SOURCES)
    clock["price"] = 10
    result = flow.recheck_entry_plans(report, SOURCES)
    assert len(result["orders"]) == 2
    flow.recheck_entry_plans(report, SOURCES)
    assert len(flow.service.ledger.list_trades()) == 2
    assert flow.entry_plans.get_summary()["rejected"] == 1


def test_entry_plans_read_on_old_database_creates_no_table(setup):
    flow, _, _, _ = setup
    assert flow.entry_plans.list_plans() == []
    assert flow.entry_plans.get_summary()["total"] == 0
    with flow.service.ledger.connect() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='paper_entry_plans'").fetchone() is None


@pytest.mark.parametrize("decision,expected", [("maintain", 1), ("upgrade", 1), ("downgrade", 0), ("cancel", 0)])
def test_afternoon_advisory_can_only_revalidate_existing_morning_authority(setup, decision, expected):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    clock.update(now=datetime(2026, 7, 13, 13, 35, tzinfo=TZ), price=10)
    report.update(type="afternoon", run_id="2026-07-13-afternoon", source_status={
        "llm": "success", "market_data": "ok", "realtime_prices": "ok_1", "morning_report": "ok"})
    report["stock_recommendations"][0].update(trade_eligible=False, decision_scope="advisory_only", afternoon_decision=decision)
    result = flow.recheck_entry_plans(report=report)
    assert len(result["orders"]) == expected
    assert len(flow.service.ledger.list_trades()) == expected


def test_same_run_rewritten_prices_do_not_mutate_or_execute_original_plan(setup):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    clock["price"] = 10
    report["stock_recommendations"][0]["target_price"] = 10.3
    assert flow.prepare_final_orders(report, SOURCES, notify=False)["orders"] == []
    plan = flow.entry_plans.list_plans()[0]
    assert plan["stock"]["target_price"] == 10.9
    assert plan["status"] == "rejected"


def test_new_opening_run_supersedes_old_waiting_plan_without_double_buy(setup):
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    report["run_id"] = "2026-07-13-morning-v2"
    report["stock_recommendations"][0]["recommendation_id"] = "REC-V2"
    flow.prepare_final_orders(report, SOURCES)
    assert sum(plan["status"] == "waiting_trigger" for plan in flow.entry_plans.list_plans()) == 1
    clock["price"] = 10
    result = flow.recheck_entry_plans(report, SOURCES)
    assert len(result["orders"]) == 1
    assert result["orders"][0]["idempotency_key"] == "entry:2026-07-13:000001"
    assert len(flow.service.ledger.list_trades()) == 1


def test_duplicate_code_in_one_report_executes_only_the_latest_registered_plan(setup):
    flow, clock, report, _ = setup
    first = report["stock_recommendations"][0]
    report["stock_recommendations"] = [first, dict(first, recommendation_id="REC-NEW", target_price=10.8)]
    clock["price"] = 10
    result = flow.prepare_final_orders(report, SOURCES, notify=False)
    assert len(result["orders"]) == 1
    assert result["orders"][0]["recommendation_id"] == "REC-NEW"
    assert len(flow.service.ledger.list_trades()) == 1


def test_default_intraday_loader_uses_afternoon_veto_without_new_buy_authority(setup, tmp_path):
    import json
    flow, clock, report, _ = setup
    flow.prepare_final_orders(report, SOURCES)
    folder = tmp_path / "recommendations" / "2026-07-13"
    folder.mkdir(parents=True)
    (folder / "morning.json").write_text(json.dumps(report))
    report.update(type="afternoon", source_status={"llm": "fallback"})
    (folder / "afternoon.json").write_text(json.dumps(report))
    clock.update(now=datetime(2026, 7, 13, 13, 35, tzinfo=TZ), price=10)
    assert flow.intraday_check(notify=False)["entry_recheck"]["orders"] == []
    assert flow.service.ledger.list_trades() == []


def test_fresh_afternoon_advisory_does_not_create_buy_authority(setup):
    flow, clock, report, _ = setup
    clock["price"] = 10
    report.update(type="afternoon", source_status={"llm": "success", "market_data": "ok",
                  "realtime_prices": "ok_1", "morning_report": "ok"})
    report["stock_recommendations"][0].update(trade_eligible=False, decision_scope="advisory_only", afternoon_decision="upgrade")
    assert flow.prepare_final_orders(report, notify=False)["orders"] == []
    assert flow.service.ledger.list_trades() == []


def test_recheck_reconciles_crash_after_fill_before_plan_status_write(setup, monkeypatch):
    flow, clock, report, _ = setup
    clock["price"] = 10
    update = flow.entry_plans.update
    with monkeypatch.context() as patch:
        patch.setattr(flow.entry_plans, "update", lambda plan_id, status, *args: None if status == "filled" else update(plan_id, status, *args))
        patch.setattr(flow.entry_plans, "reconcile_fills", lambda _: None)
        assert len(flow.prepare_final_orders(report, SOURCES, notify=False)["orders"]) == 1
    assert flow.entry_plans.list_plans()[0]["status"] == "ready"
    flow.recheck_entry_plans(report, SOURCES)
    assert flow.entry_plans.list_plans()[0]["status"] == "filled"
    assert len(flow.service.ledger.list_trades()) == 1
