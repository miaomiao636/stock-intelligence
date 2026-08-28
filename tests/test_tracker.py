# -*- coding: utf-8 -*-

from src.tracking.tracker import RecommendationTracker


def _bar(target_date, *, open_price=10.0, close=10.0, high=10.2, low=9.8):
    return {
        "date": target_date,
        "open": open_price,
        "close": close,
        "high": high,
        "low": low,
        "change_pct": 0,
        "_source": "test",
    }


def test_tracker_tolerates_null_stop_loss_pct(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: _bar(target_date),
    )

    result = tracker._track_stock(
        {
            "code": "000001",
            "name": "平安银行",
            "entry_price": 10.0,
            "target_return_pct": None,
            "stop_loss_pct": None,
        },
        "2026-07-13",
        "2026-07-13",
    )

    assert result["stop_loss_price"] == 9.5
    assert result["target_return_pct"] == 5.0
    assert result["status"] == "active"
    assert result["execution_status"] == "theoretical_trigger"


def test_tracker_win_rate_uses_closed_samples_only(tmp_path):
    tracker = RecommendationTracker(tmp_path)
    tracks = [
        {
            "status": "hit_target",
            "execution_status": "filled",
            "actual_return_pct": 6.0,
            "sector": "金融",
            "is_met_expectation": True,
            "is_failed": False,
        },
        {
            "status": "stopped_out",
            "execution_status": "filled",
            "actual_return_pct": -3.0,
            "sector": "金融",
            "is_met_expectation": False,
            "is_failed": True,
        },
        {
            "status": "active",
            "execution_status": "theoretical_trigger",
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
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: _bar(target_date, close=10.5, high=10.6),
    )

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
    quotes = {
        "2026-07-10": _bar("2026-07-10", close=11.2, high=11.3),
        "2026-07-13": _bar("2026-07-13", close=10.2, high=10.3),
    }
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: quotes[target_date],
    )
    first = tracker.track_daily("2026-07-10")
    assert first["tracks"][0]["status"] == "hit_target"

    second = tracker.track_daily("2026-07-13")

    assert second["tracks"][0]["status"] == "hit_target"
    assert second["tracks"][0]["current_price"] == 11.2


def test_tracker_uses_target_date_ohlc_and_trading_days(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    requested = []

    def fetch(code, target_date):
        requested.append((code, target_date))
        return _bar(target_date, close=10.5, high=10.6)

    monkeypatch.setattr(tracker, "_fetch_market_data", fetch)
    result = tracker._track_stock(
        {"code": "000001", "entry_price": 10, "target_price": 12, "stop_loss_price": 9},
        "2026-07-10",
        "2026-07-13",
        previous={
            "execution_status": "theoretical_trigger",
            "entry_price": 10,
            "entry_date": "2026-07-10",
            "target_price": 12,
            "stop_loss_price": 9,
        },
    )

    assert requested == [("000001", "2026-07-13")]
    assert result["data_date"] == "2026-07-13"
    assert result["holding_days"] == 1
    assert result["actual_return_pct"] == 5.0


def test_tracker_does_not_invent_historical_entry_without_prior_state(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: _bar(target_date, close=10.5, low=9.8),
    )

    result = tracker._track_stock(
        {"code": "000001", "entry_price": 10},
        "2026-07-10",
        "2026-07-13",
    )

    assert result["status"] == "history_incomplete"
    assert result["execution_status"] == "unknown"
    assert result["actual_return_pct"] is None


def test_tracker_does_not_turn_incomplete_history_into_late_entry(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: _bar(target_date, close=10.5, low=9.8),
    )

    result = tracker._track_stock(
        {"code": "000001", "entry_price": 10},
        "2026-07-10",
        "2026-07-14",
        previous={"status": "history_incomplete", "execution_status": "unknown"},
    )

    assert result["status"] == "history_incomplete"
    assert result["actual_return_pct"] is None


def test_tracker_keeps_entry_state_when_one_market_day_is_unavailable(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: {"error": "target_date_no_data"},
    )

    result = tracker._track_stock(
        {"code": "000001", "entry_price": 10},
        "2026-07-10",
        "2026-07-13",
        previous={
            "execution_status": "theoretical_trigger",
            "entry_price": 10,
            "entry_date": "2026-07-10",
            "target_price": 11,
            "stop_loss_price": 9,
        },
    )

    assert result["status"] == "data_error"
    assert result["execution_status"] == "theoretical_trigger"
    assert result["entry_price"] == 10
    assert result["entry_date"] == "2026-07-10"


def test_tracker_excludes_untriggered_recommendations_from_performance(tmp_path, monkeypatch):
    tracker = RecommendationTracker(tmp_path)
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: _bar(
            target_date,
            open_price=10.6,
            close=10.7,
            high=10.8,
            low=10.5,
        ),
    )

    track = tracker._track_stock(
        {"code": "000001", "sector": "金融", "entry_price": 10},
        "2026-07-13",
        "2026-07-13",
    )
    summary = tracker._calculate_summary([track])

    assert track["status"] == "not_triggered"
    assert track["actual_return_pct"] is None
    assert summary["valid_samples"] == 0
    assert summary["not_triggered"] == 1
    assert summary["sector_summary"] == {}


def test_tracker_prefers_actual_fill_over_planned_entry(tmp_path, monkeypatch):
    tracker = RecommendationTracker(
        tmp_path,
        execution_lookup=lambda recommendation_id, code: {
            "order_id": "ORDER-1",
            "trade_id": "TRADE-1",
            "trade_date": "2026-07-13",
            "fill_price": 10.2,
            "fill_amount": 1020,
            "entry_fees": 5,
            "entry_slippage": 0,
            "quantity": 100,
            "target_price": 11.2,
            "stop_price": 9.7,
            "instrument_type": "stock",
        },
    )
    monkeypatch.setattr(
        tracker,
        "_fetch_market_data",
        lambda code, target_date: _bar(target_date, close=10.5, high=10.6),
    )

    result = tracker._track_stock(
        {"code": "000001", "recommendation_id": "REC-1", "entry_price": 10},
        "2026-07-13",
        "2026-07-13",
    )

    assert result["execution_status"] == "filled"
    assert result["entry_price"] == 10.2
    assert result["planned_entry_price"] == 10.0
    assert result["return_basis"] == "actual_fill_to_close_after_estimated_exit_costs"
    assert result["order_id"] == "ORDER-1"
