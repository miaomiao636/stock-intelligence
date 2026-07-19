# -*- coding: utf-8 -*-
"""盘前 LLM 降级补偿测试。"""

from src.analysis.recovery import recover_degraded_morning_report


def test_healthy_report_skips_reanalysis():
    called = []
    report = {"source_status": {"llm": "success"}}

    result = recover_degraded_morning_report(
        "2026-07-14",
        report=report,
        runner=lambda **kwargs: called.append(kwargs),
    )

    assert result["status"] == "not_needed"
    assert result["report"] is report
    assert called == []


def test_fallback_report_is_reanalyzed_with_force():
    called = []
    old_report = {"source_status": {"llm": "fallback"}}
    new_report = {"source_status": {"llm": "success"}}

    def runner(**kwargs):
        called.append(kwargs)
        return {
            "status": "success",
            "report": new_report,
            "source_status": {"llm": "success"},
            "errors": [],
        }

    result = recover_degraded_morning_report(
        "2026-07-14",
        report=old_report,
        runner=runner,
    )

    assert called == [{"dry_run": False, "force": True, "date_str": "2026-07-14"}]
    assert result["status"] == "recovered"
    assert result["report"] is new_report
