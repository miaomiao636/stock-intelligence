# -*- coding: utf-8 -*-
"""每日报告推送的回归测试。"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli import cli
from src.notifier import report_delivery
from src.reporting.formatter import format_afternoon_report, format_closing_report


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


def test_closing_formatter_separates_account_loss_from_signal_returns():
    content = format_closing_report(
        "2026-08-10",
        market_data={"indices": {}},
        evaluation={
            "status": "success",
            "quality": {"valid": 5, "total": 5},
            "metrics": {
                "win_rate_pct": 100.0,
                "avg_return_pct": 3.53,
                "closed_recommendations": 1,
                "active_recommendations": 4,
                "winning_recommendations": 1,
                "losing_recommendations": 0,
            },
            "stock_results": [
                {"code": "002407", "name": "多氟多", "status": "hit", "return_pct": 8.66},
                {"code": "600667", "name": "太极实业", "status": "active", "return_pct": 1.74},
                {"code": "000725", "name": "京东方A", "status": "active", "return_pct": 0.0},
                {"code": "002396", "name": "星网锐捷", "status": "active", "return_pct": 4.60},
                {"code": "603993", "name": "洛阳钼业", "status": "active", "return_pct": 2.67},
            ],
        },
        account_summary={
            "status": "ok",
            "cash": 8082.25,
            "market_value": 11890.0,
            "total_equity": 19972.25,
            "total_return": -27.75,
            "total_return_pct": -0.14,
            "realized_pnl": 0.0,
            "unrealized_pnl": -65.0,
            "position_count": 5,
            "max_drawdown_pct": 2.5,
            "total_transaction_costs": 12.5,
        },
    )

    assert "模拟账户盘后结算（账户口径）" in content
    assert "-¥27.75 (-0.14%)" in content
    assert "今日推荐信号评估结果（非账户收益）" in content
    assert "100.0%（1/1）" in content
    assert "进行中样本: 4" in content
    assert "信号平均浮动收益: 3.53%" in content
    assert "不等于模拟账户收益" in content


def test_closing_formatter_does_not_imply_account_profit_without_snapshot():
    content = format_closing_report(
        "2026-08-10",
        market_data={"indices": {}},
        evaluation=_closing_result("success")["evaluation"],
    )

    assert "未保存账户结算快照" in content
    assert "不得据此判断账户盈亏" in content


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


def test_afternoon_formatter_marks_report_as_advisory_only():
    content = format_afternoon_report(
        "2026-07-20",
        market_data={"indices": {}},
        stock_recommendations=[{
            "code": "000001",
            "name": "平安银行",
            "latest_price": 10.2,
            "since_morning_pct": 2.0,
            "afternoon_decision": "upgrade",
            "incremental_basis": "盘中表现改善",
        }],
        summary={"maintain": 0, "upgrade": 1, "downgrade": 0, "cancel": 0},
    )

    assert "下午盘中复核" in content
    assert "升级 1" in content
    assert "不会绕过5分钟否决" in content


def test_deploy_cron_contains_afternoon_pipeline_and_watchdog():
    content = Path(__file__).resolve().parents[1] / "deploy" / "stock-intelligence.cron"
    text = content.read_text(encoding="utf-8")

    assert "15 13 * * 1-5" in text
    assert "daily --mode afternoon" in text
    assert "notify --mode afternoon --if-missing" in text


def test_ubuntu_update_preserves_env_and_installs_persistent_feishu_ws():
    script = (Path(__file__).resolve().parents[1] / "deploy" / "update_ubuntu.sh").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=${PROJECT_DIR}/.env" in script
    assert "ExecStart=${PROJECT_DIR}/.venv/bin/python -m src.notifier.feishu_ws" in script
    assert "systemctl restart \"${SERVICE_NAME}.service\"" in script
    assert "Existing .env and data were preserved" in script
    assert "cp .env.example .env" not in script


def test_ubuntu_cron_serializes_report_and_watchdog_with_shared_locks():
    root = Path(__file__).resolve().parents[1]
    cron_installer = (root / "deploy" / "install_cron_ubuntu.sh").read_text(encoding="utf-8")
    update_script = (root / "deploy" / "update_ubuntu.sh").read_text(encoding="utf-8")

    for mode in ("morning", "afternoon", "closing"):
        lock = f"/var/lock/stock-intelligence-{mode}.lock"
        assert cron_installer.count(lock) == 2
    assert "/usr/bin/flock -n" in cron_installer
    assert "/usr/bin/flock -w 1800" in cron_installer
    assert "deploy/install_cron_ubuntu.sh" in update_script
# Missing accounting evidence must remain unknown, not an invented zero.
def test_closing_report_keeps_missing_realized_pnl_unknown():
    from src.reporting.formatter import format_closing_report
    output = format_closing_report("2026-09-24", {}, {}, account_summary={
        "status": "ok", "cash": 100, "total_equity": 100, "realized_pnl": None,
        "unrealized_pnl": 0, "accounting_status": "incomplete"})
    assert "已实现盈亏: 未能核实" in output
