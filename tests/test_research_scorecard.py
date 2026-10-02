from src.research.store import ResearchStore
from tests.test_research_maturity import synthetic_report, imported_store, calendar, observation


def test_empty_scorecard_is_unknown_not_zero_accuracy(tmp_path):
    store = ResearchStore(tmp_path / "synthetic.db")
    card = store.get_prediction_scorecard()
    assert card["summary"]["evaluable"] == 0
    assert card["summary"]["accuracy_pct"] is None
    assert card["summary"]["always_up_accuracy_pct"] is None
    assert card["summary"]["coverage_pct"] is None
    assert card["affects_trading"] is False


def test_scorecard_compares_same_cohort_and_ignores_daily_review(tmp_path):
    store, now, first_id = imported_store(tmp_path, direction="down")
    store.import_report(synthetic_report(code="600001", direction="up"), "second")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    store.append_review(first_id, {"as_of": now[0], "prediction_status": "pending", "execution_status": "not_triggered"})
    card = store.get_prediction_scorecard()
    assert card["summary"]["evaluable"] == 2
    assert card["summary"]["correct"] == 1
    assert card["summary"]["incorrect"] == 1
    assert card["summary"]["accuracy_pct"] == 50
    assert card["summary"]["always_up_accuracy_pct"] == 100
    assert card["summary"]["coverage_pct"] == 100
    assert card["by_horizon"][0]["horizon_sessions"] == 2
    assert card["by_horizon"][0]["summary"]["evaluable"] == 2


def test_same_day_stock_horizon_is_counted_once_even_revised_direction(tmp_path):
    store, now, _ = imported_store(tmp_path, direction="down")
    now[0] = "2026-09-21T13:16:00+08:00"
    store.import_report(synthetic_report(time="13:15:00", direction="up"), "afternoon")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    card = store.get_prediction_scorecard()
    assert card["summary"]["records_total"] == 2
    assert card["summary"]["duplicate"] == 1
    assert card["summary"]["evaluable"] == 1
    assert card["summary"]["accuracy_pct"] == 0
    assert "not_independent" in card["sample_dependence"]


def test_unavailable_data_reduces_coverage_and_does_not_enter_baseline(tmp_path):
    store, now, _ = imported_store(tmp_path)
    store.import_report(synthetic_report(code="600001"), "missing-close")
    store.import_report(synthetic_report(code="600002", direction=None), "legacy")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
        observation_provider=lambda code, *args: observation(code, *args) if code == "600000" else None)
    summary = store.get_prediction_scorecard()["summary"]
    assert summary["evaluable"] == 1
    assert summary["unavailable"] == 1
    assert summary["excluded"] == 1
    assert summary["coverage_pct"] == 50
    assert summary["accuracy_pct"] == 100
    assert summary["always_up_accuracy_pct"] == 100


def test_asof_hides_later_labels_and_late_imports(tmp_path):
    store, now, _ = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    earlier = store.get_prediction_scorecard(as_of="2026-09-22T16:00:00+08:00")
    assert earlier["summary"]["evaluable"] == 0
    assert earlier["summary"]["pending"] == 1
    assert earlier["summary"]["accuracy_pct"] is None


def test_scorecard_reads_all_rows_not_list_default_limit(tmp_path):
    store = ResearchStore(tmp_path / "synthetic.db", clock=lambda: "2026-09-21T10:01:00+08:00")
    for i in range(105):
        store.import_report(synthetic_report(code=f"{i:06d}"), str(i))
    card = store.get_prediction_scorecard()
    assert card["summary"]["records_total"] == 105
    assert card["summary"]["pending"] == 105


def test_manual_review_cannot_claim_maturity_without_frozen_outcome_evidence(tmp_path):
    store, now, item_id = imported_store(tmp_path)
    now[0] = "2026-09-23T16:00:00+08:00"
    store.append_review(item_id, {"as_of": now[0], "review_kind": "direction_maturity_v1",
        "prediction_status": "correct", "actual_direction": "up"})
    assert store.get_prediction_scorecard()["summary"]["evaluable"] == 0


def test_exact_timestamp_duplicate_uses_insert_order_not_random_id(tmp_path):
    store, now, _ = imported_store(tmp_path, direction="down")
    for i in range(10):
        store.import_report(synthetic_report(direction="up"), f"repeat-{i}")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    summary = store.get_prediction_scorecard()["summary"]
    assert summary["evaluable"] == 1
    assert summary["incorrect"] == 1
    assert summary["duplicate"] == 10


def test_flat_price_is_direction_observation_not_missing_zero(tmp_path):
    store, now, _ = imported_store(tmp_path, direction="flat")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar,
        observation_provider=lambda *args: {**observation(*args), "close": 10})
    summary = store.get_prediction_scorecard()["summary"]
    assert summary["correct"] == 1
    assert summary["accuracy_pct"] == 100
    assert summary["always_up_accuracy_pct"] == 0
    assert store.list_reviews()[0]["observed_return_pct"] == 0


def test_maturity_never_mutates_trading_account_or_produces_net_profit(tmp_path):
    import sqlite3

    store, now, _ = imported_store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TABLE trading_accounts(account_id TEXT PRIMARY KEY,cash REAL)")
        conn.execute("INSERT INTO trading_accounts VALUES('paper',19655.74)")
    now[0] = "2026-09-23T16:00:00+08:00"
    store.review_matured_predictions(as_of=now[0], calendar_provider=calendar, observation_provider=observation)
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT * FROM trading_accounts").fetchall() == [("paper", 19655.74)]
    assert store.list_reviews()[0]["net_pnl_after_costs"] is None
    assert store.get_prediction_scorecard()["affects_trading"] is False
