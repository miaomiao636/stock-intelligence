from types import SimpleNamespace

from src.notifier.feishu import FeishuNotifier
from src.notifier.feishu_actions import process_card_action
from src.notifier.feishu_ws import card_event_to_payload


class FakeService:
    def __init__(self):
        self.calls = []

    def record_decision(self, order_id, action, actor, event_id):
        self.calls.append((order_id, action, actor, event_id))
        return {"status": "confirmed"}


def test_confirm_action_uses_shared_processor_and_executes_due_orders():
    service = FakeService()
    executions = []

    result = process_card_action(
        {
            "action": "confirm",
            "order_id": "order-1",
            "actor": "ou-test",
            "event_id": "evt-1",
        },
        service=service,
        execute_confirm=lambda: executions.append("run") or {"status": "success"},
    )

    assert service.calls == [("order-1", "confirm", "ou-test", "evt-1")]
    assert executions == ["run"]
    assert result["toast"]["type"] == "success"


def test_ping_and_unknown_actions_do_not_touch_trading_service():
    service = FakeService()

    assert process_card_action({"action": "ping"}, service=service)["toast"]["type"] == "success"
    assert process_card_action({"action": "unknown", "order_id": "x"}, service=service)["toast"]["type"] == "warning"
    assert service.calls == []


def test_terminal_order_click_returns_business_reason_without_executing():
    class TerminalOrderService:
        @staticmethod
        def record_decision(order_id, action, actor, event_id):
            raise ValueError("订单状态不允许此操作: filled")

    executions = []
    result = process_card_action(
        {
            "action": "confirm",
            "order_id": "order-filled",
            "actor": "ou-test",
            "event_id": "evt-terminal",
        },
        service=TerminalOrderService(),
        execute_confirm=lambda: executions.append("run"),
    )

    assert result == {
        "toast": {
            "type": "warning",
            "content": "操作未执行：订单状态不允许此操作: filled",
        },
        "execution": None,
    }
    assert executions == []


def test_sdk_card_event_is_converted_with_stable_event_id():
    event = SimpleNamespace(
        message_id="om-1",
        chat_id="oc-1",
        operator=SimpleNamespace(open_id="ou-1", user_id=None, name=None),
        action=SimpleNamespace(
            value={"action": "veto", "order_id": "order-1"},
            tag="button",
        ),
        raw={},
    )

    first = card_event_to_payload(event)
    second = card_event_to_payload(event)
    parsed = FeishuNotifier.parse_card_action(first)

    assert parsed["action"] == "veto"
    assert parsed["order_id"] == "order-1"
    assert parsed["actor"] == "ou-1"
    assert parsed["event_id"].startswith("ws-")
    assert parsed["event_id"] == FeishuNotifier.parse_card_action(second)["event_id"]
