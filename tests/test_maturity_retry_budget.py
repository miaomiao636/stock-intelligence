"""Budgeted maturity retries must make progress past unavailable old samples."""
import sqlite3

import pytest

from src.research.outcomes import OutcomeProvider
from src.research.store import ResearchStore
from tests.test_research_maturity import synthetic_report


@pytest.mark.parametrize("old_sample_state", ["missing_close", "corporate_action"])
def test_limited_retries_eventually_review_later_healthy_samples(tmp_path, monkeypatch, old_sample_state):
    now = ["2026-09-21T10:01:00+08:00"]
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: now[0])
    ids = []
    for i in range(8):
        imported = store.import_report(synthetic_report(code=f"600{i:03d}"), f"fixture:{i}")
        ids.append(imported["judgment_ids"][0])
    calls = []

    class Response:
        status_code = 200

        def __init__(self, fields, rows):
            self.payload = {"code": 0, "data": {"fields": fields.split(","), "items": rows}}

        def json(self):
            return self.payload

    def request(url, **kwargs):
        assert url == "https://api.tushare.pro"
        payload = kwargs["json"]
        api, params = payload["api_name"], payload["params"]
        symbol = params.get("ts_code")
        calls.append((api, symbol))
        if api == "trade_cal":
            rows = [["20260921", 1], ["20260922", 1], ["20260923", 1]]
        elif api == "daily":
            rows = [] if old_sample_state == "missing_close" and symbol != "600007.SH" else [[symbol, "20260923", 11]]
        else:
            factor = 2 if old_sample_state == "corporate_action" and symbol != "600007.SH" else 1
            rows = [[symbol, "20260921", 1], [symbol, "20260923", factor]]
        return Response(payload["fields"], rows)

    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")
    monkeypatch.setattr("requests.post", request)
    newest_requested = False
    for attempt in range(4):
        now[0] = f"2026-09-23T16:0{attempt}:00+08:00"
        calls.clear()
        provider = OutcomeProvider(max_requests=8)
        store.review_matured_predictions(as_of=now[0], calendar_provider=provider.calendar,
                                        observation_provider=provider.observation)
        assert len(calls) <= 8
        newest_requested = newest_requested or any(symbol == "600007.SH" for _, symbol in calls)

    assert newest_requested
    assert store.list_reviews(ids[-1])[0]["prediction_status"] == "correct"
    summary = store.get_prediction_scorecard()["summary"]
    assert summary["evaluable"] == 1
    assert summary["correct"] == 1
    assert all(review["prediction_status"] == "inconclusive"
               for item_id in ids[:-1] for review in store.list_reviews(item_id))


def test_budget_exhaustion_does_not_record_an_unqueried_forecast_as_failed(tmp_path, monkeypatch):
    from tests.test_research_maturity import imported_store

    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    provider = OutcomeProvider(max_requests=0)
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")
    monkeypatch.setattr("requests.post", lambda *a, **kw: pytest.fail("budget is zero"))
    result = store.review_matured_predictions(as_of=now[0], calendar_provider=provider.calendar,
                                            observation_provider=provider.observation)
    assert store.list_reviews(item_id) == []
    assert result.get("deferred_count") == 1
    assert result["calendar_unavailable_count"] == 0
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_maturity_attempts").fetchone()[0] == 0


def test_identical_unavailable_retries_advance_queue_without_rewriting_reviews(tmp_path):
    from tests.test_research_maturity import calendar, imported_store

    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    result = store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
                                            observation_provider=lambda *_: None)
    assert result["created_count"] == 1
    first_review = store.list_reviews(item_id)[0]
    with sqlite3.connect(store.db_path) as conn:
        first_attempt = conn.execute("SELECT last_attempt_at FROM research_maturity_attempts WHERE judgment_id=?",
                                     (item_id,)).fetchone()[0]

    now[0] = "2026-09-23T16:01:00+08:00"
    result = store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
                                            observation_provider=lambda *_: None)
    assert result["created_count"] == 0
    assert store.list_reviews(item_id) == [first_review]
    with sqlite3.connect(store.db_path) as conn:
        attempts = conn.execute("SELECT last_attempt_at FROM research_maturity_attempts WHERE judgment_id=?",
                                (item_id,)).fetchall()
    assert len(attempts) == 1
    assert attempts[0][0] > first_attempt
