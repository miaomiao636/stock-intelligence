# -*- coding: utf-8 -*-
"""推荐与评估审计副本必须幂等，盘后报告不能覆盖盘前报告。"""

from src.evaluation.evaluator import Evaluator
from src.reporting import report_store
from src.storage import db


def test_morning_and_closing_reports_coexist_and_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stock_intelligence.db")
    monkeypatch.setattr(report_store, "DATA_DIR", tmp_path)
    db.init_db()
    base = {
        "date": "2026-07-13",
        "run_id": "2026-07-13-morning",
        "created_at": "2026-07-13T08:30:00+08:00",
        "stock_recommendations": [],
    }

    report_store.save_report({**base, "type": "morning"}, "morning")
    report_store.save_report({**base, "type": "closing"}, "closing")
    report_store.save_report({**base, "type": "closing"}, "closing")

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT recommendation_id,type FROM recommendations ORDER BY type"
        ).fetchall()
    assert [(row["recommendation_id"], row["type"]) for row in rows] == [
        ("2026-07-13-morning:closing", "closing"),
        ("2026-07-13-morning:morning", "morning"),
    ]


def test_evaluation_save_replaces_same_business_key(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stock_intelligence.db")
    db.init_db()
    evaluator = Evaluator()
    evaluator.data_dir = tmp_path
    evaluation = {
        "date": "2026-07-13",
        "recommendation_date": "2026-07-13",
        "recommendation_type": "morning",
        "metrics": {"win_rate_pct": 50.0},
    }

    evaluator.save_evaluation(evaluation)
    evaluator.save_evaluation(evaluation)

    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    assert count == 1


def test_repeated_run_archives_previous_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(report_store, "DATA_DIR", tmp_path)
    run_id = "2026-07-14-morning"

    report_store.save_run_artifact(
        run_id,
        {"attempt": 1},
        {"llm": "fallback"},
        {"version": 1},
        ["first failure"],
    )
    report_store.save_run_artifact(
        run_id,
        {"attempt": 2},
        {"llm": "success"},
        {"version": 2},
        [],
    )

    run_dir = tmp_path / "runs" / run_id
    archived = list((run_dir / "attempts").glob("*/report.json"))
    assert len(archived) == 1
    assert '"version": 1' in archived[0].read_text(encoding="utf-8")
    assert '"version": 2' in (run_dir / "report.json").read_text(encoding="utf-8")
