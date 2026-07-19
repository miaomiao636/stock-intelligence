# -*- coding: utf-8 -*-
"""每日报告推送的回归测试。"""

import json

import pytest
from click.testing import CliRunner

from cli import cli
from src.notifier import report_delivery
from src.reporting.formatter import format_closing_report


@pytest.fixture(autouse=True)
def isolated_delivery_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(report_delivery, "DATA_DIR", tmp_path)


def _closing_result(status="degraded"):
    return {
        "status": status,
        "run_status": "completed",
        "data_quality_status": "degraded" if status == "degraded" else "ok",
        "source_status": {"evaluation": "degraded" if status == "degraded" else "ok"},
        "errors": [],
        "warnings": ["沪深300基准数据暂不可用"] if status == "degraded" else [],
        "market_data": {"indices": {}},
        "evaluation": {
            "status": "degraded" if status == "degraded" else "success",
            "quality": {"valid": 1, "total": 1},
            "metrics": {"win_rate_pct": 100.0, "avg_return_pct": 1.2},
            "stock_results": [{"code": "600000", "name": "测试股票", "status": "hit", "return_pct": 1.2}],
        },
    }


def test_daily_pushes_degraded_closing_report(monkeypatch):
    sent = []

    class FakeNotifier:
        def is_available(self):
            return True

        def send_report(self, content, mode):
            sent.append((content, mode))
            return {"status": "success", "data": {"message_id": "test-message"}}

    monkeypatch.setattr(
        "src.evaluation.closing_pipeline.run_closing_pipeline",
        lambda **_: _closing_result("degraded"),
    )
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    result = CliRunner().invoke(cli, ["daily", "--mode", "closing"])

    assert result.exit_code == 0, result.output
    assert len(sent) == 1
    assert sent[0][1] == "closing"
    assert "数据降级" in sent[0][0]


def test_daily_notification_failure_returns_nonzero(monkeypatch):
    class FakeNotifier:
        def is_available(self):
            return True

        def send_report(self, content, mode):
            return {"status": "error", "message": "temporary failure"}

    monkeypatch.setattr(
        "src.evaluation.closing_pipeline.run_closing_pipeline",
        lambda **_: _closing_result("success"),
    )
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    result = CliRunner().invoke(cli, ["daily", "--mode", "closing"])

    assert result.exit_code != 0
    assert "飞书推送失败" in result.output


def test_notify_closing_uses_closing_report_format(monkeypatch):
    sent = []
    report = {
        "type": "closing",
        "market_data": {"indices": {}},
        "evaluation": _closing_result()["evaluation"],
    }

    class FakeNotifier:
        def is_available(self):
            return True

        def send_report(self, content, mode):
            sent.append((content, mode))
            return {"status": "success", "data": {"message_id": "test-message"}}

    monkeypatch.setattr("src.reporting.report_store.load_report", lambda *_: report)
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    result = CliRunner().invoke(
        cli,
        ["notify", "--date", "2026-07-14", "--mode", "closing"],
    )

    assert result.exit_code == 0, result.output
    assert len(sent) == 1
    assert "盘后复盘" in sent[0][0]
    assert "评估结果" in sent[0][0]


def test_successful_daily_push_records_receipt_and_watchdog_does_not_duplicate(monkeypatch):
    sent = []
    closing_report = {
        "date": "2026-07-14",
        "type": "closing",
        "market_data": {"indices": {}},
        "evaluation": _closing_result("degraded")["evaluation"],
    }

    class FakeNotifier:
        def is_available(self):
            return True

        def send_report(self, content, mode):
            sent.append((content, mode))
            return {"status": "success", "data": {"message_id": "receipt-message"}}

    pipeline_result = _closing_result("degraded")
    pipeline_result["report"] = closing_report
    monkeypatch.setattr(
        "src.evaluation.closing_pipeline.run_closing_pipeline",
        lambda **_: pipeline_result,
    )
    monkeypatch.setattr("src.reporting.report_store.load_report", lambda *_: closing_report)
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    runner = CliRunner()
    daily_result = runner.invoke(
        cli,
        ["daily", "--mode", "closing", "--date", "2026-07-14"],
    )
    watchdog_result = runner.invoke(
        cli,
        ["notify", "--mode", "closing", "--date", "2026-07-14", "--if-missing"],
    )

    assert daily_result.exit_code == 0, daily_result.output
    assert watchdog_result.exit_code == 0, watchdog_result.output
    assert "已有成功推送回执" in watchdog_result.output
    assert len(sent) == 1
    receipt = json.loads(
        (report_delivery.DATA_DIR / "notifications" / "2026-07-14" / "closing.json").read_text()
    )
    assert receipt["status"] == "success"
    assert receipt["message_id"] == "receipt-message"


def test_watchdog_sends_when_receipt_is_missing(monkeypatch):
    sent = []
    report = {
        "type": "closing",
        "market_data": {"indices": {}},
        "evaluation": _closing_result()["evaluation"],
    }

    class FakeNotifier:
        def is_available(self):
            return True

        def send_report(self, content, mode):
            sent.append((content, mode))
            return {"status": "success", "data": {"message_id": "watchdog-message"}}

    monkeypatch.setattr("src.reporting.report_store.load_report", lambda *_: report)
    monkeypatch.setattr("src.notifier.feishu.FeishuNotifier", FakeNotifier)

    result = CliRunner().invoke(
        cli,
        ["notify", "--mode", "closing", "--date", "2026-07-14", "--if-missing"],
    )

    assert result.exit_code == 0, result.output
    assert len(sent) == 1


def test_closing_formatter_survives_malformed_optional_sections():
    content = format_closing_report(
        "2026-07-14",
        market_data={"indices": {"000300": None}},
        evaluation={"status": "degraded", "quality": None, "metrics": None, "stock_results": [None]},
        warnings=None,
    )

    assert "盘后复盘" in content
    assert "数据降级" in content


def test_earlier_success_receipt_prevents_later_watchdog_duplicate():
    report_delivery.record_delivery(
        "2026-07-14",
        "closing",
        {"status": "success", "data": {"message_id": "delivered"}},
        "daily",
    )
    report_delivery.record_delivery(
        "2026-07-14",
        "closing",
        {"status": "error", "message": "manual retry failed"},
        "manual_retry",
    )

    assert report_delivery.has_successful_delivery("2026-07-14", "closing") is True
