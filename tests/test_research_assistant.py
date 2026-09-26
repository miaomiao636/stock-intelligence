import pytest

from src.research.assistant import ResearchAssistant, RequestBudget, BudgetExceeded


def test_facts_never_calls_model_or_exposes_unselected_fields(tmp_path):
    def model(*args, **kwargs):
        raise AssertionError("facts must not call model")
    assistant = ResearchAssistant(RequestBudget(tmp_path / "test.db"), model=model)
    result = assistant.answer("为什么没有交易？", {"as_of": "2026-09-24", "entry_plans": {"waiting": 3},
        "judgments": [], "secret": "should-not-appear"}, request_id="facts-1")
    assert result["usage"]["model_calls"] == 0
    assert "3" in result["answer"]
    assert "should-not-appear" not in str(result)
    assert result["read_only"] is True


def test_experts_bounded_and_retry_idempotent(tmp_path):
    calls = []
    def model(messages, **kwargs):
        calls.append(kwargs)
        return "缺少可验证的财报，不能据此买入。"
    budget = RequestBudget(tmp_path / "test.db")
    assistant = ResearchAssistant(budget, model=model)
    kwargs = dict(question="分析风险", context={"as_of": "2026-09-24", "judgments": []},
                  mode="experts", request_id="research-1")
    result = assistant.answer(**kwargs)
    assert 1 <= len(calls) <= 3
    assert all(c["max_tokens"] <= 1200 and c["timeout"] <= 30 for c in calls)
    assert assistant.answer(**kwargs) == result
    assert len(calls) == result["usage"]["model_calls"]
    with pytest.raises(ValueError):
        assistant.answer(**{**kwargs, "question": "different"})


def test_shared_budget_no_process_reset_and_concurrency(tmp_path):
    path = tmp_path / "test.db"
    a, b = RequestBudget(path, daily_limit=1), RequestBudget(path, daily_limit=1)
    assert a.reserve("a", "hash-a") is None
    with pytest.raises(BudgetExceeded):
        b.reserve("b", "hash-b")
    a.finish("a", {"answer": "done"})
    assert b.reserve("a", "hash-a") == {"answer": "done"}
    with pytest.raises(BudgetExceeded):
        b.reserve("b", "hash-b")


def test_model_failure_is_safe_and_does_not_retry_cost(tmp_path):
    def model(*args, **kwargs):
        raise RuntimeError("secret-api-key")
    assistant = ResearchAssistant(RequestBudget(tmp_path / "test.db"), model=model)
    result = assistant.answer("分析", {}, mode="experts", request_id="failed-1")
    assert "secret-api-key" not in str(result)
    assert result["mode"] == "facts_fallback"
