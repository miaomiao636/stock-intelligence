from datetime import datetime, timezone

import pytest


def provider():
    from src.research.outcomes import OutcomeProvider
    return OutcomeProvider()


def test_calendar_requires_every_calendar_day_not_weekday_guess(monkeypatch):
    p = provider()
    rows = [{"cal_date": "20260930", "is_open": 1},
            {"cal_date": "20261001", "is_open": 0},
            {"cal_date": "20261002", "is_open": 0}]
    monkeypatch.setattr(p, "_query", lambda *args, **kw: rows)
    assert p.calendar("2026-09-30", "2026-10-02") == ["2026-09-30"]
    rows.pop()
    assert p.calendar("2026-09-30", "2026-10-02") is None


def test_exact_date_close_and_adjustment_factor_validation(monkeypatch):
    p = provider()
    close = [{"ts_code": "600000.SH", "trade_date": "20260930", "close": 11}]
    factors = [{"ts_code": "600000.SH", "trade_date": "20260929", "adj_factor": 1.5},
               {"ts_code": "600000.SH", "trade_date": "20260930", "adj_factor": 1.5}]
    monkeypatch.setattr(p, "_query", lambda api, **kw: close if api == "daily" else factors)
    result = p.observation("600000", "2026-09-30", "2026-09-29T13:15:00+08:00")
    assert result["close"] == 11
    assert result["corporate_action_checked"] is True
    assert result["corporate_action_detected"] is False
    assert result["adjustment_evidence"] == {
        "reference_trade_date": "2026-09-29", "target_trade_date": "2026-09-30",
        "factors": [{"trade_date": "2026-09-29", "adj_factor": 1.5},
                    {"trade_date": "2026-09-30", "adj_factor": 1.5}],
    }
    factors[1]["adj_factor"] = 2.0
    assert p.observation("600000", "2026-09-30", "2026-09-29T13:15:00+08:00")["corporate_action_detected"] is True
    factors.pop()
    assert p.observation("600000", "2026-09-30", "2026-09-29T13:15:00+08:00")["corporate_action_checked"] is False
    close[0]["trade_date"] = "20260929"
    assert p.observation("600000", "2026-09-30", "2026-09-29T13:15:00+08:00") is None


def test_provider_has_bounded_requests_tls_no_redirect_and_no_error_echo(monkeypatch):
    p = provider()
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-test-token")
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"code": 0, "data": {"fields": ["cal_date", "is_open"], "items": [["20260930", 1]]}}
    def request(url, **kwargs):
        assert url == "https://api.tushare.pro"
        assert kwargs["allow_redirects"] is False
        assert 0 < kwargs["timeout"] <= 8
        calls.append(url)
        return Response()
    monkeypatch.setattr("requests.post", request)
    p.max_requests = 1
    assert p.calendar("2026-09-30", "2026-09-30") == ["2026-09-30"]
    from src.research.outcomes import OutcomeBudgetExhausted
    with pytest.raises(OutcomeBudgetExhausted):
        p.calendar("2026-09-29", "2026-09-30")
    assert len(calls) == 1
    assert p.diagnostics["budget_exhausted"] >= 1


def test_unconfigured_provider_and_unsupported_etf_do_not_send(monkeypatch):
    p = provider()
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr("requests.post", lambda *a, **kw: pytest.fail("No unconfigured network"))
    assert p.calendar("2026-09-30", "2026-09-30") is None
    assert p.observation("510300", "2026-09-30", "2026-09-29T13:15:00+08:00") is None


def test_maturity_runner_uses_existing_store_only_and_exposes_data_gaps(tmp_path, monkeypatch):
    from src.research import outcomes
    result = outcomes.run_maturity_review(tmp_path)
    assert result["status"] == "unavailable"
    assert not (tmp_path / "stock_intelligence.db").exists()
    from src.research.store import ResearchStore
    store = ResearchStore(tmp_path / "stock_intelligence.db")
    monkeypatch.setattr(ResearchStore, "review_matured_predictions", lambda *a, **kw: {
        "calendar_unavailable_count": 1, "unavailable_count": 0, "affects_trading": False})
    assert outcomes.run_maturity_review(tmp_path)["status"] == "degraded"
