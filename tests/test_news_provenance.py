from datetime import datetime, timezone

from src.data_collectors import news


def test_news_never_uses_fetch_time_as_publication_time(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        def search(self, **kwargs):
            return {"results": [
                {"title": "unknown", "url": "https://example.com/a"},
                {"title": "old", "url": "https://example.com/b", "published_date": "2026-01-01"},
            ]}
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setattr(news, "TAVILY_AVAILABLE", True)
    monkeypatch.setattr(news, "TavilyClient", Client)
    rows = news.search_news("market")
    assert rows[0]["published_at"] is None
    assert rows[0]["event_eligible"] is False
    assert rows[0]["temporal_status"] == "unknown_publication"
    assert rows[1]["published_at"].startswith("2026-01-01")
    assert rows[1]["event_eligible"] is False
    assert rows[1]["fetched_at"] != rows[1]["published_at"]


def test_news_time_guard_future_stale_and_fresh():
    now = datetime(2026, 9, 26, 10, tzinfo=timezone.utc)
    assert news.normalize_news_item({"published_date": "2026-09-26T09:00:00Z"}, now)["event_eligible"]
    assert not news.normalize_news_item({"published_date": "2026-09-27"}, now)["event_eligible"]
    assert not news.normalize_news_item({"published_date": "unparseable"}, now)["event_eligible"]


def test_model_does_not_receive_old_or_unknown_events_as_today():
    from src.analysis.synthesizer import Synthesizer
    synth = Synthesizer.__new__(Synthesizer)
    text = synth._format_news_summary([
        {"title": "Old event", "event_eligible": False},
        {"title": "Unknown time"},
        {"title": "Fresh event", "source": "official", "published_at": "2026-09-26T09:00:00+08:00", "event_eligible": True},
    ])
    assert "Old event" not in text and "Unknown time" not in text
    assert "Fresh event" in text and "2026-09-26" in text


def test_report_records_actual_versions_and_snapshots():
    from src.reporting.formatter import format_json_report
    report = format_json_report("2026-09-26", "morning", {"date": "2026-09-24"}, [], [], [])
    assert report["strategy_version"].startswith("sha256:")
    assert report["prompt_version"].startswith("sha256:")
    assert report["evidence_snapshots"][0]["snapshot"]["date"] == "2026-09-24"


def test_error_market_snapshot_is_never_complete_evidence():
    from src.reporting.formatter import format_json_report
    report = format_json_report("2026-09-26", "morning", {"error": "no feed"}, [], [], [])
    assert report["evidence_snapshots"][0]["history_incomplete"] is True
