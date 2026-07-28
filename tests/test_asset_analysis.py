# -*- coding: utf-8 -*-
"""资产分析应排除充值、提现等外部现金流。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import server


TZ = ZoneInfo("Asia/Shanghai")


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 28, 19, 0, tzinfo=TZ)
        return value if tz is None else value.astimezone(tz)


def test_asset_analysis_excludes_cash_adjustments_from_returns(monkeypatch):
    class FakeLedger:
        @staticmethod
        def snapshots():
            return [
                {"captured_at": "2026-07-13T16:00:00+08:00", "total_equity": 4000},
                {"captured_at": "2026-07-24T16:00:00+08:00", "total_equity": 7952.27},
                {"captured_at": "2026-07-27T16:00:00+08:00", "total_equity": 8079.27},
            ]

        @staticmethod
        def cash_adjustments():
            return [{"amount": 4000, "created_at": "2026-07-20T12:00:00+08:00"}]

    class FakeService:
        ledger = FakeLedger()

        @staticmethod
        def get_account():
            return {
                "initial_cash": 4000,
                "cash": 6682.27,
                "total_equity": 8109.27,
            }

        @staticmethod
        def get_positions(_=None):
            return []

        @staticmethod
        def get_performance_metrics():
            return {"max_drawdown_pct": 0.6}

    monkeypatch.setattr(server, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "src.paper_trading.trading_service.TradingService", FakeService
    )

    payload = server.get_asset_analysis()

    assert payload["cash_flow_adjusted"] is True
    assert payload["effective_principal"] == 8000
    assert payload["total_return"] == 109.27
    assert payload["total_return_pct"] == pytest.approx(1.3659)
    assert [row["return_pct"] for row in payload["daily"]] == pytest.approx(
        [0.0, -0.5966, 1.5970, 0.3713]
    )
    assert payload["daily"][1]["external_cash_flow"] == 4000
    assert payload["monthly"][0]["return_pct"] == pytest.approx(1.3659)
