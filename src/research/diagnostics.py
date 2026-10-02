"""Read-only operational diagnostics, separate from performance and live trading."""
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from src.research.health import tracking_freshness
from src.research.review import timestamp
from src.research.scorecard import build_scorecard


def _connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()


def prediction_scorecard(db_path, *, as_of=None):
    """GET never initializes schemas/accounts or fetches outcomes from providers."""
    cutoff = timestamp(as_of or datetime.now(timezone.utc))
    judgments, reviews = [], []
    if Path(db_path).exists():
        with closing(_connect(db_path)) as conn:
            conn.execute("BEGIN")
            if _has_table(conn, "research_judgments"):
                judgments = [json.loads(row[0]) for row in conn.execute(
                    "SELECT payload_json FROM research_judgments WHERE known_at<=? AND as_of<=? ORDER BY as_of,known_at,rowid", (cutoff, cutoff))]
            if _has_table(conn, "research_reviews"):
                reviews = [json.loads(row[0]) for row in conn.execute(
                    "SELECT payload_json FROM research_reviews WHERE known_at<=? AND as_of<=? ORDER BY known_at DESC,rowid DESC", (cutoff, cutoff))]
    return build_scorecard(judgments, reviews, as_of=cutoff)


def execution_diagnostics(data_dir, today=None):
    requested = (today or date.today()).isoformat()
    target, rows = None, []
    db_path = Path(data_dir) / "stock_intelligence.db"
    if db_path.exists():
        with closing(_connect(db_path)) as conn:
            conn.execute("BEGIN")
            if _has_table(conn, "paper_entry_plans"):
                target = conn.execute("SELECT MAX(trade_date) FROM paper_entry_plans WHERE trade_date<=?", (requested,)).fetchone()[0]
                if target:
                    rows = conn.execute("SELECT status,reason FROM paper_entry_plans WHERE trade_date=?", (target,)).fetchall()
    counts = Counter(row["status"] for row in rows)
    summary = {"trade_date": target, "total": len(rows), "by_status": dict(counts),
               "waiting": sum(counts[s] for s in ("ready", "waiting_trigger", "waiting_quote")),
               "filled": counts["filled"], "rejected": counts["rejected"], "expired": counts["expired"],
               "reason_counts": dict(Counter(row["reason"] for row in rows if row["reason"]))}
    return {"requested_date": requested, "trade_date": target,
            "is_latest_available": bool(target and target != requested), "summary": summary,
            "note": ("当日无计划记录，展示最近历史记录；不代表今天的成交情况。" if target and target != requested
                     else "暂无计划记录，不能据此判断失败或无投资机会。" if not target
                     else "计划状态与原因不是收益；允许等待或空仓，不因此放宽风控。")}


def build_diagnostics(data_dir, today=None):
    from src.tracking.tracker import RecommendationTracker
    base = Path(data_dir)
    data = RecommendationTracker(base).get_latest_tracking() if (base / "tracker").is_dir() else None
    data = data or {}
    return {"as_of": timestamp(datetime.now(timezone.utc)),
            "tracking": {"available": bool(data.get("tracking_date")),
                         "tracking_date": data.get("tracking_date"),
                         "freshness": tracking_freshness(data, today=today),
                         "data_quality": data.get("data_quality", {"status": "unknown"}),
                         "tracking_quality": data.get("tracking_quality", {"status": "unknown"})},
            "execution": execution_diagnostics(base, today),
            "scorecard": prediction_scorecard(base / "stock_intelligence.db")}
