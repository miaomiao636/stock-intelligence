"""Per-candidate failures must not freeze the daily tracking artifact."""
import json
from datetime import date

import pytest

from src.tracking.tracker import RecommendationTracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    instance = RecommendationTracker(tmp_path, execution_lookup=lambda *_: None)
    monkeypatch.setattr("src.tracking.tracker.count_trading_days", lambda start, end: (end - start).days)
    folder = instance.recommendations_dir / "2026-09-30"
    folder.mkdir(parents=True)
    (folder / "morning.json").write_text(json.dumps({"stock_recommendations": [
        {"code": code, "name": "合成样本", "entry_price": 10, "target_price": 11,
         "stop_loss_price": 9, "action": "setup_ready"}
        for code in ("000001", "000002")
    ]}), encoding="utf-8")
    return instance


def bar(day):
    return {"date": day, "open": 10, "close": 10.2, "high": 10.3, "low": 9.9}


@pytest.mark.parametrize("bad_response", [None, [], "not a quote"])
def test_malformed_provider_response_does_not_abort_other_candidates(tracker, monkeypatch, bad_response):
    monkeypatch.setattr(tracker, "_fetch_market_data", lambda code, day: bad_response if code == "000001" else bar(day))
    result = tracker.track_daily("2026-09-30")
    assert result["persisted"] is True
    assert [t["status"] for t in result["tracks"]] == ["data_error", "active"]
    assert result["tracks"][0]["actual_return_pct"] is None
    assert result["summary"]["valid_samples"] == 1
    assert result["tracking_quality"]["data_error_samples"] == 1
    assert result["summary"]["statistics_trustworthy"] is False


def test_provider_exception_is_isolated_without_exposing_exception_content(tracker, monkeypatch):
    def fetch(code, day):
        if code == "000001":
            raise RuntimeError("provider failed with sensitive token do-not-publish")
        return bar(day)

    monkeypatch.setattr(tracker, "_fetch_market_data", fetch)
    result = tracker.track_daily("2026-09-30")
    assert [t["status"] for t in result["tracks"]] == ["data_error", "active"]
    assert "do-not-publish" not in json.dumps(result)
    assert result["tracks"][0]["actual_return_pct"] is None


def test_provider_error_payload_does_not_echo_sensitive_details(tracker, monkeypatch):
    monkeypatch.setattr(tracker, "_fetch_market_data", lambda code, day: {
        "error": "HTTP request failed with token=synthetic-private-token",
        "_source": "https://provider.invalid/?token=synthetic-private-token",
        "date": "synthetic-private-token",
    } if code == "000001" else bar(day))
    result = tracker.track_daily("2026-09-30")
    assert result["tracks"][0]["status"] == "data_error"
    assert result["tracks"][1]["status"] == "active"
    assert "synthetic-private-token" not in json.dumps(result)


def test_invalid_candidate_field_is_isolated_without_losing_other_candidates(tracker, monkeypatch):
    path = tracker.recommendations_dir / "2026-09-30" / "morning.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stock_recommendations"][0]["reason"] = {"unexpected": "object"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(tracker, "_fetch_market_data", lambda code, day: {
        **bar(day), "close": 11.2, "high": 11.3,
    })
    result = tracker.track_daily("2026-09-30")
    assert [t["status"] for t in result["tracks"]] == ["data_error", "hit_target"]
    assert result["tracks"][0]["actual_return_pct"] is None
    assert result["persisted"] is True


def test_execution_lookup_failure_is_not_replaced_by_theoretical_entry(tracker, monkeypatch):
    path = tracker.recommendations_dir / "2026-09-30" / "morning.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["run_id"] = "synthetic-run"
    path.write_text(json.dumps(payload), encoding="utf-8")
    def lookup(recommendation_id, code):
        if code == "000001":
            raise RuntimeError("private lookup path")
        return None
    tracker.execution_lookup = lookup
    monkeypatch.setattr(tracker, "_fetch_market_data", lambda code, day: bar(day))
    result = tracker.track_daily("2026-09-30")
    assert [t["status"] for t in result["tracks"]] == ["data_error", "active"]
    assert result["tracks"][0]["execution_status"] == "unknown"
    assert result["tracks"][0]["actual_return_pct"] is None
    assert "private lookup path" not in json.dumps(result)


@pytest.mark.parametrize("field,value", [
    ("close", float("nan")), ("high", float("inf")), ("low", "NaN"),
    ("open", True), ("high", None), ("low", 12),
])
def test_invalid_ohlc_is_unscored_and_persisted_as_strict_json(tracker, monkeypatch, field, value):
    def fetch(code, day):
        value_bar = bar(day)
        if code == "000001":
            value_bar[field] = value
        return value_bar

    monkeypatch.setattr(tracker, "_fetch_market_data", fetch)
    result = tracker.track_daily("2026-09-30")
    assert result["tracks"][0]["status"] == "data_error"
    assert result["tracks"][0]["actual_return_pct"] is None
    assert result["tracks"][1]["status"] == "active"
    json.dumps(result, allow_nan=False)
    assert tracker.get_latest_tracking()["tracking_date"] == "2026-09-30"


def test_legacy_terminal_without_derived_flags_does_not_crash_summary(tracker, monkeypatch):
    monkeypatch.setattr(tracker, "_fetch_market_data", lambda code, day: bar(day))
    path = tracker.tracker_dir / "2026-09-30.json"
    path.write_text(json.dumps({"tracking_date": "2026-09-30", "tracks": [{
        "recommendation_date": "2026-09-30", "code": "000001", "sector": "金融",
        "status": "hit_target", "execution_status": "theoretical_trigger", "actual_return_pct": 5.0,
    }]}), encoding="utf-8")
    result = tracker.track_daily("2026-09-30")
    assert result["persisted"] is True
    assert result["tracks"][0]["status"] == "hit_target"
    assert result["summary"]["sector_summary"]["金融"]["hit"] == 1


def test_snapshot_date_mismatch_is_not_reported_as_a_newer_valid_snapshot(tracker):
    older = tracker.tracker_dir / "2026-09-29.json"
    older.write_text(json.dumps({"tracking_date": "2026-09-29", "tracks": []}), encoding="utf-8")
    mismatched = tracker.tracker_dir / "2026-09-30.json"
    original = json.dumps({"tracking_date": "2026-07-17", "tracks": [], "summary": {"win_rate_pct": 99}})
    mismatched.write_text(original, encoding="utf-8")
    result = tracker.get_latest_tracking()
    assert result["tracking_date"] == "2026-09-29"
    assert result["fallback_from"] == "2026-09-30"
    assert result["data_quality"]["diagnostics"] == [{"path": "tracker/2026-09-30.json", "kind": "invalid_schema"}]
    assert mismatched.read_text(encoding="utf-8") == original


def test_stale_tracking_does_not_generate_performance_tuning_advice(tracker, monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 3)

    monkeypatch.setattr("src.tracking.tracker.date", FixedDate)
    (tracker.tracker_dir / "2026-07-17.json").write_text(json.dumps({
        "tracking_date": "2026-07-17", "tracks": [{"code": "000001", "status": "active"}],
        "summary": {"closed_samples": 12, "win_rate_pct": 20, "valid_samples": 12, "avg_return_pct": -5},
    }), encoding="utf-8")
    suggestions = tracker.get_improvement_suggestions()
    assert [s["type"] for s in suggestions] == ["data_freshness"]


@pytest.mark.parametrize("invalid_return", [float("nan"), float("inf"), True])
def test_non_finite_or_boolean_returns_never_enter_summary(tracker, invalid_return):
    summary = tracker._calculate_summary([{
        "status": "hit_target", "execution_status": "filled", "actual_return_pct": invalid_return,
        "is_met_expectation": True, "is_failed": False,
    }])
    assert summary["valid_samples"] == 0
    assert summary["closed_samples"] == 0
    json.dumps(summary, allow_nan=False)


def test_weekly_review_keeps_daily_evaluation_quality(tracker, monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 3)

    monkeypatch.setattr("src.tracking.tracker.date", FixedDate)
    (tracker.tracker_dir / "2026-09-30.json").write_text(json.dumps({
        "tracking_date": "2026-09-30", "tracks": [{
            "code": "600000", "name": "合成样本", "recommendation_date": "2026-09-30",
            "status": "data_error", "execution_status": "unknown", "actual_return_pct": None,
        }], "summary": {},
    }), encoding="utf-8")
    daily = tracker.get_tracking("2026-09-30")
    weekly = tracker.get_weekly_review(1)[0]
    assert daily["tracking_quality"]["status"] == "incomplete"
    assert weekly["data_error"] == 1
    assert weekly["tracking_quality"]["status"] == "incomplete"
    assert weekly["statistics_trustworthy"] is False
