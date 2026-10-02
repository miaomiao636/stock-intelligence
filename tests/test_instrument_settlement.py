"""可卖数量与成交事务使用同一证券结算限制；仅使用临时合成账本。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.paper_trading.trading_service import TradingService
from src.paper_trading import trading_service
from src.utils.cost_calculator import calculate_trade_costs
from src.utils import instrument_settlement


TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def account(tmp_path):
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    service = TradingService(tmp_path, now_provider=lambda: clock["now"])
    service.initialize_account(20000)
    return service, clock


def quote(code, now):
    return {
        "code": code,
        "price": 10.0,
        "quote_time": now.isoformat(),
        "trade_date": now.date().isoformat(),
        "trade_status": "trading",
    }


def order(service, action, *, code="516160", kind="etf", quantity=100):
    item = service.propose_order(
        run_id=f"{action}-{service.now().isoformat()}",
        recommendation_id=f"REC-{code}",
        code=code,
        name="结算规则测试",
        sector="测试",
        action=action,
        quantity=quantity,
        planned_price=10.0,
        stop_price=9.7,
        target_price=10.8,
        instrument_type=kind,
    )
    service.confirm_automatically(item["order_id"])
    return service.get_order(item["order_id"])


def buy(service, clock, *, code="516160", kind="etf"):
    item = order(service, "buy", code=code, kind=kind)
    result = service.execute_ready_order(item["order_id"], quote(code, clock["now"]))
    assert result["success"], result
    return result["trade"]


@pytest.mark.parametrize("code,kind", [
    ("516160", "etf"),
    ("516160", "stock"),
    ("516160", "unknown"),
    ("516160", "etf_t0"),
    ("516160", ""),
    ("000001", "etf"),
    ("999999", "unknown"),
])
def test_unverified_instruments_cannot_be_sold_same_day(account, code, kind):
    service, clock = account
    buy(service, clock, code=code, kind=kind)
    before = service.get_account()
    sell = order(service, "sell", code=code, kind="etf_t0")

    result = service.execute_ready_order(sell["order_id"], quote(code, clock["now"]))

    assert not result["success"]
    assert "T+1" in result["error"]
    assert service.get_positions()[0]["available_quantity"] == 0
    assert service.get_account()["cash"] == before["cash"]
    assert len(service.ledger.list_trades()) == 1


@pytest.mark.parametrize("kind", ["stock", "etf", "unknown"])
def test_previous_day_lots_can_be_sold(account, kind):
    service, clock = account
    buy(service, clock, kind=kind)
    clock["now"] += timedelta(days=1)

    assert service.get_positions()[0]["available_quantity"] == 100
    sell = order(service, "sell", kind=kind)
    result = service.execute_ready_order(sell["order_id"], quote("516160", clock["now"]))

    assert result["success"]
    assert service.get_positions() == []


def test_transaction_rechecks_settlement_if_risk_check_is_bypassed(account):
    service, clock = account
    buy(service, clock)
    before_cash = service.get_account()["cash"]
    sell = order(service, "sell", kind="etf_t0")
    assert service.ledger.claim_order(sell["order_id"], "confirmed", clock["now"])

    with pytest.raises(RuntimeError, match="T\\+1"):
        service._fill(sell, 10.0, clock["now"])

    assert service.get_account()["cash"] == before_cash
    assert service.get_positions()[0]["quantity"] == 100
    assert len(service.ledger.list_trades()) == 1


def test_mixed_age_lots_only_sell_previous_day_quantity_fifo(account):
    service, clock = account
    buy(service, clock)
    clock["now"] += timedelta(days=1)
    buy(service, clock, kind="unknown")

    position = service.get_positions()[0]
    assert position["quantity"] == 200
    assert position["available_quantity"] == 100
    sell = order(service, "sell", kind="etf")
    result = service.execute_ready_order(sell["order_id"], quote("516160", clock["now"]))

    assert result["success"]
    with service.ledger.connect() as conn:
        lots = [dict(row) for row in conn.execute(
            "SELECT acquired_date,quantity FROM position_lots ORDER BY acquired_date"
        )]
    assert lots == [
        {"acquired_date": "2026-07-13", "quantity": 0},
        {"acquired_date": "2026-07-14", "quantity": 100},
    ]
    assert service.get_positions()[0]["available_quantity"] == 0


def test_daily_new_risk_includes_buy_fee_without_double_counting_buy_slippage(account):
    service, clock = account
    trade = buy(service, clock, code="000001", kind="stock")
    exit_cost = calculate_trade_costs(9.7 * 100, "sell", "stock")
    expected = (trade["price"] - 9.7) * 100 + trade["fees"] + exit_cost["total_cost"]

    assert service.daily_new_risk_amount("2026-07-13") == pytest.approx(expected, abs=1e-4)
    assert service.daily_new_risk_amount("2026-07-14") == 0


def test_daily_risk_gate_does_not_omit_previous_buy_fees(account, monkeypatch):
    service, clock = account
    config = dict(trading_service.get_paper_trading_config())
    config["max_daily_new_risk_pct"] = 0.004
    monkeypatch.setattr(trading_service, "get_paper_trading_config", lambda: config)
    buy(service, clock, code="000001", kind="stock")
    before_cash = service.get_account()["cash"]
    second = order(service, "buy", code="000002", kind="stock")

    result = service.execute_ready_order(second["order_id"], quote("000002", clock["now"]))

    assert not result["success"]
    assert "当日新增风险" in result["error"]
    assert service.get_account()["cash"] == before_cash
    assert len(service.ledger.list_trades()) == 1


def test_transaction_rolls_back_partial_sell_if_only_some_lots_have_settled(account):
    service, clock = account
    buy(service, clock)
    clock["now"] += timedelta(days=1)
    buy(service, clock)
    before_cash = service.get_account()["cash"]
    sell = order(service, "sell", quantity=200)
    assert service.ledger.claim_order(sell["order_id"], "confirmed", clock["now"])

    with pytest.raises(RuntimeError, match="T\\+1"):
        service._fill(sell, 10.0, clock["now"])

    position = service.get_positions()[0]
    assert position["quantity"] == 200
    assert position["available_quantity"] == 100
    assert service.get_account()["cash"] == before_cash
    assert len(service.ledger.list_trades()) == 2


@pytest.mark.parametrize("acquired,as_of", [
    (None, "2026-07-14"),
    ("", "2026-07-14"),
    ("bad-date", "2026-07-14"),
    ("20260713", "2026-07-14"),
    ("2026-07-13", None),
    ("2026-07-13", "2026-13-01"),
    ("2026-07-15", "2026-07-14"),
])
def test_invalid_or_future_lot_dates_are_not_sellable(acquired, as_of):
    assert not instrument_settlement.is_lot_sellable("516160", acquired, as_of)


def test_same_day_exception_requires_exact_reviewed_code_not_a_prefix(monkeypatch):
    # 合成证券代码仅在本测试注入；生产白名单仍为空。
    monkeypatch.setattr(instrument_settlement, "VERIFIED_SAME_DAY_SELL_CODES", frozenset({"999999"}))
    assert instrument_settlement.is_lot_sellable("999999", "2026-07-13", "2026-07-13")
    assert not instrument_settlement.is_lot_sellable("999998", "2026-07-13", "2026-07-13")
    assert not instrument_settlement.is_lot_sellable("999999", "2026-07-14", "2026-07-13")
