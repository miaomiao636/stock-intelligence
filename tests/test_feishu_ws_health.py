import json
import os
from datetime import datetime, timedelta, timezone

from src.notifier.feishu_ws import configure_direct_feishu_connection, get_long_connection_health


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
