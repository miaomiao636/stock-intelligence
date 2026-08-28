# -*- coding: utf-8 -*-

from datetime import date

from src.data_collectors import trading_calendar


class _TradeDateColumn:
    def astype(self, _type):
        return self

    def tolist(self):
        return ["2026-08-28", "2026-08-31"]


class _TradeDateFrame:
    def __getitem__(self, key):
        assert key == "trade_date"
        return _TradeDateColumn()


def test_count_trading_days_reuses_trade_date_calendar(monkeypatch):
    calls = []

    class FakeAk:
        @staticmethod
        def tool_trade_date_hist_sina():
            calls.append(True)
            return _TradeDateFrame()

    monkeypatch.setattr(trading_calendar, "AKSHARE_AVAILABLE", True)
    monkeypatch.setattr(trading_calendar, "ak", FakeAk)
    monkeypatch.setattr(trading_calendar, "_TRADE_DATES_CACHE", None, raising=False)

    first = trading_calendar.count_trading_days(date(2026, 8, 28), date(2026, 8, 31))
    second = trading_calendar.count_trading_days(date(2026, 8, 28), date(2026, 8, 31))

    assert first == 1
    assert second == 1
    assert len(calls) == 1
