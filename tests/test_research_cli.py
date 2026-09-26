import json
from click.testing import CliRunner
import cli


def test_research_import_and_review_do_not_run_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    folder = tmp_path / "data" / "recommendations" / "2026-09-24"
    folder.mkdir(parents=True)
    (folder / "morning.json").write_text(json.dumps({"date": "2026-09-24", "type": "morning",
        "stock_recommendations": [{"code": "600000", "reason": "测试判断"}]}))
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["research", "import-history"])
    assert result.exit_code == 0, result.output
    assert '"imported_reports": 1' in result.output
    result = runner.invoke(cli.cli, ["research", "status"])
    assert result.exit_code == 0
    assert '"judgments_count": 1' in result.output
    assert runner.invoke(cli.cli, ["research", "import-history"]).exit_code == 0
    assert runner.invoke(cli.cli, ["research", "review-history"]).exit_code == 0
