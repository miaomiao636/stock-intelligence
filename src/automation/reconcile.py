# -*- coding: utf-8 -*-
"""macOS开机/唤醒后的关键阶段回执补偿。"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from datetime import datetime, time
from pathlib import Path
from typing import Callable, Dict, Optional

from src.data_collectors.trading_calendar import is_trading_day
from src.notifier.paper_delivery import has_successful_delivery as has_paper_delivery
from src.notifier.report_delivery import has_successful_delivery as has_report_delivery
from src.reporting.report_store import load_report


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
STATE_DIR = PROJECT_ROOT / "data" / "runtime" / "reconcile"
RETRY_SECONDS = 10 * 60
MAX_ATTEMPTS = 3


def due_stage(now: datetime) -> Optional[str]:
    """只在主任务和首层watchdog之后补偿，避免与正常任务竞争。"""
    if now.weekday() >= 5:
        return None
    current = now.timetz().replace(tzinfo=None)
    if time(9, 10) <= current < time(9, 30):
        return "morning"
    if time(9, 50) <= current < time(10, 5):
        return "paper_open"
    if time(17, 10) <= current <= time(23, 59, 59):
        return "closing"
    return None


def _state_path(date_str: str) -> Path:
    return STATE_DIR / f"{date_str}.json"


def _load_state(date_str: str) -> Dict:
    path = _state_path(date_str)
    if not path.exists():
        return {"date": date_str, "stages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"date": date_str, "stages": {}}
    except (OSError, json.JSONDecodeError):
        return {"date": date_str, "stages": {}}


def _save_state(date_str: str, state: Dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(date_str)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def _stage_delivered(date_str: str, stage: str) -> bool:
    if stage == "paper_open":
        return has_paper_delivery(date_str)
    return has_report_delivery(date_str, stage)


def _command_for(date_str: str, stage: str) -> list[str]:
    cli = str(PROJECT_ROOT / "cli.py")
    python = str(PYTHON)
    if stage == "paper_open":
        return [python, cli, "paper", "open", "--date", date_str, "--if-missing"]
    if load_report(date_str, stage):
        return [python, cli, "notify", "--mode", stage, "--date", date_str, "--if-missing"]
    return [python, cli, "daily", "--mode", stage, "--date", date_str]


def _default_runner(command: list[str]) -> Dict:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=15 * 60,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def run_reconcile(
    *,
    now: Optional[datetime] = None,
    runner: Optional[Callable[[list[str]], Dict]] = None,
) -> Dict:
    """补跑当前时窗缺失的单个阶段；成功回执存在时绝不重复执行。"""
    current = now or datetime.now().astimezone()
    stage = due_stage(current)
    if not stage:
        return {"status": "idle", "reason": "当前没有补偿时窗"}
    date_str = current.date().isoformat()
    if _stage_delivered(date_str, stage):
        return {"status": "complete", "stage": stage, "reason": "已有成功回执"}
    if not is_trading_day(current.date()):
        return {"status": "skip", "stage": stage, "reason": "非交易日"}

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / "reconcile.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "locked", "stage": stage, "reason": "已有补偿任务运行中"}

        # 加锁后再次检查，防止正常cron刚刚完成时重复补跑。
        if _stage_delivered(date_str, stage):
            return {"status": "complete", "stage": stage, "reason": "已有成功回执"}

        state = _load_state(date_str)
        stages = state.setdefault("stages", {})
        stage_state = stages.setdefault(stage, {"attempts": 0})
        attempts = int(stage_state.get("attempts") or 0)
        last_attempt = stage_state.get("last_attempt_at")
        if attempts >= MAX_ATTEMPTS:
            return {"status": "exhausted", "stage": stage, "attempts": attempts}
        if last_attempt:
            try:
                elapsed = (current - datetime.fromisoformat(last_attempt)).total_seconds()
            except (TypeError, ValueError):
                elapsed = RETRY_SECONDS
            if elapsed < RETRY_SECONDS:
                return {"status": "throttled", "stage": stage, "attempts": attempts}

        command = _command_for(date_str, stage)
        stage_state.update({
            "attempts": attempts + 1,
            "last_attempt_at": current.isoformat(),
            "last_status": "running",
        })
        _save_state(date_str, state)

        try:
            result = (runner or _default_runner)(command)
        except Exception as exc:
            result = {"returncode": 1, "stdout": "", "stderr": type(exc).__name__}

        success = int(result.get("returncode", 1)) == 0
        stage_state.update({
            "last_status": "success" if success else "error",
            "last_exit_code": int(result.get("returncode", 1)),
            "last_stdout": str(result.get("stdout") or "")[-1000:],
            "last_stderr": str(result.get("stderr") or "")[-1000:],
        })
        _save_state(date_str, state)
        return {
            "status": "success" if success else "error",
            "stage": stage,
            "attempts": stage_state["attempts"],
            "exit_code": stage_state["last_exit_code"],
        }
