"""Corrupt research files cannot silently become complete tracking evidence."""
import json
from datetime import date

import pytest

from src.tracking.tracker import RecommendationTracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    instance = RecommendationTracker(tmp_path, execution_lookup=lambda *_: None)
    monkeypatch.setattr("src.tracking.tracker.count_trading_days", lambda start, end: (end - start).days)
    monkeypatch.setattr(instance, "_fetch_market_data", lambda code, day: {
        "date": day, "open": 10, "close": 10.2, "high": 10.3, "low": 9.9,
        "_source": "synthetic",
    })
    return instance


def write_recommendation(tracker, day, code="000001"):
    folder = tracker.recommendations_dir / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "morning.json"
    path.write_text(json.dumps({"stock_recommendations": [{"code": code, "name": "合成样本",
        "action": "setup_ready", "entry_price": 10, "target_price": 11, "stop_loss_price": 9}]}), encoding="utf-8")
    return path


def test_non_date_tracker_files_and_directories_are_ignored(tracker):
    day = "2026-09-30"
    write_recommendation(tracker, day)
    for name in ("._2026-09-29.json", "notes.json", "2026-02-30.json", "20260929.json"):
        (tracker.tracker_dir / name).write_bytes(b" " * 45 + b"\xa3")
    invalid_folder = tracker.recommendations_dir / "._2026-09-29"
    invalid_folder.mkdir()
    (invalid_folder / "morning.json").write_bytes(b"\xa3")
    result = tracker.track_daily(day)
    assert result["total_tracked"] == 1
    assert result["data_quality"]["status"] == "ok"
    assert result["data_quality"]["diagnostics"] == []


@pytest.mark.parametrize("payload,kind", [
    (b" " * 45 + b"\xa3", "invalid_encoding"),
    (b"{broken", "invalid_json"),
    (b"[]", "invalid_schema"),
    (b'{"tracks":[null]}', "invalid_schema"),
    (b'{"tracks":[],"summary":{"win_rate_pct":NaN}}', "invalid_json"),
])
def test_corrupt_history_is_diagnosed_and_not_replaced_by_older_active_state(tracker, payload, kind):
    write_recommendation(tracker, "2026-09-28")
    tracker.track_daily("2026-09-28")
    bad = tracker.tracker_dir / "2026-09-29.json"
    bad.write_bytes(payload)
    result = tracker.track_daily("2026-09-30")
    assert result["status"] == "partial_success"
    assert result["data_quality"]["history_incomplete"] is True
    diagnostic = next(d for d in result["data_quality"]["diagnostics"] if d["path"] == "tracker/2026-09-29.json")
    assert diagnostic["kind"] == kind
    if kind == "invalid_encoding":
        assert diagnostic["offset"] == 45
    assert result["tracks"][0]["status"] == "history_incomplete"
    assert result["tracks"][0]["actual_return_pct"] is None
    assert result["summary"]["valid_samples"] == 0
    assert result["summary"]["statistics_trustworthy"] is False
    assert bad.read_bytes() == payload


def test_corrupt_morning_preserves_good_candidates_and_exposes_incomplete_population(tracker):
    bad = write_recommendation(tracker, "2026-09-29")
    bad.write_bytes(b"\xa3")
    write_recommendation(tracker, "2026-09-30", "600000")
    result = tracker.track_daily("2026-09-30")
    assert [t["code"] for t in result["tracks"]] == ["600000"]
    assert result["status"] == "partial_success"
    assert result["summary"]["statistics_trustworthy"] is False
    assert result["data_quality"]["diagnostics"][0]["path"] == "recommendations/2026-09-29/morning.json"
    assert bad.read_bytes() == b"\xa3"


def test_current_corrupt_tracking_file_is_not_overwritten(tracker):
    write_recommendation(tracker, "2026-09-30")
    target = tracker.tracker_dir / "2026-09-30.json"
    target.write_bytes(b"\xa3")
    result = tracker.track_daily("2026-09-30")
    assert result["persisted"] is False
    assert target.read_bytes() == b"\xa3"
    assert result["status"] == "partial_success"


def test_latest_fallback_is_labelled_and_scans_beyond_three_corrupt_files(tracker):
    write_recommendation(tracker, "2026-09-25")
    tracker.track_daily("2026-09-25")
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        (tracker.tracker_dir / f"{day}.json").write_bytes(b"\xa3")
    latest = tracker.get_latest_tracking()
    assert latest["tracking_date"] == "2026-09-25"
    assert latest["requested_tracking_date"] == "2026-09-30"
    assert latest["fallback_from"] == "2026-09-30"
    assert latest["data_quality"]["status"] == "incomplete"
    assert len(latest["data_quality"]["diagnostics"]) == 3
    assert latest["summary"]["statistics_trustworthy"] is False


def test_all_bad_tracking_returns_explicit_error_not_no_data(tracker):
    (tracker.tracker_dir / "2026-09-30.json").write_bytes(b"\xa3")
    latest = tracker.get_latest_tracking()
    assert latest["status"] == "error"
    assert latest["tracks"] == []
    assert latest["data_quality"]["history_incomplete"] is True
    dated = tracker.get_tracking("2026-09-30")
    assert dated["status"] == "error"
    assert dated["data_quality"]["diagnostics"] == latest["data_quality"]["diagnostics"]


def test_known_terminal_state_before_gap_remains_immutable_but_global_quality_is_incomplete(tracker, monkeypatch):
    write_recommendation(tracker, "2026-09-28")
    monkeypatch.setattr(tracker, "_fetch_market_data", lambda code, day: {
        "date": day, "open": 10, "close": 11.2, "high": 11.3, "low": 9.9,
    })
    assert tracker.track_daily("2026-09-28")["tracks"][0]["status"] == "hit_target"
    (tracker.tracker_dir / "2026-09-29.json").write_bytes(b"\xa3")
    result = tracker.track_daily("2026-09-30")
    assert result["tracks"][0]["status"] == "hit_target"
    assert result["summary"]["statistics_trustworthy"] is False


def test_incomplete_history_does_not_generate_performance_advice(tracker):
    (tracker.tracker_dir / "2026-09-29.json").write_text(json.dumps({
        "tracking_date": "2026-09-29", "tracks": [{"code": "000001", "status": "active"}],
        "summary": {"closed_samples": 5, "win_rate_pct": 20},
    }), encoding="utf-8")
    (tracker.tracker_dir / "2026-09-30.json").write_bytes(b"\xa3")
    suggestions = tracker.get_improvement_suggestions()
    assert [s["type"] for s in suggestions] == ["data_quality"]


def test_weekly_review_keeps_data_quality_when_no_valid_snapshot_survives(tracker, monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 30)
    monkeypatch.setattr("src.tracking.tracker.date", FixedDate)
    (tracker.tracker_dir / "2026-09-30.json").write_bytes(b"\xa3")
    reviews = tracker.get_weekly_review(weeks=1)
    assert len(reviews) == 1
    assert reviews[0]["data_quality"]["status"] == "incomplete"
    assert reviews[0]["statistics_trustworthy"] is False
    assert reviews[0]["top_winners"] == []


def test_diagnostics_are_safe_and_survive_new_snapshot_and_restart(tracker):
    write_recommendation(tracker, "2026-09-28")
    tracker.track_daily("2026-09-28")
    bad = tracker.tracker_dir / "2026-09-29.json"
    bad.write_bytes(b"\xa3")
    tracker.track_daily("2026-09-30")
    other = RecommendationTracker(tracker.data_dir, execution_lookup=lambda *_: None)
    latest = other.get_latest_tracking()
    assert latest["data_quality"]["diagnostics"] == [
        {"path": "tracker/2026-09-29.json", "kind": "invalid_encoding", "offset": 0}]
    assert latest["tracks"][0]["actual_return_pct"] is None
    saved = json.loads((tracker.tracker_dir / "2026-09-30.json").read_text())
    saved["data_quality"]["diagnostics"].append({"path": "/etc/private", "kind": "invalid_encoding",
        "message": "must not propagate content"})
    saved["data_quality"]["diagnostics"].append({"path": "tracker/../../.env", "kind": "read_error"})
    (tracker.tracker_dir / "2026-09-30.json").write_text(json.dumps(saved), encoding="utf-8")
    assert other.get_latest_tracking()["data_quality"]["diagnostics"] == latest["data_quality"]["diagnostics"]


def test_valid_snapshot_after_old_damage_does_not_invent_a_new_gap(tracker):
    write_recommendation(tracker, "2026-09-29")
    tracker.track_daily("2026-09-29")
    (tracker.tracker_dir / "2026-09-28.json").write_bytes(b"\xa3")
    result = tracker.track_daily("2026-09-30")
    assert result["tracks"][0]["status"] == "active"
    assert result["data_quality"]["status"] == "incomplete"


def test_get_tracking_rejects_path_traversal_without_opening_files(tracker):
    assert tracker.get_tracking("../../outside") is None
    assert tracker.get_tracking("2026-02-30") is None


@pytest.mark.parametrize("summary", [{}, {"statistics_trustworthy": False}])
def test_valid_json_does_not_certify_legacy_statistics(tracker, summary):
    (tracker.tracker_dir / "2026-09-30.json").write_text(json.dumps({
        "tracking_date": "2026-09-30", "tracks": [], "summary": summary,
    }), encoding="utf-8")
    result = tracker.get_latest_tracking()
    assert result["data_quality"]["status"] == "ok"
    assert result["summary"] == summary
