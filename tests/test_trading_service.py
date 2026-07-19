# -*- coding: utf-8 -*-
"""SQLite 交易服务跨日集成测试。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.paper_trading.trading_service import TradingService


TZ = ZoneInfo("Asia/Shanghai")


def _quote(code: str, price: float, now: datetime) -> dict:
    return {
        "code": code,
        "price": price,
        "quote_time": now.isoformat(),
        "trade_date": now.date().isoformat(),
        "trade_status": "trading",
        "instrument_type": "stock",
    }


def test_trading_service_buy_is_idempotent_and_sell_is_t_plus_one(tmp_path):
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    service = TradingService(tmp_path, now_provider=lambda: clock["now"])
    service.initialize_account(4000)

    buy = service.propose_order(
        run_id="2026-07-13-open",
        recommendation_id="REC-000001",
        code="000001",
        name="平安银行",
        sector="金融",
        action="buy",
        quantity=100,
        planned_price=10,
        min_price=9.9,
        max_price=10.1,
        stop_price=9.7,
        target_price=10.6,
        reason="集成测试",
    )
    duplicate = service.propose_order(
        run_id="2026-07-13-open",
        recommendation_id="REC-000001",
        code="000001",
        name="平安银行",
        sector="金融",
        action="buy",
        quantity=100,
        planned_price=10,
        stop_price=9.7,
    )
    assert duplicate["order_id"] == buy["order_id"]

    service.mark_final_notified(buy["order_id"], clock["now"])
    service.record_decision(buy["order_id"], "confirm")
    filled = service.execute_ready_order(buy["order_id"], _quote("000001", 10, clock["now"]))
    assert filled["success"] is True
    assert service.get_positions()[0]["available_quantity"] == 0

    clock["now"] = datetime(2026, 7, 14, 9, 35, tzinfo=TZ)
    sell = service.propose_order(
        run_id="2026-07-14-exit",
        recommendation_id="REC-000001",
        code="000001",
        name="平安银行",
        sector="金融",
        action="sell",
        quantity=100,
        planned_price=10.5,
        min_price=10.4,
        max_price=10.6,
        reason="跨日退出",
    )
    service.mark_final_notified(sell["order_id"], clock["now"])
    service.record_decision(sell["order_id"], "confirm")
    sold = service.execute_ready_order(sell["order_id"], _quote("000001", 10.5, clock["now"]))

    assert sold["success"] is True
    assert service.get_positions() == []
    assert sold["trade"]["realized_pnl"] is not None


def test_performance_uses_live_net_equity_costs_and_initial_peak(tmp_path):
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    service = TradingService(tmp_path, now_provider=lambda: clock["now"])
    service.initialize_account(4000)
    service.record_equity_snapshot(3900, at="2026-07-13T09:30:00+08:00")

    with service.ledger.transaction() as conn:
        conn.execute(
            "UPDATE trading_accounts SET cash=3800 WHERE account_id='default'"
        )

    metrics = service.get_performance_metrics()

    assert metrics["total_equity"] == 3800
    assert metrics["net_return_after_costs"] == -200
    assert metrics["net_return_after_costs_pct"] == -5.0
    assert metrics["max_drawdown_pct"] == 5.0
    assert metrics["cost_basis"] == "fees_and_slippage_included"


def test_available_cash_adjustment_preserves_performance_principal(tmp_path):
    service = TradingService(tmp_path)
    service.initialize_account(4000)

    account = service.update_available_cash(5500)
    metrics = service.get_performance_metrics()

    assert account["cash"] == 5500
    assert account["net_cash_adjustment"] == 1500
    assert account["adjusted_principal"] == 5500
    assert metrics["effective_principal"] == 5500
    assert metrics["net_return_after_costs"] == 0


def test_available_cash_adjustment_rejects_non_positive_or_non_finite_amount(tmp_path):
    service = TradingService(tmp_path)
    service.initialize_account(4000)

    for value in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            service.update_available_cash(value)


def test_account_reset_clears_cash_adjustment_history(tmp_path):
    service = TradingService(tmp_path)
    service.initialize_account(4000)
    service.update_available_cash(5500)

    reset = service.initialize_account(4000, reset=True)

    assert reset["cash"] == 4000
    assert reset["net_cash_adjustment"] == 0
    assert reset["adjusted_principal"] == 4000
