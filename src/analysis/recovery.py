# -*- coding: utf-8 -*-
"""盘前分析降级后的安全补偿。"""

from typing import Callable, Dict, Optional


DEGRADED_LLM_STATES = {"fallback", "error", "degraded", "unavailable"}


def recover_degraded_morning_report(
    date_str: str,
    report: Optional[Dict] = None,
    runner: Callable = None,
) -> Dict:
    """仅在 LLM 降级时强制重跑盘前流程，供09:35和手动恢复复用。"""
    if report is None:
        from src.reporting.report_store import load_report
        report = load_report(date_str, "morning")
    if not report:
        return {"status": "missing", "report": None, "errors": ["盘前报告不存在"]}

    llm_status = report.get("source_status", {}).get("llm")
    if llm_status not in DEGRADED_LLM_STATES:
        return {"status": "not_needed", "report": report, "errors": []}

    if runner is None:
        from src.orchestrator import run_morning_pipeline
        runner = run_morning_pipeline
    result = runner(dry_run=False, force=True, date_str=date_str)
    refreshed = result.get("report") or report
    refreshed_llm = result.get("source_status", {}).get("llm")
    recovered = refreshed_llm == "success"
    return {
        "status": "recovered" if recovered else "still_degraded",
        "report": refreshed,
        "llm_status": refreshed_llm or "unknown",
        "errors": result.get("errors", []),
    }
