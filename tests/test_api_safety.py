# -*- coding: utf-8 -*-
"""API写入口和飞书回调的安全门禁。"""

from fastapi.testclient import TestClient
import json
import yaml

import server


def test_health_reports_safety_flags(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["version"] == "1.0.0"
    assert response.json()["data"]["api_writes_enabled"] is False


def test_mutating_api_is_closed_without_api_key(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)
    response = client.post("/api/paper/execute-due")
    assert response.status_code == 503


def test_read_account_is_public_but_cash_update_requires_api_key(monkeypatch):
    """看盘不应要求密钥；任何资金变更仍必须由服务端校验密钥。"""
    class StubTradingService:
        def get_account(self):
            return {"cash": 4000.0}

        def update_available_cash(self, cash):
            return {"cash": cash, "adjusted_principal": cash}

    monkeypatch.setattr(server, "API_KEY", "test-key")
    monkeypatch.setattr("src.paper_trading.trading_service.TradingService", StubTradingService)
    client = TestClient(server.app)

    assert client.get("/api/account").status_code == 200
    denied = client.put("/api/account", json={"cash": 5200})
    allowed = client.put(
        "/api/account",
        json={"cash": 5200},
        headers={"X-API-Key": "test-key"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["account"]["cash"] == 5200


def test_manual_analysis_regeneration_is_authenticated_and_compact(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "test-key")
    monkeypatch.setattr(
        "src.orchestrator.run_morning_pipeline",
        lambda **kwargs: {
            "status": "success",
            "source_status": {"llm": "success"},
            "errors": [],
            "report": {"date": "2026-07-14"},
        },
    )
    client = TestClient(server.app)

    denied = client.post("/api/recommendation/regenerate")
    response = client.post(
        "/api/recommendation/regenerate",
        headers={"X-API-Key": "test-key"},
    )

    assert denied.status_code == 401
    assert response.status_code == 200
    assert response.json()["data"]["llm_status"] == "success"
    assert "report" not in response.json()["data"]


def test_feishu_callback_returns_challenge_for_url_verification(monkeypatch):
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    client = TestClient(server.app)
    response = client.post(
        "/api/feishu/callback",
        json={"type": "url_verification", "token": "wrong", "challenge": "x"},
    )
    assert response.status_code == 200
    assert response.json()["challenge"] == "x"


def test_feishu_callback_has_independent_token_auth(monkeypatch):
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    client = TestClient(server.app)
    response = client.post(
        "/api/feishu/callback",
        json={"header": {"event_id": "evt-0"}, "event": {"action": {"value": {"action": "ping"}}}},
    )
    assert response.status_code == 401


def test_feishu_callback_probe_ping(monkeypatch):
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    client = TestClient(server.app)
    response = client.post(
        "/api/feishu/callback",
        json={
            "token": "expected",
            "header": {"event_id": "evt-1"},
            "event": {
                "operator": {"open_id": "ou_xxx"},
                "action": {"value": {"action": "ping", "order_id": "feishu-probe"}},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["toast"]["content"] == "飞书回调链路正常"


def test_feishu_callback_unknown_payload_is_not_fatal(monkeypatch):
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    client = TestClient(server.app)
    response = client.post(
        "/api/feishu/callback",
        json={"token": "expected", "event": {"action": {"value": {"foo": "bar"}}}},
    )
    assert response.status_code == 200
    assert "回调已收到" in response.json()["toast"]["content"]


def test_feishu_callback_legacy_ping_is_accepted(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    client = TestClient(server.app)
    response = client.post(
        "/api/feishu/callback",
        headers={
            "x-lark-signature": "sig",
            "x-lark-request-timestamp": "ts",
        },
        json={
            "app_id": "cli_test",
            "open_message_id": "om_xxx",
            "open_chat_id": "oc_xxx",
            "action": {"value": {"action": "ping", "order_id": "feishu-probe"}, "tag": "button"},
        },
    )
    assert response.status_code == 200
    assert response.json()["toast"]["content"] == "飞书回调链路正常"


def test_feishu_callback_does_not_persist_raw_secrets(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    debug_log = tmp_path / "feishu_callback_debug.log"
    monkeypatch.setattr(server, "FEISHU_DEBUG_LOG", debug_log, raising=False)
    client = TestClient(server.app)
    marker = "sensitive-marker-must-not-be-logged"

    response = client.post(
        "/api/feishu/callback",
        headers={"x-sensitive-test": marker},
        json={
            "token": "expected",
            "event": {"action": {"value": {"action": "ping", "note": marker}}},
        },
    )

    assert response.status_code == 200
    assert not debug_log.exists()
    assert marker not in caplog.text


def test_latest_quote_does_not_rewrite_historical_signal(monkeypatch):
    from src.data_collectors import realtime_prices

    monkeypatch.setattr(realtime_prices, "fetch_realtime_prices", lambda codes: {
        "000001": {"price": 10.5, "quote_time": "2026-07-13T10:00:00+08:00", "source": "test"}
    })
    report = {"stock_recommendations": [{
        "code": "000001",
        "current_price": 9.8,
        "entry_price": 9.7,
        "target_price": 10.2,
        "stop_loss_price": 9.4,
    }]}
    server._enrich_stock_prices(report)
    stock = report["stock_recommendations"][0]
    assert stock["current_price"] == 9.8
    assert stock["entry_price"] == 9.7
    assert stock["target_price"] == 10.2
    assert stock["stop_loss_price"] == 9.4
    assert stock["latest_price"] == 10.5


def test_recommendation_tracking_keeps_execution_truth_fields(tmp_path, monkeypatch):
    rec_dir = tmp_path / "recommendations" / "2026-07-13"
    tracker_dir = tmp_path / "tracker"
    rec_dir.mkdir(parents=True)
    tracker_dir.mkdir()
    (rec_dir / "morning.json").write_text(
        json.dumps({"stock_recommendations": [{"code": "000001", "name": "平安银行"}]}),
        encoding="utf-8",
    )
    (tracker_dir / "2026-07-13.json").write_text(
        json.dumps({
            "tracks": [{
                "code": "000001",
                "recommendation_date": "2026-07-13",
                "status": "not_triggered",
                "execution_status": "not_triggered",
                "return_basis": "no_position_no_return",
                "planned_entry_price": 10.0,
                "entry_price": None,
                "actual_return_pct": None,
                "data_date": "2026-07-13",
                "is_met_expectation": False,
                "is_failed": False,
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    client = TestClient(server.app)

    response = client.get("/api/recommendations?with_tracking=true&limit=1")

    assert response.status_code == 200
    tracking = response.json()[0]["stocks"][0]["tracking"]
    assert tracking["execution_status"] == "not_triggered"
    assert tracking["return_basis"] == "no_position_no_return"
    assert tracking["planned_entry_price"] == 10.0
    assert tracking["entry_price"] is None
    assert tracking["actual_return_pct"] is None


def test_strategy_update_is_authenticated_validated_and_persisted(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    strategy_file = config_dir / "strategy.yaml"
    strategy_file.write_text(
        yaml.safe_dump({"custom_params": {"max_stock_price": 20}}, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "API_KEY", "test-key")
    client = TestClient(server.app)

    denied = client.put("/api/strategy/params", json={"custom_params": {"max_stock_price": 50}})
    saved = client.put(
        "/api/strategy/params",
        json={"custom_params": {"max_stock_price": 50}},
        headers={"X-API-Key": "test-key"},
    )
    invalid = client.put(
        "/api/strategy/params",
        json={"custom_params": {"max_stock_price": 5001}},
        headers={"X-API-Key": "test-key"},
    )

    assert denied.status_code == 401
    assert saved.status_code == 200
    assert saved.json()["strategy"]["custom_params"]["max_stock_price"] == 50
    assert yaml.safe_load(strategy_file.read_text(encoding="utf-8"))["custom_params"]["max_stock_price"] == 50
    assert invalid.status_code == 400
