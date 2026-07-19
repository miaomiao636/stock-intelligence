# -*- coding: utf-8 -*-
"""09:35模拟交易阶段的状态推送与补偿回归测试。"""

import json

import pytest
from click.testing import CliRunner

from cli import cli
from src.notifier import paper_delivery


@pytest.fixture(autouse=True)
def isolated_delivery_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_delivery, "DATA_DIR", tmp_path)


def _healthy_report():
    return {
        "date": "2026-07-15",
        "market_regime": "high_volatility",
        "source_status": {"llm": "success", "candidate_universe": "ok_50"},
        "stock_recommendations": [],
    }


def test_safe_mode_is_notified_and_watchdog_does_not_duplicate(monkeypatch):
    sent = []

    class FakeNotifier:
        def is_available(self):
            return True

        def send_message(self, title, content):
            sent.append((title, content))
            return {"status": "success", "data": {"message_id": "paper-status-1"}}

    monkeypatch.setattr("src.reporting.report_store.load_report", lambda *_: _healthy_report())
    monkeypatch.setattr(
        "src.analysis.recovery.recover_degraded_morning_report",
        lambda *_args, **_kwargs: {"status": "not_needed", "report": _healthy_report()},
    )
    monkeypatch.setattr(
        "src.paper_trading.workflow.PaperTradingWorkflow.prepare_final_orders",
        lambda *_args, **_kwargs: {
            "status": "safe_mode",
            "orders": [],
            "reason": "市场状态high_volatility，¥4,000账户暂停新开仓",
        },
    )
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    runner = CliRunner()
    first = runner.invoke(cli, ["paper", "open", "--date", "2026-07-15"])
    watchdog = runner.invoke(
        cli,
        ["paper", "open", "--date", "2026-07-15", "--if-missing"],
    )

    assert first.exit_code == 0, first.output
    assert watchdog.exit_code == 0, watchdog.output
    assert "已有成功状态回执" in watchdog.output
    assert len(sent) == 1
    assert "暂停新开仓" in sent[0][1]
    receipt = json.loads(paper_delivery.delivery_path("2026-07-15").read_text())
    assert receipt["status"] == "success"
    assert receipt["workflow_status"] == "safe_mode"


def test_status_notification_failure_keeps_stage_retryable(monkeypatch):
    class FakeNotifier:
        def is_available(self):
            return True

        def send_message(self, *_args):
            return {"status": "error", "message": "temporary failure"}

    monkeypatch.setattr("src.reporting.report_store.load_report", lambda *_: _healthy_report())
    monkeypatch.setattr(
        "src.analysis.recovery.recover_degraded_morning_report",
        lambda *_args, **_kwargs: {"status": "not_needed", "report": _healthy_report()},
    )
    monkeypatch.setattr(
        "src.paper_trading.workflow.PaperTradingWorkflow.prepare_final_orders",
        lambda *_args, **_kwargs: {"status": "no_orders", "orders": [], "rejected": []},
    )
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    result = CliRunner().invoke(cli, ["paper", "open", "--date", "2026-07-15"])

    assert result.exit_code != 0
    assert paper_delivery.has_successful_delivery("2026-07-15") is False


def test_cron_contains_second_chance_for_paper_open():
    cron = (paper_delivery.DATA_DIR.parent / "deploy" / "stock-intelligence.cron")
    if not cron.exists():
        cron = paper_delivery.PROJECT_ROOT / "deploy" / "stock-intelligence.cron"
    content = cron.read_text(encoding="utf-8")

    assert "45 9 * * 1-5" in content
    assert "paper open --if-missing" in content
