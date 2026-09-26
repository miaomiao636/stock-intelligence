import sqlite3

import pytest

from src.research.store import ResearchStore


def store_at(tmp_path, now="2026-09-26T10:00:00+08:00"):
    return ResearchStore(tmp_path / "stock_intelligence.db", clock=lambda: now)


def judgment(**changes):
    return {
        "stock_code": "600000", "as_of": "2026-09-24T08:45:00+08:00",
        "horizon": "5_trading_days", "thesis": "测试研究假设，不是投资建议",
        "model_version": "test-model", "strategy_version": "v1",
        "prompt_version": "p1", "data_version": "d1", **changes,
    }


def test_new_research_tables_share_database_without_touching_account(tmp_path):
    path = tmp_path / "stock_intelligence.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trading_accounts(account_id TEXT, cash REAL)")
        conn.execute("INSERT INTO trading_accounts VALUES('paper',19655.74)")
    store = store_at(tmp_path)
    item = store.record_judgment(judgment())
    assert item["history_incomplete"] is True
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT cash FROM trading_accounts").fetchone()[0] == 19655.74


def test_known_at_cannot_be_backdated_by_payload(tmp_path):
    store = store_at(tmp_path)
    item = store.record_judgment(judgment(known_at="2020-01-01"))
    assert item["known_at"] == "2026-09-26T02:00:00.000000Z"
    assert store.list_judgments(as_of="2026-09-25") == []
    assert store.get_judgment(item["judgment_id"], as_of="2026-09-25") is None
    assert len(store.list_judgments(as_of="2026-09-26T10:01:00+08:00")) == 1


def test_records_are_immutable_even_through_sql(tmp_path):
    store = store_at(tmp_path)
    item = store.record_judgment(judgment())
    with sqlite3.connect(store.db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE research_judgments SET payload_json='{}' WHERE judgment_id=?", (item["judgment_id"],))
    assert store.get_judgment(item["judgment_id"])["thesis"] == judgment()["thesis"]


def test_import_is_idempotent_and_keeps_historical_provenance_honest(tmp_path):
    store = store_at(tmp_path)
    report = {"date": "2026-09-24", "type": "morning", "run_id": "run-1", "created_at": "2026-09-24T08:45:00+08:00", "stock_recommendations": [{"code": "600000", "reason": "测试", "target_price": 10}], "news_sources": [{"url": "https://example.org/news", "title": "报道", "published_at": "2026-09-24"}]}
    first = store.import_report(report, source_id="reports/2026-09-24-morning.json")
    second = store.import_report(report, source_id="reports/2026-09-24-morning.json")
    assert first["imported"] is True and second["imported"] is False
    assert first["judgment_ids"] == second["judgment_ids"]
    assert len(store.list_judgments()) == 1
    assert first["history_incomplete"] is True
    imported = store.get_judgment(first["judgment_ids"][0])
    assert imported["provenance"]["source_id"].endswith("morning.json")
    assert imported["model_version"] == "unknown"
    assert store.list_evidence()[0]["available_at"] is None
    assert store.list_reviews() == []


def test_changed_report_appends_revision_instead_of_overwriting(tmp_path):
    store = store_at(tmp_path)
    report = {"date": "2026-09-24", "stock_recommendations": [{"code": "600000", "reason": "初始假设"}]}
    a = store.import_report(report, "same-source")
    report["stock_recommendations"][0]["reason"] = "修订假设"
    b = store.import_report(report, "same-source")
    assert a["judgment_ids"] != b["judgment_ids"]
    assert len(store.list_judgments()) == 2


def test_evidence_requires_real_reference_and_cannot_be_from_future(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        store.record_judgment(judgment(supporting_evidence=["invented"]))
    ev = store.record_evidence({"source": "test-provider", "kind": "quote", "published_at": "2026-09-25T12:00:00+08:00", "fetched_at": "2026-09-25T12:00:01+08:00", "available_at": "2026-09-25T12:00:00+08:00", "snapshot": {"price": 10}})
    with pytest.raises(ValueError, match="future"):
        store.record_judgment(judgment(supporting_evidence=[ev["evidence_id"]]))


def test_review_separates_prediction_execution_and_profit(tmp_path):
    store = store_at(tmp_path)
    item = store.record_judgment(judgment())
    review = store.append_review(item["judgment_id"], {"as_of": "2026-09-26T09:00:00+08:00", "prediction_status": "correct", "execution_status": "not_triggered", "net_pnl_status": "unavailable"})
    assert review["prediction_status"] == "correct"
    assert review["net_pnl_after_costs"] is None
    with pytest.raises(ValueError, match="execution"):
        store.append_review(item["judgment_id"], {"as_of": "2026-09-26", "prediction_status": "correct", "execution_status": "not_triggered", "net_pnl_status": "realized", "net_pnl_after_costs": 100})
    assert store.list_reviews(as_of="2026-09-25") == []


def test_lesson_state_requires_explicit_confirmation_and_replays_asof(tmp_path):
    store = store_at(tmp_path)
    item = store.record_judgment(judgment())
    lesson = store.create_lesson({"title": "高成本交易要审查", "body": "候选假设", "judgment_ids": [item["judgment_id"]], "status": "validated"})
    assert lesson["status"] == "candidate"
    with pytest.raises(ValueError, match="confirmation"):
        store.transition_lesson(lesson["lesson_id"], "validated", actor="operator", reason="reviewed")
    later = store_at(tmp_path, "2026-09-27T10:00:00+08:00")
    updated = later.transition_lesson(lesson["lesson_id"], "validated", actor="operator", reason="人工核验", confirmed=True)
    assert updated["status"] == "validated"
    assert later.list_lessons(as_of="2026-09-26")[0]["status"] == "candidate"
    assert later.list_lessons(status="validated", as_of="2026-09-26") == []
    assert later.list_lessons(as_of="2026-09-25") == []
    assert updated["affects_trading"] is False


def test_import_transaction_rolls_back_invalid_stock(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ValueError):
        store.import_report({"date": "2026-09-24", "stock_recommendations": [{"code": "600000"}, {"code": ""}]}, "bad")
    assert store.list_judgments() == []


def test_non_finite_json_rejected(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ValueError):
        store.record_judgment(judgment(provenance={"price": float("nan")}))


def test_summary_counts_all_rows_not_default_list_limit(tmp_path):
    store = store_at(tmp_path)
    for i in range(105):
        store.record_judgment(judgment(stock_code=f"{i:06d}"))
    assert len(store.list_judgments()) == 100
    assert store.get_summary()["judgments_count"] == 105
    assert store.get_summary(as_of="2026-09-25")["judgments_count"] == 0


def test_automatic_review_keeps_theoretical_return_separate_and_is_idempotent(tmp_path):
    store = store_at(tmp_path)
    imported = store.import_report({"date": "2026-09-24", "type": "morning", "created_at": "2026-09-24T08:45:00+08:00", "stock_recommendations": [{"code": "600000", "reason": "假设"}]}, "morning")
    evaluation = {"date": "2026-09-24", "recommendation_date": "2026-09-24", "recommendation_type": "morning", "evaluated_at": "2026-09-24T16:45:00+08:00", "stock_results": [{"code": "600000", "status": "profitable", "execution_status": "theoretical_trigger", "return_pct": 5.0, "return_basis": "triggered_limit_entry_to_close"}]}
    first = store.review_from_evaluation(evaluation, source_id="eval-1")
    second = store.review_from_evaluation(evaluation, source_id="eval-1")
    assert first["review_count"] == 1 and second["imported"] is False
    assert len(store.list_reviews()) == 1
    review = store.list_reviews()[0]
    assert review["judgment_id"] == imported["judgment_ids"][0]
    assert review["execution_status"] == "unknown"
    assert review["net_pnl_after_costs"] is None
    assert review["prediction_status"] == "history_incomplete"
    assert review["observed_recommendation_return_pct"] == 5.0


def test_automatic_review_does_not_assume_unknown_code_or_fake_trade(tmp_path):
    store = store_at(tmp_path)
    store.import_report({"date": "2026-09-24", "type": "morning", "stock_recommendations": [{"code": "600000"}]}, "morning")
    evaluation = {"date": "2026-09-24", "recommendation_date": "2026-09-24", "recommendation_type": "morning", "stock_results": [{"code": "600000", "execution_status": "filled", "return_pct": 1.0}, {"code": "600999", "execution_status": "filled", "trade_id": "missing"}]}
    result = store.review_from_evaluation(evaluation, source_id="eval-1")
    assert result["review_count"] == 1 and result["unmatched_count"] == 1
    assert store.list_reviews()[0]["execution_status"] == "unknown"


def test_ui_aliases_and_experiment_storage(tmp_path):
    store = store_at(tmp_path)
    item = store.record_judgment(judgment())
    lesson = store.create_lesson({"title": "test", "body": "summary"})
    exp = store.create_experiment({"name": "missing-data", "protocol_version": "v1"})
    assert item["id"] == item["judgment_id"]
    assert lesson["summary"] == "summary"
    assert exp["status"] == "blocked"
    assert store.list_experiments()[0]["id"] == exp["experiment_id"]
    assert store.get_summary()["experiments_count"] == 1


def test_import_summary_does_not_claim_complete_when_versions_are_missing(tmp_path):
    store = store_at(tmp_path)
    report = {"date": "2026-09-24", "created_at": "2026-09-24T08:45:00+08:00", "stock_recommendations": [{"code": "600000"}], "evidence_snapshots": [{"source": "fixture", "fetched_at": "2026-09-24T08:00:00+08:00", "available_at": "2026-09-24T08:00:00+08:00", "snapshot": {"close": 1}}]}
    result = store.import_report(report, "source")
    assert result["history_incomplete"] is True


def test_evidence_cannot_claim_a_future_fetch(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ValueError, match="future"):
        store.record_evidence({"source": "provider", "fetched_at": "2030-01-01", "snapshot": {}})


def test_future_experiment_cutoff_cannot_admit_future_labels(tmp_path):
    from tests.test_research_experiments import rows
    store = store_at(tmp_path, "2026-09-01T10:00:00+08:00")
    result = store.create_experiment({"name": "future", "protocol_version": "v1", "as_of": "2030-01-01", "observations": rows()})
    assert result["status"] == "blocked"


def test_review_alias_uses_review_id(tmp_path):
    store = store_at(tmp_path)
    item = store.record_judgment(judgment())
    review = store.append_review(item["id"], {"as_of": "2026-09-26", "execution_status": "not_triggered"})
    assert review["id"] == review["review_id"]


def test_archive_metadata_does_not_change_import_identity(tmp_path):
    store = store_at(tmp_path)
    report = {"date": "2026-09-24", "stock_recommendations": [{"code": "600000"}]}
    first = store.import_report(report, "2026-09-24:morning")
    second = store.import_report({**report, "research_archive": first}, "2026-09-24:morning")
    assert second["imported"] is False
    assert second["judgment_ids"] == first["judgment_ids"]


def test_missing_snapshot_fetch_is_honest_incomplete_not_import_crash(tmp_path):
    store = store_at(tmp_path)
    result = store.import_report({"date": "2026-09-24", "stock_recommendations": [], "evidence_snapshots": [{"source": "news", "fetched_at": None, "available_at": None, "published_at": None, "snapshot": {"title": "unknown time"}}]}, "source")
    assert result["history_incomplete"] is True
    assert store.list_evidence()[0]["history_incomplete"] is True


def test_auto_candidate_lessons_group_diagnostics_and_never_promote(tmp_path):
    store = store_at(tmp_path)
    store.import_report({"date": "2026-09-24", "type": "morning", "stock_recommendations": [{"code": "600000"}, {"code": "600001"}]}, "report")
    evaluation = {"date": "2026-09-24", "recommendation_date": "2026-09-24", "recommendation_type": "morning", "stock_results": [{"code": code, "execution_status": "not_triggered", "return_pct": None} for code in ["600000", "600001"]]}
    store.review_from_evaluation(evaluation, source_id="2026-09-24:closing")
    first = store.generate_candidate_lessons(source_id="2026-09-24:closing")
    second = store.generate_candidate_lessons(source_id="2026-09-24:closing")
    assert 1 <= first["created_count"] <= 4
    assert second["created_count"] == 0
    assert first["lesson_ids"] == second["lesson_ids"]
    lessons = store.list_lessons()
    assert all(item["status"] == "candidate" and not item["affects_trading"] for item in lessons)
    assert all(item["review_ids"] and item["judgment_ids"] for item in lessons)
    waiting = next(item for item in lessons if item["diagnostic_kind"] == "not_triggered")
    assert waiting["sample_count"] == 2
    assert "不代表" in waiting["body"]


def test_auto_candidate_lessons_respect_known_at(tmp_path):
    store = store_at(tmp_path)
    assert store.generate_candidate_lessons(source_id="missing", as_of="2026-09-25")["created_count"] == 0


def current_report(news=None):
    capture = "2026-09-24T08:45:00+08:00"
    snapshots = [{"source": "market", "kind": "market_snapshot", "fetched_at": capture, "available_at": capture, "snapshot": {"stock_code": "600000", "as_of": "2026-09-24T08:44:00+08:00", "price": 10}}]
    if news is not None:
        snapshots.append({"source": "news", "kind": "news", "fetched_at": capture, "available_at": capture, "published_at": news, "snapshot": {"title": "test"}})
    return {"date": "2026-09-24", "type": "morning", "data_as_of": capture,
            "stock_recommendations": [{"code": "600000", "reason": "假设"}],
            "evidence_snapshots": snapshots, "model_version": "m1", "strategy_version": "s1",
            "prompt_version": "p1", "data_version": "d1", "source_status": {"llm": "success"}}


def test_news_unknown_publication_does_not_hide_behind_complete_market_snapshot(tmp_path):
    store = store_at(tmp_path)
    report = current_report()
    report["evidence_snapshots"].append({"source": "news", "kind": "news", "fetched_at": report["data_as_of"], "available_at": report["data_as_of"], "published_at": None, "snapshot": {"title": "unknown"}})
    result = store.import_report(report, "current")
    assert result["history_incomplete"] is True
    assert store.list_judgments()[0]["history_incomplete"] is True


def test_future_news_quarantined_without_losing_report_judgments(tmp_path):
    store = store_at(tmp_path)
    result = store.import_report(current_report("2030-01-01"), "future-news")
    assert len(result["judgment_ids"]) == 1
    assert result["history_incomplete"] is True
    assert any(item["kind"] == "quarantined_snapshot" for item in store.list_evidence())


def test_date_only_publication_keeps_precision_and_quote_keeps_own_asof(tmp_path):
    store = store_at(tmp_path)
    result = store.import_report(current_report("2026-09-23"), "current")
    assert result["history_incomplete"] is False
    evidence = store.list_evidence()
    assert next(item for item in evidence if item["kind"] == "news")["publication_precision"] == "date"
    assert next(item for item in evidence if item["kind"] == "market_snapshot")["as_of"] == "2026-09-24T00:44:00.000000Z"


def test_operational_health_updates_do_not_duplicate_research_but_thesis_changes_do(tmp_path):
    store = store_at(tmp_path)
    report = current_report()
    first = store.import_report(report, "current")
    report["source_status"]["tracking"] = "degraded"
    report["errors"] = ["tracking job failed"]
    again = store.import_report(report, "current")
    assert again["imported"] is False
    assert again["judgment_ids"] == first["judgment_ids"]
    report["stock_recommendations"][0]["reason"] = "new thesis"
    assert store.import_report(report, "current")["imported"] is True


def test_future_quote_timestamp_not_accepted_as_past_support(tmp_path):
    store = store_at(tmp_path)
    report = current_report()
    report["evidence_snapshots"][0]["snapshot"]["as_of"] = "2026-09-25T08:44:00+08:00"
    result = store.import_report(report, "future-quote")
    assert result["history_incomplete"] is True
    assert store.list_judgments()[0]["supporting_evidence"] == []


def test_current_snapshot_archive_preserves_horizon_and_is_not_legacy_import(tmp_path):
    store = store_at(tmp_path)
    report = current_report()
    report["stock_recommendations"][0].update(horizon="short", horizon_days=3)
    store.import_report(report, "current")
    record = store.list_judgments()[0]
    assert record["horizon_days"] == 3
    assert record["provenance"]["historical_import"] is False


@pytest.mark.parametrize("market", [{"error": "data unavailable"}, {"indices": {"000300": {"close": 3800}}}])
def test_error_or_index_only_snapshot_does_not_support_individual_stock(tmp_path, market):
    store = store_at(tmp_path)
    report = current_report()
    report["evidence_snapshots"][0]["snapshot"] = market
    result = store.import_report(report, "bad-market")
    assert result["history_incomplete"] is True
    assert store.list_judgments()[0]["supporting_evidence"] == []


def test_quote_snapshot_only_supports_covered_stock_without_contaminating_sibling(tmp_path):
    store = store_at(tmp_path)
    report = current_report()
    report["stock_recommendations"] = [{"code": "600999"}, {"code": "600000"}]
    report["evidence_snapshots"][0]["snapshot"] = {"realtime_stock_prices": {"600000": {"code": "600000", "price": 10, "quote_time": "2026-09-24T08:44:00+08:00", "source_time_reliable": True}}}
    store.import_report(report, "coverage")
    records = {item["stock_code"]: item for item in store.list_judgments()}
    assert records["600999"]["history_incomplete"] is True
    assert records["600999"]["supporting_evidence"] == []
    assert records["600000"]["history_incomplete"] is False
    assert records["600000"]["claim_verification_status"] == "not_verified"
