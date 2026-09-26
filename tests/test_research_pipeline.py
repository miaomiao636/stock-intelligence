from src.reporting import report_store
from src.storage import db


def test_report_write_imports_judgment_once_without_creating_account(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stock_intelligence.db")
    monkeypatch.setattr(report_store, "DATA_DIR", tmp_path)
    db.init_db()
    report = {"date": "2026-09-24", "type": "morning", "created_at": "2026-09-24T08:45:00+08:00",
              "stock_recommendations": [{"code": "600000", "name": "测试", "reason": "原判断", "horizon_days": 3}]}
    report_store.save_report(report)
    report_store.save_report(report)
    from src.research.store import ResearchStore
    rows = ResearchStore(tmp_path / "stock_intelligence.db").list_judgments()
    assert len(rows) == 1
    assert rows[0]["history_incomplete"] is True
    with db.get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='trading_accounts'").fetchone()


def test_morning_final_persistence_exposes_tracking_failure(monkeypatch):
    from src import orchestrator
    reports, runs = [], []
    monkeypatch.setattr(orchestrator, "save_report", lambda report, *_, **kwargs: reports.append(report))
    monkeypatch.setattr(orchestrator, "save_run_artifact", lambda **kwargs: runs.append(kwargs))
    report = {"date": "2026-09-24", "source_status": {"llm": "success"}}
    orchestrator.persist_morning_outcome(report, "test", {"llm": "success", "tracking": "degraded"}, ["跟踪失败"])
    assert reports[0]["source_status"]["tracking"] == "degraded"
    assert runs[0]["report"]["errors"] == ["跟踪失败"]
    reports.clear()
    orchestrator.persist_morning_outcome(report, "test", {"report_preserved": "existing_llm_success"}, ["failure"])
    assert not reports
