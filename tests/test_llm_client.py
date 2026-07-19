# -*- coding: utf-8 -*-
"""LLM客户端输出长度与截断检测。"""

from types import SimpleNamespace

import pytest

from src.analysis.llm_client import LLMClient


class FakeCompletions:
    def __init__(self, finish_reason="stop"):
        self.finish_reason = finish_reason
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        choice = SimpleNamespace(
            finish_reason=self.finish_reason,
            message=SimpleNamespace(content='{"ok": true}'),
        )
        return SimpleNamespace(choices=[choice])


def _client(completions):
    client = LLMClient.__new__(LLMClient)
    client.api_key = "test"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client


def test_chat_sets_explicit_output_token_limit(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    completions = FakeCompletions()

    output = _client(completions).chat([{"role": "user", "content": "x"}])

    assert output == '{"ok": true}'
    assert completions.request["max_tokens"] == 8192


def test_chat_rejects_truncated_completion(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    completions = FakeCompletions(finish_reason="length")

    with pytest.raises(RuntimeError, match="输出被截断"):
        _client(completions).chat([{"role": "user", "content": "x"}])
