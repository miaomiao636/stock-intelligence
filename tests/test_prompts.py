from src.analysis.prompts import MORNING_ANALYSIS_PROMPT


def test_morning_prompt_allows_abstention_and_requires_triggered_entry():
    assert "推荐0-8只个股" in MORNING_ANALYSIS_PROMPT
    assert "setup_ready可以为0" in MORNING_ANALYSIS_PROMPT
    assert "禁止为了凑数而推荐" in MORNING_ANALYSIS_PROMPT
    assert "仍须等计划进场价真实触发" in MORNING_ANALYSIS_PROMPT
    assert "推荐5-8只个股" not in MORNING_ANALYSIS_PROMPT
