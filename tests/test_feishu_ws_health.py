import asyncio
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from src.notifier.feishu_ws import (
    FeishuLongConnection,
    configure_direct_feishu_connection,
    get_long_connection_health,
)


def test_long_connection_health_requires_fresh_live_heartbeat(tmp_path):
    status_path = tmp_path / "feishu_ws_status.json"
    now = datetime.now(timezone.utc)
    status_path.write_text(json.dumps({
        "pid": os.getpid(),
        "state": "connected",
        "ready": True,
        "heartbeat_at": now.isoformat(),
    }))

    healthy = get_long_connection_health(status_path, now=now)
    stale = get_long_connection_health(status_path, now=now + timedelta(seconds=60))

    assert healthy["reachable"] is True
    assert healthy["transport"] == "ws"
    assert stale["reachable"] is False
    assert "心跳" in stale["reason"]


def test_long_connection_health_fails_closed_for_invalid_status(tmp_path):
    status_path = tmp_path / "feishu_ws_status.json"
    status_path.write_text("not-json")

    result = get_long_connection_health(status_path)

    assert result["reachable"] is False
    assert "状态文件无效" in result["reason"]


def test_long_connection_bypasses_unstable_desktop_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:12000")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")

    configure_direct_feishu_connection()

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert name not in os.environ
    assert "open.feishu.cn" in os.environ["NO_PROXY"]
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


def test_confirm_callback_acks_before_slow_execution_and_reports_node(tmp_path):
    class Notifier:
        def __init__(self):
            self.messages = []

        @staticmethod
        def parse_card_action(_payload):
            return {
                "action": "confirm",
                "order_id": "order-1",
                "actor": "ou-test",
                "event_id": "event-1",
            }

        def send_message(self, title, content):
            self.messages.append((title, content))
            return {"status": "success"}

    execution_started = threading.Event()
    allow_execution_to_finish = threading.Event()
    execution_finished = threading.Event()

    def slow_execution():
        execution_started.set()
        allow_execution_to_finish.wait(timeout=2)
        execution_finished.set()
        return {"status": "success"}

    def processor(_action, *, execute_confirm):
        return {
            "toast": {"type": "success", "content": "订单已确认"},
            "execution": execute_confirm(),
        }

    async def scenario():
        notifier = Notifier()
        connection = FeishuLongConnection(
            status_path=tmp_path / "status.json",
            notifier=notifier,
            action_processor=processor,
            execute_confirm=slow_execution,
            node_id="mac-test",
        )

        await asyncio.wait_for(connection.handle_card_action(object()), timeout=0.5)
        assert execution_finished.is_set() is False

        for _ in range(50):
            if execution_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert execution_started.is_set() is True

        allow_execution_to_finish.set()
        for _ in range(100):
            if execution_finished.is_set() and notifier.messages:
                break
            await asyncio.sleep(0.01)

        assert execution_finished.is_set() is True
        assert notifier.messages
        assert "处理节点：mac-test" in notifier.messages[0][1]

        if connection.background_tasks:
            await asyncio.gather(*tuple(connection.background_tasks))

    asyncio.run(scenario())
