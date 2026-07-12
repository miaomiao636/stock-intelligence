# -*- coding: utf-8 -*-
"""API写入口和飞书回调的安全门禁。"""

from fastapi.testclient import TestClient

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


def test_feishu_callback_has_independent_token_auth(monkeypatch):
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected")
    client = TestClient(server.app)
    response = client.post(
        "/api/feishu/callback",
        json={"type": "url_verification", "token": "wrong", "challenge": "x"},
    )
    assert response.status_code == 401


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
