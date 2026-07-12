# -*- coding: utf-8 -*-
"""半自动模拟交易闭环的行为规格。"""

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

from src.paper_trading.trading_service import TradingService


TZ = ZoneInfo("Asia/Shanghai")


def quote(code: str, price: float, at: datetime) -> dict:
    return {
        "code": code,
        "price": price,
        "quote_time": at.isoformat(),
        "trade_date": at.date().isoformat(),
        "trade_status": "trading",
        "instrument_type": "stock",
    }


def proposed_buy(service: TradingService, code: str = "000001", price: float = 10.0) -> dict:
    return service.propose_order(
        run_id="2026-07-13-open",
        recommendation_id=f"REC-{code}",
        code=code,
        name=f"测试{code}",
        sector="测试",
        action="buy",
        quantity=100,
        planned_price=price,
        min_price=price * 0.99,
        max_price=price * 1.01,
        stop_price=price * 0.97,
        target_price=price * 1.06,
        horizon="short",
        reason="测试信号",
    )


@pytest.fixture
def open_time() -> datetime:
    return datetime(2026, 7, 13, 9, 35, tzinfo=TZ)


@pytest.fixture
def service(tmp_path, open_time) -> TradingService:
    clock = {"now": open_time}
    result = TradingService(tmp_path, now_provider=lambda: clock["now"])
    result.initialize_account(4000)
    result._test_clock = clock
    return result


def test_confirm_revalidates_and_fills_with_costs(service, open_time):
    order = proposed_buy(service)
    service.mark_final_notified(order["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(order["order_id"], "confirm", actor="user")

    result = service.execute_ready_order(order["order_id"], quote("000001", 10.02, open_time))

    assert result["success"] is True
    assert result["order"]["status"] == "filled"
    assert result["trade"]["fees"] >= 5
    assert service.get_account()["cash"] < 3000
    assert service.get_positions()[0]["recommendation_id"] == "REC-000001"
    replay = service.execute_ready_order(order["order_id"], quote("000001", 10.02, open_time))
    assert replay["success"] is False
    assert service.get_order(order["order_id"])["status"] == "filled"


def test_no_action_becomes_executable_only_after_five_minutes(service, open_time):
    order = proposed_buy(service)
    service.mark_final_notified(order["order_id"], notified_at=open_time, veto_minutes=5)

    early = service.execute_ready_order(order["order_id"], quote("000001", 10.0, open_time))
    assert early["success"] is False
    assert "等待否决窗口" in early["error"]

    service._test_clock["now"] = open_time + timedelta(minutes=5)
    result = service.execute_ready_order(
        order["order_id"], quote("000001", 10.0, service._test_clock["now"])
    )
    assert result["success"] is True


def test_veto_is_final_and_prevents_fill(service, open_time):
    order = proposed_buy(service)
    service.mark_final_notified(order["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(order["order_id"], "veto", actor="user")

    result = service.execute_ready_order(order["order_id"], quote("000001", 10.0, open_time))
    assert result["success"] is False
    assert service.get_order(order["order_id"])["status"] == "cancelled_by_user"
    assert service.get_positions() == []


def test_pause_day_cancels_all_pending_and_blocks_new_orders(service, open_time):
    first = proposed_buy(service, "000001")
    second = proposed_buy(service, "000002")
    for order in (first, second):
        service.mark_final_notified(order["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(first["order_id"], "pause_day", actor="user")
    assert service.get_order(second["order_id"])["status"] == "paused_for_day"
    with pytest.raises(ValueError, match="今日模拟交易已由用户暂停"):
        proposed_buy(service, "000003")


def test_stale_or_out_of_range_quote_is_rejected(service, open_time):
    order = proposed_buy(service)
    service.mark_final_notified(order["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(order["order_id"], "confirm")

    stale = quote("000001", 10.0, open_time - timedelta(minutes=2))
    result = service.execute_ready_order(order["order_id"], stale)
    assert result["success"] is False
    assert "行情" in result["error"]


def test_stock_is_t_plus_one(service, open_time):
    buy = proposed_buy(service)
    service.mark_final_notified(buy["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(buy["order_id"], "confirm")
    assert service.execute_ready_order(buy["order_id"], quote("000001", 10.0, open_time))["success"]

    sell = service.propose_order(
        run_id="2026-07-13-exit",
        recommendation_id="REC-000001",
        code="000001",
        name="测试000001",
        sector="测试",
        action="sell",
        quantity=100,
        planned_price=10.2,
        min_price=10.0,
        max_price=10.5,
        reason="退出测试",
    )
    service.mark_final_notified(sell["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(sell["order_id"], "confirm")
    result = service.execute_ready_order(sell["order_id"], quote("000001", 10.2, open_time))
    assert result["success"] is False
    assert "T+1" in result["error"]


def test_4000_account_rules_limit_positions_and_preserve_cash(service, open_time):
    for code in ("000001", "000002"):
        order = proposed_buy(service, code)
        service.mark_final_notified(order["order_id"], notified_at=open_time, veto_minutes=5)
        service.record_decision(order["order_id"], "confirm")
        assert service.execute_ready_order(order["order_id"], quote(code, 10.0, open_time))["success"]

    third = proposed_buy(service, "000003")
    service.mark_final_notified(third["order_id"], notified_at=open_time, veto_minutes=5)
    service.record_decision(third["order_id"], "confirm")
    rejected = service.execute_ready_order(third["order_id"], quote("000003", 10.0, open_time))

    assert rejected["success"] is False
    assert len(service.get_positions()) == 2
    assert service.get_account()["cash"] >= 4000 * 0.30


def test_equity_metrics_use_net_equity_and_peak_drawdown(service):
    service.record_equity_snapshot(4000, at="2026-07-13T15:00:00+08:00")
    service.record_equity_snapshot(4200, at="2026-07-14T15:00:00+08:00")
    service.record_equity_snapshot(3780, at="2026-07-15T15:00:00+08:00")

    metrics = service.get_performance_metrics()
    assert metrics["net_return"] == pytest.approx(-220)
    assert metrics["max_drawdown_pct"] == pytest.approx(10.0)


def test_realtime_fallback_dict_is_safe_watch_only():
    from src.analysis.synthesizer import Synthesizer

    synthesizer = Synthesizer.__new__(Synthesizer)
    result = synthesizer._generate_tradeable_fallback({
        "realtime_stock_prices": {
            "000001": {"code": "000001", "name": "平安银行", "price": 10, "change_pct": 1}
        },
        "_account_equity": 4000,
    })

    assert result["stock_recommendations"][0]["action"] == "watch"
    assert result["stock_recommendations"][0]["trade_eligible"] is False


def test_feishu_trade_card_has_three_safe_actions(service):
    from src.notifier.feishu import FeishuNotifier

    order = proposed_buy(service)
    card = FeishuNotifier.build_trade_card(order)
    actions = card["elements"][1]["actions"]
    values = {item["value"]["action"] for item in actions}
    assert values == {"confirm", "veto", "pause_day"}


def test_daily_loss_gate_stops_new_positions(service, open_time):
    service.record_equity_snapshot(4000, at="2026-07-13T09:35:00+08:00")
    service.record_equity_snapshot(3939, at="2026-07-13T10:05:00+08:00")
    order = proposed_buy(service)
    service.mark_final_notified(order["order_id"], open_time)
    service.record_decision(order["order_id"], "confirm")
    result = service.execute_ready_order(order["order_id"], quote("000001", 10, open_time))
    assert result["success"] is False
    assert "当日亏损" in result["error"]


def test_callback_and_scheduler_cannot_double_fill(tmp_path, open_time):
    first = TradingService(tmp_path, now_provider=lambda: open_time)
    second = TradingService(tmp_path, now_provider=lambda: open_time)
    first.initialize_account(4000)
    order = proposed_buy(first)
    first.mark_final_notified(order["order_id"], open_time)
    first.record_decision(order["order_id"], "confirm")
    current_quote = quote("000001", 10, open_time)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda svc: svc.execute_ready_order(order["order_id"], current_quote), (first, second)))
    assert first.get_order(order["order_id"])["status"] == "filled"
    assert len(first.ledger.list_trades()) == 1
    assert len(first.get_positions()) == 1
