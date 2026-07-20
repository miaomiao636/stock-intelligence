# -*- coding: utf-8 -*-
"""macOS唤醒后的流程补偿测试。"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.automation import reconcile


TZ = ZoneInfo("Asia/Shanghai")


def test_due_stage_windows_are_late_and_safe():
    assert reconcile.due_stage(datetime(2026, 7, 15, 9, 15, tzinfo=TZ)) == "morning"
    assert reconcile.due_stage(datetime(2026, 7, 15, 9, 55, tzinfo=TZ)) == "paper_open"
    assert reconcile.due_stage(datetime(2026, 7, 15, 13, 30, tzinfo=TZ)) == "afternoon"
    assert reconcile.due_stage(datetime(2026, 7, 15, 17, 15, tzinfo=TZ)) == "closing"
    assert reconcile.due_stage(datetime(2026, 7, 15, 10, 30, tzinfo=TZ)) is None
    assert reconcile.due_stage(datetime(2026, 7, 18, 17, 15, tzinfo=TZ)) is None


def test_reconcile_uses_existing_closing_report_for_missing_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "STATE_DIR", tmp_path)
    monkeypatch.setattr(reconcile, "_stage_delivered", lambda *_: False)
    monkeypatch.setattr(reconcile, "load_report", lambda *_: {"type": "closing"})
    monkeypatch.setattr(reconcile, "is_trading_day", lambda *_: True)
    commands = []

    result = reconcile.run_reconcile(
        now=datetime(2026, 7, 15, 17, 15, tzinfo=TZ),
        runner=lambda command: commands.append(command) or {"returncode": 0, "stdout": "ok", "stderr": ""},
    )

    assert result["status"] == "success"
    assert commands == [[
        str(reconcile.PYTHON),
        str(reconcile.PROJECT_ROOT / "cli.py"),
        "notify",
        "--mode",
        "closing",
        "--date",
        "2026-07-15",
        "--if-missing",
    ]]


def test_failed_reconcile_is_throttled(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "STATE_DIR", tmp_path)
    monkeypatch.setattr(reconcile, "_stage_delivered", lambda *_: False)
    monkeypatch.setattr(reconcile, "load_report", lambda *_: {"type": "closing"})
    monkeypatch.setattr(reconcile, "is_trading_day", lambda *_: True)
    calls = []
    now = datetime(2026, 7, 15, 17, 15, tzinfo=TZ)

    first = reconcile.run_reconcile(
        now=now,
        runner=lambda command: calls.append(command) or {"returncode": 1, "stdout": "", "stderr": "fail"},
    )
    second = reconcile.run_reconcile(
        now=now,
        runner=lambda command: calls.append(command) or {"returncode": 1, "stdout": "", "stderr": "fail"},
    )

    assert first["status"] == "error"
    assert second["status"] == "throttled"
    assert len(calls) == 1


def test_launch_agent_runs_reconcile_periodically():
    plist = Path(reconcile.PROJECT_ROOT / "deploy" / "com.stockintelligence.reconcile.plist")
    content = plist.read_text(encoding="utf-8")

    assert "<key>StartInterval</key>" in content
    assert "<integer>300</integer>" in content
    assert "<string>reconcile</string>" in content
