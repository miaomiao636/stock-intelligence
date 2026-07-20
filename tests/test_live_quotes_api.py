# -*- coding: utf-8 -*-
"""Dashboard 60秒只读行情接口回归测试。"""

from fastapi.testclient import TestClient

import server


def test_live_quotes_marks_positions_to_market_without_writing(monkeypatch):
    class FakeService:
        def get_positions(self, _=None):
            return [{
                "code": "000001",
                "name": "平安银行",
                "quantity": 100,
                "avg_cost": 10.0,
                "current_price": 9.8,
            }]

        def get_account(self):
            return {"initial_cash": 4000, "cash": 2900, "updated_at": "2026-07-20T10:00:00"}

        def get_performance_metrics(self):
            return {
                "initial_cash": 4000,
                "effective_principal": 4000,
                "max_drawdown_pct": 0,
            }

    monkeypatch.setattr(server, "_collect_live_quote_codes", lambda service: ["000001"])
    monkeypatch.setattr(
        "src.paper_trading.trading_service.TradingService",
        FakeService,
    )
    monkeypatch.setattr(
        "src.data_collectors.realtime_prices.fetch_realtime_prices",
        lambda codes: {
            "000001": {
                "code": "000001",
                "price": 10.5,
                "quote_time": "2026-07-20T10:05:00+08:00",
                "source": "test",
            }
        },
    )
    monkeypatch.setattr(
        "src.data_collectors.market_data.get_realtime_market_overview",
        lambda: {"date": "2026-07-20", "indices": {"000001": {"close": 3800}}},
    )

    response = TestClient(server.app).get("/api/live/quotes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["refresh_after_seconds"] == 60
    assert payload["positions"][0]["current_price"] == 10.5
    assert payload["positions"][0]["market_value"] == 1050.0
    assert payload["positions"][0]["unrealized_pnl"] == 50.0
    assert payload["account"]["market_value"] == 1050.0
    assert payload["account"]["total_equity"] == 3950.0
    assert payload["account"]["net_return_after_costs"] == -50.0


def test_dashboard_polls_read_only_live_quotes_every_60_seconds():
    html = (server.PROJECT_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert "apiFetch('/api/live/quotes')" in html
    assert "setInterval(refreshData, 60 * 1000)" in html
    assert "if (document.hidden || refreshInFlight) return" in html
    assert "document.addEventListener('visibilitychange'" in html
    assert "s.latest_price ?? s.current_price" in html
