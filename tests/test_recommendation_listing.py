"""Recent report discovery and stable unread revisions (synthetic files only)."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import server
from src.reporting import report_store


@pytest.fixture
def reports(tmp_path, monkeypatch):
    class HolidayDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 7)

    monkeypatch.setattr(server, "date", HolidayDate)
    monkeypatch.setattr(report_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_enrich_stock_prices", lambda report: None)
    def write(day, kind="morning", **extra):
        directory = tmp_path / "recommendations" / day
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"date": day, "stock_recommendations": [{"code": "600000", "action": "watch"}], **extra}
        (directory / f"{kind}.json").write_text(json.dumps(payload), encoding="utf-8")
        return payload
    return write


def test_long_holiday_keeps_latest_available_day_and_excludes_future(reports):
    reports("2026-09-30")
    reports("2026-09-30", "afternoon")
    reports("2026-09-30", "closing")
    reports("2026-10-08")
    reports("._2026-10-06")
    result = TestClient(server.app).get("/api/recommendations/all").json()
    assert [row["type"] for row in result] == ["morning", "afternoon", "closing"]
    assert {row["date"] for row in result} == {"2026-09-30"}


def test_revision_ignores_live_quote_enrichment_but_detects_report_edit(reports, monkeypatch):
    reports("2026-10-07")
    quote = {"price": 10}
    monkeypatch.setattr(server, "_enrich_stock_prices", lambda report: report["stock_recommendations"][0].update(latest_price=quote["price"]))
    client = TestClient(server.app)
    first = client.get("/api/recommendations/all").json()[0]
    quote["price"] = 12
    second = client.get("/api/recommendations/all").json()[0]
    assert first["revision"] == second["revision"]
    assert first["data"]["stock_recommendations"][0]["latest_price"] != second["data"]["stock_recommendations"][0]["latest_price"]
    reports("2026-10-07", market_summary="更新后的判断")
    third = client.get("/api/recommendations/all").json()[0]
    assert third["revision"] != first["revision"]


def test_single_report_fallback_also_survives_holiday(reports):
    reports("2026-09-30")
    response = TestClient(server.app).get("/api/recommendation")
    assert response.status_code == 200
    assert response.json()["date"] == "2026-09-30"
