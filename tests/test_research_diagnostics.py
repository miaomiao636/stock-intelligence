from datetime import date, datetime
import json
import sqlite3

from fastapi.testclient import TestClient
import server


def test_diagnostics_empty_does_not_manufacture_account(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    response = TestClient(server.app).get("/api/research/diagnostics")
    assert response.status_code == 200
    data = response.json()
    assert data["tracking"]["available"] is False
    assert data["execution"]["trade_date"] is None
    assert data["execution"]["summary"]["total"] == 0
    assert data["scorecard"]["summary"]["accuracy_pct"] is None
    path = tmp_path / "stock_intelligence.db"
    if path.exists():
        with sqlite3.connect(path) as conn:
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='trading_accounts'").fetchone()


def test_execution_diagnostics_latest_past_day_not_future_or_truncated(tmp_path):
    from src.research.diagnostics import execution_diagnostics
    from src.storage.trading_ledger import TradingLedger
    from src.paper_trading.entry_plans import EntryPlanStore
    ledger = TradingLedger(tmp_path)
    store = EntryPlanStore(ledger)
    for i in range(105):
        plan = store.register({"code": f"{i:06d}"}, run_id="test", trade_date="2026-09-30",
                              now=datetime.fromisoformat("2026-09-30T09:35:00+08:00"))
        store.update(plan["plan_id"], "rejected", "推荐置信度不足", datetime.fromisoformat("2026-09-30T09:35:01+08:00"))
    store.register({"code": "600001"}, run_id="future", trade_date="2026-10-08",
                   now=datetime.fromisoformat("2026-10-08T09:35:00+08:00"))
    data = execution_diagnostics(tmp_path, date(2026, 10, 3))
    assert data["requested_date"] == "2026-10-03"
    assert data["trade_date"] == "2026-09-30"
    assert data["is_latest_available"] is True
    assert data["summary"]["total"] == 105
    assert data["summary"]["reason_counts"] == {"推荐置信度不足": 105}
    assert "历史" in data["note"]


def test_diagnostics_exposes_stale_tracking_without_a_success_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    tracker = tmp_path / "tracker"
    tracker.mkdir()
    (tracker / "2026-07-17.json").write_text(json.dumps({"tracking_date": "2026-07-17", "tracks": [], "summary": {}}))
    data = TestClient(server.app).get("/api/research/diagnostics").json()
    assert data["tracking"]["available"] is True
    assert data["tracking"]["freshness"]["status"] == "stale"


def test_diagnostics_invalid_date_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    assert TestClient(server.app).get("/api/research/diagnostics?trade_date=invalid").status_code == 422


def test_public_scorecard_reads_persisted_outcomes_without_database_changes(tmp_path, monkeypatch):
    from src.research.store import ResearchStore
    from tests.test_research_maturity import synthetic_report, calendar, observation
    now = ["2026-09-21T10:01:00+08:00"]
    store = ResearchStore(tmp_path / "stock_intelligence.db", clock=lambda: now[0])
    store.import_report(synthetic_report(), "fixture")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    with sqlite3.connect(store.db_path) as conn:
        before = list(conn.iterdump())
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr("requests.post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("GET must not fetch")))
    response = TestClient(server.app).get("/api/research/scorecard")
    assert response.status_code == 200
    assert response.json()["summary"]["evaluable"] == 1
    assert response.json()["summary"]["correct"] == 1
    with sqlite3.connect(store.db_path) as conn:
        assert list(conn.iterdump()) == before
