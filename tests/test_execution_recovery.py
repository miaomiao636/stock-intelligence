"""Offline regressions for bounded exit recovery and cost-aware sizing."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.paper_trading.quality_gate import calculate_net_reward_risk
from src.paper_trading.workflow import PaperTradingWorkflow
from src.utils.cost_calculator import calculate_trade_costs


TZ = ZoneInfo("Asia/Shanghai")


class NoNotifications:
    def send_message(self, *args, **kwargs):
        raise AssertionError("test must not send a notification")


@pytest.fixture
def exit_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    monkeypatch.setattr("src.paper_trading.workflow.is_trading_day", lambda day: day.weekday() < 5)
    monkeypatch.setattr("src.paper_trading.workflow.count_trading_days", lambda start, end: (end - start).days)
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ), "price": 10.0, "age": 0}

    def quotes(codes):
        return {code: {"code": code, "price": clock["price"], "trade_status": "trading",
                       "source_time_reliable": True, "trade_date": clock["now"].date().isoformat(),
                       "quote_time": (clock["now"] - timedelta(seconds=clock["age"])).isoformat()}
                for code in codes}

    def make_workflow():
        return PaperTradingWorkflow(tmp_path, notifier=NoNotifications(), quote_fetcher=quotes,
                                    now_provider=lambda: clock["now"])

    flow = make_workflow()
    flow.service.initialize_account(20000)
    buy = flow.service.propose_order(run_id="fixture", recommendation_id="REC-EXIT", code="000001",
        name="合成标的", sector="测试", action="buy", quantity=100, planned_price=10,
        min_price=9.9, max_price=10.1, stop_price=9.7, target_price=11.2)
    flow.service.confirm_automatically(buy["order_id"])
    assert flow.service.execute_ready_order(buy["order_id"], quotes(["000001"])["000001"])["success"]
    clock.update(now=datetime(2026, 7, 14, 10, 5, tzinfo=TZ), price=9.5, age=120)
    return flow, clock, make_workflow


def sell_orders(flow):
    return [order for order in flow.service.ledger.list_orders() if order["action"] == "sell"]


def test_stale_exit_recovers_after_restart_with_new_quote_and_only_one_fill(exit_fixture):
    flow, clock, restart = exit_fixture
    first = flow.intraday_check(notify=False)
    original = sell_orders(flow)[0]
    assert original["status"] == "rejected" and original["reject_reason"] == "行情已过期"
    assert any("行情已过期" in text for text in first["alerts"])
    clock.update(now=datetime(2026, 7, 14, 10, 35, tzinfo=TZ), price=9.2, age=0)
    flow = restart()
    recovered = flow.intraday_check(notify=False)
    assert flow.service.get_positions() == []
    assert len(sell_orders(flow)) == 2
    assert flow.service.get_order(original["order_id"]) == original
    assert sell_orders(flow)[1]["reason"] == original["reason"]
    assert any("已自动卖出100股" in text for text in recovered["alerts"])
    flow.intraday_check(notify=False)
    assert len([trade for trade in flow.service.ledger.list_trades() if trade["action"] == "sell"]) == 1


def test_stale_exit_attempts_are_bounded_and_exhaustion_remains_visible(exit_fixture):
    flow, clock, restart = exit_fixture
    for _ in range(5):
        result = flow.intraday_check(notify=False)
        clock["now"] += timedelta(minutes=1)
        flow = restart()
    assert len(sell_orders(flow)) == 3  # Original plus at most two recovery attempts.
    assert all(order["status"] == "rejected" for order in sell_orders(flow))
    assert len(flow.service.get_positions()) == 1
    assert any("重试上限" in text for text in result["alerts"])


@pytest.mark.parametrize("status,reason", [
    ("cancelled_by_user", "人工否决"),
    ("paused_for_day", "用户暂停"),
    ("rejected", "没有可卖持仓"),
    ("rejected", "行情代码与订单不一致"),
    ("rejected", "最新价低于允许成交区间"),
    ("rejected", "账本事务失败: unknown"),
])
def test_terminal_exit_is_not_resurrected_or_silently_hidden(exit_fixture, status, reason):
    flow, clock, _ = exit_fixture
    flow.intraday_check(notify=False)
    original = sell_orders(flow)[0]
    flow.service.ledger.update_order(original["order_id"], status, clock["now"], reject_reason=reason)
    protected = flow.service.get_order(original["order_id"])
    clock.update(now=datetime(2026, 7, 14, 10, 35, tzinfo=TZ), age=0)
    result = flow.intraday_check(notify=False)
    assert len(sell_orders(flow)) == 1
    assert flow.service.get_order(original["order_id"]) == protected
    assert len(flow.service.get_positions()) == 1
    assert result["alerts"]


def test_confirmed_auto_exit_survives_interruption_without_new_order(exit_fixture, monkeypatch):
    flow, clock, restart = exit_fixture
    clock["age"] = 0
    real_execute = flow.service.execute_ready_order
    monkeypatch.setattr(flow.service, "execute_ready_order", lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        flow.intraday_check(notify=False)
    assert sell_orders(flow)[0]["status"] == "confirmed"
    monkeypatch.setattr(flow.service, "execute_ready_order", real_execute)
    flow = restart()
    flow.intraday_check(notify=False)
    assert flow.service.get_positions() == []
    assert len(sell_orders(flow)) == 1


def test_other_legacy_exit_root_business_rejection_blocks_recovery(exit_fixture):
    flow, clock, restart = exit_fixture
    flow.intraday_check(notify=False)
    clock["now"] += timedelta(minutes=1)
    legacy = flow.service.propose_order(run_id="2026-07-14-intraday", recommendation_id="REC-EXIT",
        code="000001", name="合成标的", sector="测试", action="sell", quantity=100,
        planned_price=9.5, reason="达到持有周期上限",
        idempotency_key="exit:2026-07-14:000001:达到持有周期上限")
    flow.service.ledger.update_order(legacy["order_id"], "rejected", clock["now"], reject_reason="没有可卖持仓")
    previous = sell_orders(flow)
    clock.update(now=datetime(2026, 7, 14, 10, 35, tzinfo=TZ), age=0)
    flow = restart()
    result = flow.intraday_check(notify=False)
    assert sell_orders(flow) == previous
    assert len(flow.service.get_positions()) == 1
    assert any("没有可卖持仓" in text for text in result["alerts"])


@pytest.mark.parametrize("price,kind,cash,max_amount,daily_risk", [
    (1.25, "stock", 20000, 6000, 300),
    (1.01, "stock", 20000, 6000, 300),
    (2.0, "etf", 20000, 6000, 300),
    (10.0, "stock", 5250, 6000, 300),
    (3.25, "stock", 20000, 6000, 45),
])
def test_quantity_is_largest_whole_lot_with_identical_round_trip_risk(price, kind, cash, max_amount, daily_risk):
    stop, equity, reserve = round(price * .97, 3), 20000, .2
    risk_budget = min(equity * .0075, daily_risk)

    def allowed(quantity):
        risk = calculate_net_reward_risk(entry_market_price=price, target_market_price=price * 1.12,
            stop_market_price=stop, quantity=quantity, instrument_type=kind)["net_risk"]
        buy = calculate_trade_costs(price * quantity, "buy", kind)
        return (price * quantity <= max_amount and
                price * quantity + buy["total_cost"] <= cash - equity * reserve + 1e-9 and
                0 < risk <= risk_budget + 1e-9)

    expected = max([0] + [q for q in range(100, int(max_amount // (price * 100)) * 100 + 1, 100) if allowed(q)])
    quantity = PaperTradingWorkflow._maximum_quantity(price=price, stop=stop, max_amount=max_amount,
        equity=equity, cash=cash, reserve_pct=reserve, instrument_type=kind,
        max_trade_risk_pct=.0075, remaining_daily_risk=daily_risk)
    assert quantity == expected


def test_cost_aware_sizing_does_not_terminally_reject_an_affordable_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    monkeypatch.setattr("src.paper_trading.workflow.is_trading_day", lambda _: True)
    now = datetime(2026, 7, 13, 9, 35, tzinfo=TZ)
    flow = PaperTradingWorkflow(tmp_path, notifier=NoNotifications(), now_provider=lambda: now,
        quote_fetcher=lambda codes: {code: {"code": code, "price": 1.25, "trade_status": "trading",
            "source_time_reliable": True, "trade_date": now.date().isoformat(), "quote_time": now.isoformat()} for code in codes})
    flow.service.initialize_account(20000)
    report = {"date": "2026-07-13", "run_id": "sizing-fixture", "market_regime": "bullish",
        "stock_recommendations": [{"code": "000001", "name": "合成标的", "sector": "测试",
            "recommendation_id": "REC-SIZE", "action": "setup_ready", "trade_eligible": True,
            "confidence": 4, "price_validation": {"verified": True}, "entry_price": 1.25,
            "stop_loss_price": 1.25 * .97, "target_price": 1.25 * 1.12, "horizon": "short"}]}
    result = flow.prepare_final_orders(report, {"llm": "success", "candidate_universe": "ok_50", "recommendation_prices": "ok_1"}, notify=False)
    assert result["status"] == "success"
    assert len(result["orders"]) == 1 and result["orders"][0]["status"] == "filled"
    assert flow.entry_plans.list_plans()[0]["status"] == "filled"
    order = result["orders"][0]
    risk = calculate_net_reward_risk(entry_market_price=1.25, target_market_price=order["target_price"],
        stop_market_price=order["stop_price"], quantity=order["quantity"], instrument_type="stock")["net_risk"]
    assert risk <= 20000 * .0075
