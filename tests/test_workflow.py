from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.paper_trading.workflow import PaperTradingWorkflow


TZ = ZoneInfo("Asia/Shanghai")


class FakeNotifier:
    def __init__(self):
        self.cards = []
        self.messages = []
        self.callback_reachable = True

    def is_interactive_available(self):
        return True

    def check_callback_reachable(self, timeout=5):
        return {
            "reachable": self.callback_reachable,
            "reason": "ok" if self.callback_reachable else "测试回调离线",
        }

    def send_trade_plan(self, order):
        self.cards.append(order)
        return {"status": "success"}

    def send_message(self, title, content):
        self.messages.append((title, content))
        return {"status": "success"}


def test_0935_workflow_auto_executes_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}

    def fetch(codes):
        return {code: {
            "code": code,
            "name": "平安银行",
            "price": 10.0,
            "quote_time": clock["now"].isoformat(),
            "trade_date": clock["now"].date().isoformat(),
            "trade_status": "trading",
            "source_time_reliable": True,
        } for code in codes}

    notifier = FakeNotifier()
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=notifier,
        quote_fetcher=fetch,
        now_provider=lambda: clock["now"],
    )
    workflow.service.initialize_account(4000)
    report = {
        "run_id": "2026-07-13-morning",
        "market_regime": "bullish",
        "stock_recommendations": [{
            "recommendation_id": "REC-000001",
            "code": "000001",
            "name": "平安银行",
            "sector": "金融",
            "action": "setup_ready",
            "trade_eligible": True,
            "confidence": 4,
            "price_validation": {"verified": True},
            "current_price": 10,
            "stop_loss_price": 9.7,
            "target_price": 10.6,
            "horizon": "short",
            "reason": "测试",
        }],
    }

    prepared = workflow.prepare_final_orders(
        report,
        {"llm": "success", "candidate_universe": "ok_50", "recommendation_prices": "ok_1"},
    )
    assert prepared["status"] == "success"
    assert notifier.cards == []
    assert prepared["orders"][0]["status"] == "filled"
    assert prepared["notification_result"]["status"] == "success"
    assert notifier.messages[0][0] == "模拟订单已自动成交"
    assert workflow.service.get_positions()[0]["code"] == "000001"
    assert workflow.execute_due_orders()["results"] == []


def test_degraded_source_never_prepares_order(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    workflow = PaperTradingWorkflow(tmp_path, notifier=FakeNotifier(), quote_fetcher=lambda _: {})
    workflow.service.initialize_account(4000)
    result = workflow.prepare_final_orders({"stock_recommendations": []}, {"llm": "fallback"})
    assert result["status"] == "safe_mode"
    assert workflow.service.ledger.list_orders() == []


def test_auto_execution_does_not_depend_on_callback(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    notifier = FakeNotifier()
    notifier.callback_reachable = False
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=notifier,
        quote_fetcher=_quote_fetcher(clock),
        now_provider=lambda: clock["now"],
    )
    workflow.service.initialize_account(4000)

    result = workflow.prepare_final_orders(
        _tradeable_report("REC-NO-CALLBACK"),
        {"llm": "success", "candidate_universe": "ok_50", "recommendation_prices": "ok_1"},
    )

    assert result["status"] == "success"
    assert result["orders"][0]["status"] == "filled"
    assert workflow.service.get_positions()[0]["code"] == "000001"


def test_high_volatility_pause_is_decided_before_callback_probe(tmp_path, monkeypatch):
    """风险暂停是业务结论，不应被临时回调故障遮蔽。"""
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    notifier = FakeNotifier()
    notifier.callback_reachable = False
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=notifier,
        quote_fetcher=lambda _: (_ for _ in ()).throw(AssertionError("暂停交易时不应取行情")),
    )
    workflow.service.initialize_account(4000)

    result = workflow.prepare_final_orders(
        {"market_regime": "high_volatility", "stock_recommendations": []},
        {"llm": "success", "candidate_universe": "ok_50", "recommendation_prices": "ok_0"},
    )

    assert result["status"] == "safe_mode"
    assert "high_volatility" in result["reason"]
    assert "回调" not in result["reason"]


def _tradeable_report(recommendation_id):
    return {
        "run_id": recommendation_id,
        "market_regime": "bullish",
        "stock_recommendations": [{
            "recommendation_id": recommendation_id,
            "code": "000001",
            "name": "平安银行",
            "sector": "金融",
            "action": "setup_ready",
            "trade_eligible": True,
            "confidence": 4,
            "price_validation": {"verified": True},
            "stop_loss_price": 9.7,
            "target_price": 10.6,
            "horizon": "short",
            "reason": "测试",
        }],
    }


def _quote_fetcher(clock):
    def fetch(codes):
        return {code: {
            "code": code,
            "name": "平安银行",
            "price": 10.0,
            "quote_time": clock["now"].isoformat(),
            "trade_date": clock["now"].date().isoformat(),
            "trade_status": "trading",
            "source_time_reliable": True,
        } for code in codes}
    return fetch


def test_due_order_is_rejected_when_callback_becomes_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    notifier = FakeNotifier()
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=notifier,
        quote_fetcher=_quote_fetcher(clock),
        now_provider=lambda: clock["now"],
    )
    workflow.service.initialize_account(4000)
    order = workflow.service.propose_order(
        run_id="legacy-offline",
        recommendation_id="REC-OFFLINE",
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
    )
    order = workflow.service.mark_final_notified(order["order_id"], veto_minutes=5)
    order_id = order["order_id"]

    notifier.callback_reachable = False
    clock["now"] += timedelta(minutes=5)
    executed = workflow.execute_due_orders()

    assert executed["results"][0]["success"] is False
    assert "回调" in executed["results"][0]["error"]
    assert workflow.service.get_order(order_id)["status"] == "rejected"
    assert workflow.service.get_positions() == []


def test_confirmed_order_executes_without_second_callback_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    notifier = FakeNotifier()
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=notifier,
        quote_fetcher=_quote_fetcher(clock),
        now_provider=lambda: clock["now"],
    )
    workflow.service.initialize_account(4000)
    order = workflow.service.propose_order(
        run_id="legacy-confirmed",
        recommendation_id="REC-CONFIRMED",
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
    )
    order = workflow.service.mark_final_notified(order["order_id"], veto_minutes=5)
    order_id = order["order_id"]
    workflow.service.record_decision(order_id, "confirm")
    notifier.callback_reachable = False

    executed = workflow.execute_due_orders()

    assert executed["results"][0]["success"] is True, executed
    assert workflow.service.get_order(order_id)["status"] == "filled"


def test_intraday_risk_exit_auto_executes_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    clock = {"now": datetime(2026, 7, 13, 9, 35, tzinfo=TZ)}
    price = {"value": 10.0}

    def fetch(codes):
        return {code: {
            "code": code,
            "name": "平安银行",
            "price": price["value"],
            "quote_time": clock["now"].isoformat(),
            "trade_date": clock["now"].date().isoformat(),
            "trade_status": "trading",
            "source_time_reliable": True,
        } for code in codes}

    notifier = FakeNotifier()
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=notifier,
        quote_fetcher=fetch,
        now_provider=lambda: clock["now"],
    )
    workflow.service.initialize_account(4000)
    buy = workflow.service.propose_order(
        run_id="buy-day-one",
        recommendation_id="REC-AUTO-EXIT",
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
    )
    workflow.service.confirm_automatically(buy["order_id"])
    assert workflow.service.execute_ready_order(buy["order_id"], fetch(["000001"])["000001"])["success"]

    clock["now"] = datetime(2026, 7, 14, 10, 5, tzinfo=TZ)
    price["value"] = 10.7
    result = workflow.intraday_check()

    assert result["status"] == "success"
    assert any("已自动卖出100股" in item for item in result["alerts"])
    assert workflow.service.get_positions() == []
    assert notifier.cards == []
    assert notifier.messages[-1][0] == "盘中风险变化"


def test_intraday_check_is_disabled_when_paper_trading_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "false")
    workflow = PaperTradingWorkflow(
        tmp_path,
        notifier=FakeNotifier(),
        quote_fetcher=lambda _: (_ for _ in ()).throw(AssertionError("不应取行情")),
    )
    workflow.service.initialize_account(4000)

    result = workflow.intraday_check()

    assert result["status"] == "disabled"
