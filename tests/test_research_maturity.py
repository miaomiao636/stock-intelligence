"""Synthetic, isolated tests for the forward-only direction review contract."""

import sqlite3

import pytest

from src.research.store import ResearchStore


def synthetic_report(*, day="2026-09-21", time="10:00:00", code="600000", direction="up", horizon=2):
    quote_at = f"{day}T{time}+08:00"
    return {
        "date": day, "type": "morning", "data_as_of": quote_at,
        "model_version": "synthetic-model", "strategy_version": "s1",
        "prompt_version": "p1", "data_version": "d1",
        "stock_recommendations": [{"code": code, "reason": "Explicit synthetic forecast",
            "forecast_direction": direction, "forecast_horizon_sessions": horizon,
            "confidence": 5, "target_price": 100}],
        "evidence_snapshots": [{"source": "fixture", "kind": "market_snapshot", "purpose": "forecast_input",
            "as_of": quote_at, "fetched_at": quote_at, "available_at": quote_at,
            "snapshot": {"realtime_stock_prices": {code: {"price": 10,
                "quote_time": quote_at, "source_time_reliable": True}}}}],
    }


def imported_store(tmp_path, **report_changes):
    now = ["2026-09-21T10:01:00+08:00"]
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: now[0])
    imported = store.import_report(synthetic_report(**report_changes), "fixture:morning")
    return store, now, imported["judgment_ids"][0]


def calendar(start, end):
    return [d for d in ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
            if start <= d <= end]


def observation(code, target, reference):
    return {"trade_date": target, "close": 11, "as_of": target + "T15:00:00+08:00",
            "source": "synthetic-daily", "price_basis": "raw",
            "corporate_action_checked": True, "corporate_action_detected": False}


def test_import_freezes_explicit_spec_from_observable_quote(tmp_path):
    store, _, item_id = imported_store(tmp_path)
    spec = store.get_judgment(item_id)["forecast_spec"]
    assert spec["direction"] == "up"
    assert spec["horizon_sessions"] == 2
    assert spec["reference_price"] == 10
    assert spec["reference_evidence_id"]
    assert spec["status"] == "registered"
    assert spec["probability"] is None  # LLM confidence 5 is not P(up)=100%.


def test_missing_explicit_direction_is_not_inferred_from_target_or_confidence(tmp_path):
    store, _, item_id = imported_store(tmp_path, direction=None)
    spec = store.get_judgment(item_id)["forecast_spec"]
    assert spec["status"] == "excluded"
    assert spec["reason"] == "explicit_forecast_missing"


def test_historical_import_cannot_become_forward_sample(tmp_path):
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: "2026-09-24T10:00:00+08:00")
    result = store.import_report(synthetic_report(), "late-import")
    spec = store.get_judgment(result["judgment_ids"][0])["forecast_spec"]
    assert spec["status"] == "excluded"
    assert spec["reason"] == "not_recorded_on_forecast_day"


def test_same_day_late_import_cannot_masquerade_as_forward_forecast(tmp_path):
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: "2026-09-21T16:00:00+08:00")
    result = store.import_report(synthetic_report(), "same-day-late-import")
    spec = store.get_judgment(result["judgment_ids"][0])["forecast_spec"]
    assert spec["status"] == "excluded"
    assert spec["reason"] == "not_recorded_at_forecast_time"


def test_registered_reference_cannot_be_forged_by_linking_different_quote(tmp_path):
    store, _, item_id = imported_store(tmp_path)
    original = store.get_judgment(item_id)
    item = store.record_judgment({**original,
        "forecast_spec": {**original["forecast_spec"], "reference_price": 1}})
    assert item["forecast_spec"]["status"] == "excluded"
    assert item["forecast_spec"]["reason"] == "reference_evidence_mismatch"


def test_stale_intraday_quote_is_not_a_forward_reference(tmp_path):
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: "2026-09-21T13:16:00+08:00")
    report = synthetic_report(time="13:15:00")
    report["evidence_snapshots"][0]["snapshot"]["realtime_stock_prices"]["600000"]["quote_time"] = "2026-09-21T10:00:00+08:00"
    result = store.import_report(report, "stale-quote")
    spec = store.get_judgment(result["judgment_ids"][0])["forecast_spec"]
    assert spec["status"] == "excluded"
    assert spec["reason"] == "reference_quote_stale"


def test_preopen_last_close_quote_is_allowed_without_inventing_current_price(tmp_path):
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: "2026-09-21T08:46:00+08:00")
    report = synthetic_report(time="08:45:00")
    report["evidence_snapshots"][0]["snapshot"]["realtime_stock_prices"]["600000"]["quote_time"] = "2026-09-18T15:00:00+08:00"
    result = store.import_report(report, "last-close")
    spec = store.get_judgment(result["judgment_ids"][0])["forecast_spec"]
    assert spec["status"] == "registered"
    assert spec["reference_as_of"] == "2026-09-18T07:00:00.000000Z"


@pytest.mark.parametrize("direction,horizon", [([], 2), ("up", True), ("up", "2"), ("UP", 2)])
def test_malformed_explicit_spec_is_excluded_without_breaking_archive(tmp_path, direction, horizon):
    store, _, item_id = imported_store(tmp_path, direction=direction, horizon=horizon)
    assert store.get_judgment(item_id)["forecast_spec"]["status"] == "excluded"


def test_review_waits_for_nth_exchange_session_close_and_not_same_day(tmp_path):
    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T14:59:00+08:00"
    output = store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
                                             observation_provider=observation)
    assert output["created_count"] == 0
    assert output["pending_count"] == 1
    assert store.list_reviews(item_id) == []
    now[0] = "2026-09-23T16:00:00+08:00"
    output = store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
                                             observation_provider=observation)
    review = store.list_reviews(item_id)[0]
    assert output["created_count"] == 1
    assert review["target_trade_date"] == "2026-09-23"
    assert review["prediction_status"] == "correct"
    assert review["observed_return_pct"] == pytest.approx(10)
    assert review["execution_status"] == "unknown"
    assert review["net_pnl_after_costs"] is None


def test_repeat_maturity_job_is_append_only_and_idempotent(tmp_path):
    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    args = dict(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    first = store.review_matured_predictions(**args)
    now[0] = "2026-09-24T16:00:00+08:00"
    second = store.review_matured_predictions(**{**args, "as_of": now[0]})
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert len(store.list_reviews(item_id)) == 1
    with sqlite3.connect(store.db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM research_reviews")


@pytest.mark.parametrize("change,reason", [
    ({"trade_date": "2026-09-22"}, "target_date_mismatch"),
    ({"as_of": "2026-09-24T15:00:00+08:00"}, "observation_time_invalid"),
    ({"close": float("nan")}, "invalid_close"),
    ({"close": 0}, "invalid_close"),
    ({"corporate_action_checked": False}, "price_adjustment_unverified"),
    ({"corporate_action_detected": True}, "corporate_action_requires_adjusted_reference"),
])
def test_unusable_observation_is_not_a_loss_or_zero_return(tmp_path, change, reason):
    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
        observation_provider=lambda *args: {**observation(*args), **change})
    review = store.list_reviews(item_id)[0]
    assert review["prediction_status"] == "inconclusive"
    assert review["exclusion_reason"] == reason
    assert review["observed_return_pct"] is None


def test_calendar_unavailable_never_falls_back_to_weekdays(tmp_path):
    store, now, _ = imported_store(tmp_path)
    now[0] = "2026-09-30T16:00:00+08:00"
    result = store.review_matured_predictions(as_of=now[0], calendar_provider=lambda *_: None,
        observation_provider=lambda *_: pytest.fail("Cannot label without a calendar"))
    assert result["calendar_unavailable_count"] == 1
    assert result["created_count"] == 0


def test_missing_close_can_later_be_reviewed_without_overwriting(tmp_path):
    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=lambda *_: None)
    now[0] = "2026-09-24T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    reviews = store.list_reviews(item_id)
    assert len(reviews) == 2
    assert reviews[0]["prediction_status"] == "correct"
    assert reviews[1]["prediction_status"] == "inconclusive"


def test_future_asof_does_not_fetch_future_outcome(tmp_path):
    store, _, _ = imported_store(tmp_path)
    result = store.review_matured_predictions(as_of="2030-01-01", calendar_provider=calendar,
        observation_provider=lambda *_: pytest.fail("Not due in the real clock"))
    assert result["created_count"] == 0


def test_no_calendar_horizon_is_guessed_from_legacy_text(tmp_path):
    store, _, item_id = imported_store(tmp_path, horizon=None)
    assert store.get_judgment(item_id)["forecast_spec"]["status"] == "excluded"


def test_holiday_gap_counts_only_supplied_exchange_sessions(tmp_path):
    now = ["2026-09-30T10:01:00+08:00"]
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: now[0])
    store.import_report(synthetic_report(day="2026-09-30", horizon=2), "before-holiday")
    now[0] = "2026-10-08T16:00:00+08:00"
    result = store.review_matured_predictions(as_of=now[0],
        calendar_provider=lambda *_: ["2026-09-30", "2026-10-08"],
        observation_provider=lambda *_: pytest.fail("Only one session elapsed"))
    assert result["pending_count"] == 1
    now[0] = "2026-10-09T16:00:00+08:00"
    result = store.review_matured_predictions(as_of=now[0],
        calendar_provider=lambda *_: ["2026-09-30", "2026-10-08", "2026-10-09"],
        observation_provider=observation)
    assert result["created_count"] == 1
    assert store.list_reviews()[0]["target_trade_date"] == "2026-10-09"


def test_concurrent_maturity_retries_create_only_one_review(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, now, _ = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    barrier = Barrier(2)

    def waiting_observation(*args):
        barrier.wait(timeout=5)
        return observation(*args)

    def run():
        return store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
                                                 observation_provider=waiting_observation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result["created_count"] for result in results) == 1
    assert len(store.list_reviews()) == 1


def test_source_snapshot_is_rechecked_not_only_evidence_id_link(tmp_path):
    store, _, item_id = imported_store(tmp_path)
    item = store.get_judgment(item_id)
    spoofed = store.record_judgment({**item, "stock_code": "600001"})
    assert spoofed["forecast_spec"]["status"] == "excluded"
    assert spoofed["forecast_spec"]["reason"] == "reference_evidence_mismatch"
