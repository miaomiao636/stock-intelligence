# -*- coding: utf-8 -*-
"""LLM 输出容错和安全降级回归测试。"""

import json

from src.analysis.synthesizer import Synthesizer
from src.analysis.validators import validate_llm_output


def _valid_report() -> str:
    return json.dumps({
        "market_regime": "neutral",
        "sector_recommendations": [{
            "sector_name": "银行",
            "rating": 3,
            "reason": "测试板块",
            "target_return_pct": 2,
            "risk_level": "medium",
        }],
        "stock_recommendations": [{
            "code": "000001",
            "name": "平安银行",
            "sector": "银行",
            "action": "watch",
            "confidence": 2,
            "horizon": "short",
            "reason": "测试观察",
        }],
    }, ensure_ascii=False)


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _synthesizer(responses):
    synthesizer = Synthesizer()
    synthesizer.llm_client = FakeLLMClient(responses)
    synthesizer.max_retries = 2
    synthesizer.format_retries = 1
    return synthesizer


def test_trailing_comma_json_is_repaired_locally():
    raw = _valid_report().replace(
        '"reason": "测试观察"}',
        '"reason": "测试观察",}',
    )
    valid, data, error = validate_llm_output(raw)

    assert valid, error
    assert data["stock_recommendations"][0]["code"] == "000001"


def test_invalid_json_uses_format_repair_before_full_analysis_retry(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    synthesizer = _synthesizer(['{"sector_recommendations": [', _valid_report()])

    result = synthesizer._try_llm_analysis("full prompt", {"date": "2026-07-14"})

    assert result["status"] == "success"
    assert result["source"] == "llm_repaired"
    assert len(synthesizer.llm_client.calls) == 2
    assert "修复" in synthesizer.llm_client.calls[1]["messages"][0]["content"]
    assert result["diagnostics"][0]["phase"] == "analysis_validation"
    assert result["diagnostics"][1]["phase"] == "format_repair"


def test_network_failure_retries_full_analysis(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    synthesizer = _synthesizer([TimeoutError("timed out"), _valid_report()])

    result = synthesizer._try_llm_analysis("full prompt", {"date": "2026-07-14"})

    assert result["status"] == "success"
    assert result["source"] == "llm"
    assert result["attempt"] == 2
    assert result["diagnostics"][0]["phase"] == "analysis_request"


def test_all_failures_return_safe_fallback_with_sanitized_diagnostics(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    synthesizer = _synthesizer([
        '{"sector_recommendations": [',
        '{"still": "invalid"}',
        TimeoutError("timed out"),
    ])

    result = synthesizer._try_llm_analysis(
        "full prompt",
        {
            "date": "2026-07-14",
            "realtime_stock_prices": {
                "000001": {
                    "code": "000001",
                    "name": "平安银行",
                    "sector": "银行",
                    "price": 10,
                    "change_pct": 1,
                }
            },
            "_account_equity": 4000,
        },
    )

    assert result["status"] == "fallback"
    assert result["data"]["stock_recommendations"][0]["trade_eligible"] is False
    assert result["diagnostics"]
    assert all("raw_output" not in item for item in result["diagnostics"])
    assert any(item.get("response_sha256") for item in result["diagnostics"])


def test_realtime_fallback_uses_dashboard_schema_and_no_fake_trade_targets():
    synthesizer = Synthesizer.__new__(Synthesizer)
    report = synthesizer._generate_tradeable_fallback({
        "date": "2026-07-14",
        "realtime_stock_prices": {
            "000001": {
                "code": "000001",
                "name": "平安银行",
                "sector": "银行",
                "price": 10,
                "change_pct": 1,
            }
        },
        "_account_equity": 4000,
    })

    sector = report["sector_recommendations"][0]
    stock = report["stock_recommendations"][0]
    assert sector["sector_name"] == "银行"
    assert sector["rating"] == 0
    assert sector["degraded"] is True
    assert sector["risk_level"] == "unassessed"
    assert stock["entry_price"] is None
    assert stock["target_price"] is None
    assert stock["stop_loss_price"] is None
    assert stock["target_return_pct"] is None
    assert stock["timing"]["entry_condition"] == "仅观察，不生成进场条件"
