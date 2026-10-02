"""Automatic confirmation may recover interruptions, never overwrite later states."""
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.paper_trading.trading_service import TradingService


@pytest.fixture
def proposed(tmp_path):
    now = datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    service = TradingService(tmp_path, now_provider=lambda: now)
    service.initialize_account(20000)
    order = service.propose_order(
        run_id="atomic-fixture", recommendation_id="REC-ATOMIC", code="000001",
        name="合成标的", sector="测试", action="buy", quantity=100,
        planned_price=10, stop_price=9.7, target_price=11.2,
    )
    return service, order, now


@pytest.mark.parametrize("status", [
    "cancelled_by_user", "paused_for_day", "rejected", "filled",
    "revalidating", "final_notified",
])
def test_auto_confirm_does_not_overwrite_state_changed_during_confirmation(proposed, monkeypatch, status):
    service, order, now = proposed
    record = service.ledger.record_decision
    protected = {}

    def concurrent_decision(*args, **kwargs):
        result = record(*args, **kwargs)
        protected.update(service.ledger.update_order(
            order["order_id"], status, now, decision="concurrent-owner",
            decision_actor="other-worker", reject_reason="protected state",
        ))
        return result

    monkeypatch.setattr(service.ledger, "record_decision", concurrent_decision)
    result = service.confirm_automatically(order["order_id"])
    assert result == protected
    assert service.get_order(order["order_id"]) == protected


@pytest.mark.parametrize("status", ["proposed", "pre_notified"])
def test_duplicate_auto_decision_after_interruption_can_finish_confirmation(proposed, status):
    service, order, now = proposed
    order_id = order["order_id"]
    service.ledger.update_order(order_id, status, now)
    event_id = hashlib.sha256(f"{order_id}:auto_execute:system_auto".encode()).hexdigest()
    service.ledger.record_decision(order_id, "auto_execute", "system_auto", now, event_id)

    result = service.confirm_automatically(order_id)
    assert result["status"] == "confirmed"
    assert result["decision"] == "auto_execute"
    assert service.confirm_automatically(order_id) == result
    with service.ledger.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trading_decisions WHERE order_id=?", (order_id,)).fetchone()[0] == 1
