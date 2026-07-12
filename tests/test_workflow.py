from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.paper_trading.workflow import PaperTradingWorkflow


TZ = ZoneInfo("Asia/Shanghai")


class FakeNotifier:
    def __init__(self):
        self.cards = []
        self.messages = []

    def is_interactive_available(self):
        return True

    def send_trade_plan(self, order):
        self.cards.append(order)
        return {"status": "success"}

    def send_message(self, title, content):
        self.messages.append((title, content))
        return {"status": "success"}


def test_0935_to_0940_workflow_requotes_and_fills(tmp_path, monkeypatch):
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
            "current_price": 10,
            "stop_loss_price": 9.7,
            "target_price": 10.6,
            "horizon": "short",
            "reason": "测试",
        }],
    }

    prepared = workflow.prepare_final_orders(report, {"llm": "success", "candidate_universe": "ok_50"})
    assert prepared["status"] == "success"
    assert len(notifier.cards) == 1
    assert prepared["orders"][0]["status"] == "final_notified"

    clock["now"] += timedelta(minutes=5)
    executed = workflow.execute_due_orders()
    assert executed["results"][0]["success"] is True
    assert workflow.service.get_positions()[0]["code"] == "000001"


def test_degraded_source_never_prepares_order(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    workflow = PaperTradingWorkflow(tmp_path, notifier=FakeNotifier(), quote_fetcher=lambda _: {})
    workflow.service.initialize_account(4000)
    result = workflow.prepare_final_orders({"stock_recommendations": []}, {"llm": "fallback"})
    assert result["status"] == "safe_mode"
    assert workflow.service.ledger.list_orders() == []
