from fastapi.testclient import TestClient
import server


def test_research_read_contract_and_isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    client = TestClient(server.app)
    response = client.get("/api/research/overview")
    assert response.status_code == 200
    assert response.json()["judgments_count"] == 0
    assert client.get("/api/research/judgments").json() == {"items": []}
    assert client.get("/api/research/lessons").json() == {"items": []}
    assert client.get("/api/research/judgments/does-not-exist").status_code == 404
    assert client.get("/api/research/judgments?code=bad").status_code == 422


def test_research_chat_auth_validation_and_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "API_KEY", "test-key")
    client = TestClient(server.app)
    assert client.post("/api/research/chat", json={"question": "为什么"}).status_code == 401
    headers = {"X-API-Key": "test-key"}
    response = client.post("/api/research/chat", json={"question": "为什么没交易？"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["read_only"]
    assert response.json()["usage"]["model_calls"] == 0
    assert client.post("/api/research/chat", json={"question": "x" * 1201}, headers=headers).status_code == 422
    assert client.post("/api/research/chat", json={"question": "x", "mode": "execute"}, headers=headers).status_code == 422
    assert client.post("/api/research/chat", json={"question": "   "}, headers=headers).status_code == 400
    assert client.post("/api/research/chat", json={"question": "x", "mode": "experts"}, headers=headers).status_code == 400
    assert client.get("/api/research/entry-plans?trade_date=invalid").status_code == 400


def test_tracking_response_has_freshness_and_unknown_coverage(monkeypatch):
    class Tracker:
        def get_latest_tracking(self):
            return {"tracking_date": "2026-07-17", "tracks": [], "summary": {"win_rate_pct": 50}}
    monkeypatch.setattr("src.tracking.tracker.RecommendationTracker", Tracker)
    response = TestClient(server.app).get("/api/tracking").json()
    assert response["freshness"]["status"] == "stale"
    assert response["summary"]["execution_coverage_pct"] is None
