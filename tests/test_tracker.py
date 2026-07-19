# -*- coding: utf-8 -*-

from src.tracking.tracker import RecommendationTracker


def test_tracker_tolerates_null_stop_loss_pct(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(tracker, "_fetch_price", lambda code: 10.0)

    result = tracker._track_stock(
        {
            "code": "000001",
            "name": "平安银行",
            "entry_price": 10.0,
            "target_return_pct": None,
            "stop_loss_pct": None,
        },
        "2026-07-10",
        "2026-07-13",
    )

    assert result["stop_loss_price"] == 9.5
    assert result["target_return_pct"] == 0
    assert result["status"] == "active"


def test_tracker_win_rate_uses_closed_samples_only(tmp_path):
    tracker = RecommendationTracker(tmp_path)
    tracks = [
        {
            "status": "hit_target",
            "actual_return_pct": 6.0,
            "sector": "金融",
            "is_met_expectation": True,
            "is_failed": False,
        },
        {
            "status": "stopped_out",
            "actual_return_pct": -3.0,
            "sector": "金融",
            "is_met_expectation": False,
            "is_failed": True,
        },
        {
            "status": "active",
            "actual_return_pct": 1.0,
            "sector": "金融",
            "is_met_expectation": False,
            "is_failed": False,
        },
    ]

    summary = tracker._calculate_summary(tracks)

    assert summary["closed_samples"] == 2
    assert summary["pending_samples"] == 1
    assert summary["win_rate_pct"] == 50.0
    assert summary["sample_coverage_pct"] == 66.7
    assert summary["sector_summary"]["金融"]["win_rate"] == 50.0


def test_tracker_excludes_watch_and_track_from_performance(tmp_path, monkeypatch):
    rec_dir = tmp_path / "recommendations" / "2026-07-13"
    rec_dir.mkdir(parents=True)
    (rec_dir / "morning.json").write_text(
        '{"stock_recommendations": ['
        '{"code": "000001", "action": "setup_ready", "entry_price": 10},'
        '{"code": "000002", "action": "track", "entry_price": 10},'
        '{"code": "000003", "action": "watch", "entry_price": null}'
        ']}',
        encoding="utf-8",
    )
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(tracker, "_fetch_price", lambda code: 10.5)

    result = tracker.track_daily("2026-07-13")

    assert result["total_tracked"] == 1
    assert result["excluded_non_actionable"] == 2
    assert [track["code"] for track in result["tracks"]] == ["000001"]


def test_tracker_preserves_terminal_state_on_later_runs(tmp_path, monkeypatch):
    rec_dir = tmp_path / "recommendations" / "2026-07-10"
    rec_dir.mkdir(parents=True)
    (rec_dir / "morning.json").write_text(
        '{"stock_recommendations": ['
        '{"code": "000001", "action": "setup_ready", '
        '"entry_price": 10, "target_price": 11, "stop_loss_price": 9}'
        ']}',
        encoding="utf-8",
    )
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(tracker, "_fetch_price", lambda code: 11.2)
    first = tracker.track_daily("2026-07-10")
    assert first["tracks"][0]["status"] == "hit_target"

    monkeypatch.setattr(tracker, "_fetch_price", lambda code: 10.2)
    second = tracker.track_daily("2026-07-11")

    assert second["tracks"][0]["status"] == "hit_target"
    assert second["tracks"][0]["current_price"] == 11.2
