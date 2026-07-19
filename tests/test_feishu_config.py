from src.notifier.feishu import FeishuNotifier


def test_interactive_missing_fields(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_RECEIVE_ID", raising=False)
    monkeypatch.delenv("FEISHU_VERIFICATION_TOKEN", raising=False)

    notifier = FeishuNotifier()
    assert notifier.interactive_missing_fields() == [
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_RECEIVE_ID",
        "FEISHU_VERIFICATION_TOKEN",
    ]


def test_callback_url_uses_public_base_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stock.example.com/")
    notifier = FeishuNotifier()
    assert notifier.callback_path() == "/api/feishu/callback"
    assert notifier.callback_url() == "https://stock.example.com/api/feishu/callback"


def test_callback_reachability_uses_public_callback(monkeypatch):
    monkeypatch.setenv("FEISHU_CALLBACK_MODE", "http")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stock.example.com")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "secret-token")
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"toast": {"type": "success", "content": "飞书回调链路正常"}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        captured["timeout"] = kwargs.get("timeout")
        return Response()

    monkeypatch.setattr("src.notifier.feishu.requests.post", fake_post)
    result = FeishuNotifier().check_callback_reachable(timeout=2)

    assert result["reachable"] is True
    assert captured["url"] == "https://stock.example.com/api/feishu/callback"
    assert captured["payload"]["token"] == "secret-token"
    assert captured["payload"]["action"]["value"]["action"] == "ping"
    assert captured["timeout"] == 2


def test_callback_reachability_fails_closed_without_public_url(monkeypatch):
    monkeypatch.setenv("FEISHU_CALLBACK_MODE", "http")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "secret-token")

    result = FeishuNotifier().check_callback_reachable()

    assert result["reachable"] is False
    assert "PUBLIC_BASE_URL" in result["reason"]


def test_callback_reachability_prefers_healthy_long_connection(monkeypatch):
    monkeypatch.setenv("FEISHU_CALLBACK_MODE", "auto")
    monkeypatch.setattr(
        "src.notifier.feishu_ws.get_long_connection_health",
        lambda: {"reachable": True, "reason": "ok", "transport": "ws"},
    )

    result = FeishuNotifier().check_callback_reachable()

    assert result == {"reachable": True, "reason": "ok", "transport": "ws"}


def test_auto_mode_does_not_fall_back_to_stale_public_callback(monkeypatch):
    monkeypatch.setenv("FEISHU_CALLBACK_MODE", "auto")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stale.example.com")
    monkeypatch.delenv("FEISHU_HTTP_FALLBACK_ENABLED", raising=False)
    monkeypatch.setattr(
        "src.notifier.feishu_ws.get_long_connection_health",
        lambda: {"reachable": False, "reason": "飞书长连接心跳已过期", "transport": "ws"},
    )
    monkeypatch.setattr(
        "src.notifier.feishu.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应探测旧公网回调")),
    )

    result = FeishuNotifier().check_callback_reachable()

    assert result["reachable"] is False
    assert result["transport"] == "ws"
    assert "心跳" in result["reason"]


def test_app_card_retries_transient_failure(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    monkeypatch.setenv("FEISHU_RECEIVE_ID", "chat-id")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "token")
    monkeypatch.setenv("NOTIFY_RETRY_MAX", "3")
    monkeypatch.setattr("time.sleep", lambda *_: None)

    calls = {"count": 0}

    class Response:
        def raise_for_status(self):
            if calls["count"] < 3:
                raise RuntimeError("temporary network failure")

        @staticmethod
        def json():
            return {"code": 0, "data": {"message_id": "retry-success"}}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        return Response()

    notifier = FeishuNotifier()
    monkeypatch.setattr(notifier, "_tenant_token", lambda: "tenant-token")
    monkeypatch.setattr("src.notifier.feishu.requests.post", fake_post)

    result = notifier.send_message("测试", "重试")

    assert result["status"] == "success"
    assert calls["count"] == 3
