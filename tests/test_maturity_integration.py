from datetime import date
import json
from click.testing import CliRunner

import cli
from src.evaluation import closing_pipeline


def test_closing_reviews_maturity_even_without_todays_report(monkeypatch):
    calls = []
    monkeypatch.setattr(closing_pipeline, "is_trading_day", lambda _: True)
    monkeypatch.setattr(closing_pipeline, "load_report", lambda *args: None)
    monkeypatch.setattr("src.research.outcomes.run_maturity_review", lambda *a, **kw: calls.append(kw) or {"status": "ok", "created_count": 1})
    result = closing_pipeline.run_closing_pipeline(date_str=date.today().isoformat())
    assert len(calls) == 1
    assert result["prediction_review"]["created_count"] == 1
    assert result["source_status"]["prediction_review"] == "ok"


def test_dry_or_historical_closing_cannot_fetch_or_write_maturity(monkeypatch):
    monkeypatch.setattr(closing_pipeline, "is_trading_day", lambda _: True)
    monkeypatch.setattr(closing_pipeline, "load_report", lambda *args: None)
    def forbidden(*a, **kw):
        raise AssertionError("must not fetch/write research")
    monkeypatch.setattr("src.research.outcomes.run_maturity_review", forbidden)
    result = closing_pipeline.run_closing_pipeline(dry_run=True)
    assert result["source_status"]["prediction_review"] == "skipped_dry_run"
    result = closing_pipeline.run_closing_pipeline(date_str="2026-07-17")
    assert result["source_status"]["prediction_review"] == "skipped_historical_run"


def test_maturity_provider_failure_does_not_leak_or_stop_closing(monkeypatch):
    monkeypatch.setattr(closing_pipeline, "is_trading_day", lambda _: True)
    monkeypatch.setattr(closing_pipeline, "load_report", lambda *args: None)
    def fails(*a, **kw):
        raise RuntimeError("synthetic-private-provider-detail")
    monkeypatch.setattr("src.research.outcomes.run_maturity_review", fails)
    result = closing_pipeline.run_closing_pipeline()
    assert result["prediction_review"]["status"] == "error"
    assert "synthetic-private-provider-detail" not in json.dumps(result)


def test_explicit_review_maturity_cli_is_not_trading(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr("src.research.outcomes.run_maturity_review", lambda path, **kw: calls.append(path) or {"status": "ok", "affects_trading": False})
    result = CliRunner().invoke(cli.cli, ["research", "review-maturity"])
    assert result.exit_code == 0, result.output
    assert calls == [tmp_path / "data"]
    assert json.loads(result.output)["affects_trading"] is False
