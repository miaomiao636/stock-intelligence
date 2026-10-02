"""Append-only research records in the existing SQLite database.

This module never opens an account, edits a trading table, calls a model, or
promotes a strategy. Historical imports record today's knowledge boundary;
they must not be presented as information that the system knew in the past.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.research.review import normalize_review, timestamp


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _required_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _ids(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("evidence references must be a list")
    return [_required_text(item.get("evidence_id") if isinstance(item, dict) else item, "evidence_id") for item in value]


def _view(item: dict) -> dict:
    """UI aliases are presentation only; persisted domain field names stay stable."""
    result = dict(item)
    for key in ("review_id", "judgment_id", "lesson_id", "experiment_id", "evidence_id"):
        if key in result:
            result["id"] = result[key]
            break
    if "judgment_id" in result and "review_id" not in result:
        result.setdefault("stock_name", "")
        result.setdefault("status", "history_incomplete" if result.get("history_incomplete") else "recorded")
    if "lesson_id" in result:
        result.setdefault("summary", result.get("body", ""))
    if "experiment_id" in result:
        evaluation = result.get("result") or {}
        result.setdefault("metrics", {"rank_ic": evaluation.get("rank_ic"), "comparison": evaluation.get("comparison")})
        result.setdefault("limitations", evaluation.get("warnings", []) + evaluation.get("errors", []))
    return result


def _supports_stock(evidence: dict, code: str, as_of: str) -> bool:
    """A market-wide snapshot is context, not proof about every stock."""
    if evidence.get("history_incomplete") or evidence["as_of"] > as_of:
        return False
    snapshot = evidence.get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot or snapshot.get("error"):
        return False
    kind = evidence.get("kind")
    if kind == "market_snapshot":
        prices = snapshot.get("realtime_stock_prices")
        quote = prices.get(code) if isinstance(prices, dict) else None
        if quote is None and (snapshot.get("stock_code") or snapshot.get("code")) == code:
            quote = snapshot
        if not isinstance(quote, dict) or quote.get("error") or quote.get("source_time_reliable") is False:
            return False
        try:
            price = float(quote.get("price") or quote.get("close") or 0)
            quote_time = quote.get("quote_time") or quote.get("as_of") or quote.get("data_as_of")
            return bool(math.isfinite(price) and price > 0 and quote_time and timestamp(quote_time) <= as_of)
        except (ValueError, TypeError):
            return False
    return (evidence.get("stock_code") or snapshot.get("stock_code") or snapshot.get("code")) == code


class ResearchStore:
    """Incremental research tables, with server-controlled append timestamps."""

    def __init__(self, db_path: Path | str | None = None, *, clock: Callable | None = None):
        if db_path is None:
            from src.storage import db
            db_path = db.DB_PATH
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._init_schema()

    def _now(self) -> str:
        return timestamp(self._clock())

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_evidence (
                    evidence_id TEXT PRIMARY KEY, as_of TEXT NOT NULL,
                    known_at TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_judgments (
                    judgment_id TEXT PRIMARY KEY, stock_code TEXT NOT NULL,
                    as_of TEXT NOT NULL, known_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_reviews (
                    review_id TEXT PRIMARY KEY, judgment_id TEXT NOT NULL
                    REFERENCES research_judgments(judgment_id), as_of TEXT NOT NULL,
                    known_at TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_maturity_attempts (
                    judgment_id TEXT NOT NULL REFERENCES research_judgments(judgment_id),
                    protocol_version TEXT NOT NULL, last_attempt_at TEXT NOT NULL,
                    PRIMARY KEY(judgment_id,protocol_version));
                CREATE TABLE IF NOT EXISTS research_lessons (
                    lesson_id TEXT PRIMARY KEY, known_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_lesson_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id TEXT NOT NULL REFERENCES research_lessons(lesson_id),
                    known_at TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_lesson_origins (
                    origin_key TEXT PRIMARY KEY,
                    lesson_id TEXT NOT NULL REFERENCES research_lessons(lesson_id));
                CREATE TABLE IF NOT EXISTS research_imports (
                    import_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                    checksum TEXT NOT NULL, known_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL, UNIQUE(source_id,checksum));
                CREATE TABLE IF NOT EXISTS research_experiments (
                    experiment_id TEXT PRIMARY KEY, known_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_research_judgment_asof
                    ON research_judgments(stock_code,as_of,known_at);
                CREATE INDEX IF NOT EXISTS idx_research_review_judgment
                    ON research_reviews(judgment_id,known_at);
                CREATE INDEX IF NOT EXISTS idx_research_lesson_events
                    ON research_lesson_events(lesson_id,known_at);
            """)
            # Historical judgments and results are never silently rewritten.
            for table in ("research_evidence", "research_judgments", "research_reviews",
                          "research_lessons", "research_lesson_events", "research_imports",
                          "research_experiments", "research_lesson_origins"):
                for operation in ("UPDATE", "DELETE"):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'research records are append-only'); END""")

    @staticmethod
    def _payload(row):
        return _view(json.loads(row["payload_json"])) if row else None

    def _cutoff(self, as_of) -> str:
        return timestamp(as_of, end_of_day=True) if as_of is not None else self._now()

    def record_evidence(self, payload: dict) -> dict:
        with self._connect() as conn:
            return self._record_evidence(conn, payload, self._now())

    def _record_evidence(self, conn, payload, known_at):
        source = _required_text(payload.get("source"), "source")
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        kind = payload.get("kind", "unknown")
        fetch_time_missing = not bool(payload.get("fetched_at"))
        fetched_at = timestamp(payload.get("fetched_at") or known_at)
        if fetched_at > known_at:
            raise ValueError("future fetched_at cannot be recorded as known evidence")
        published_at = timestamp(payload["published_at"]) if payload.get("published_at") else None
        available_at = timestamp(payload["available_at"]) if payload.get("available_at") else None
        if available_at and available_at > fetched_at:
            raise ValueError("available_at cannot be later than fetched_at")
        if published_at and available_at and published_at > available_at:
            raise ValueError("published_at cannot be later than available_at")
        if published_at and published_at > fetched_at:
            raise ValueError("future published_at cannot be known at fetch time")
        incomplete = bool(payload.get("history_incomplete")) or fetch_time_missing or available_at is None or "snapshot" not in payload
        if snapshot.get("error") or not payload.get("snapshot"):
            incomplete = True
        if kind == "news" and (published_at is None or snapshot.get("event_eligible") is False):
            incomplete = True
        raw_publication = payload.get("published_at")
        precision = payload.get("publication_precision") or snapshot.get("publication_precision") or (
            "date" if isinstance(raw_publication, str) and len(raw_publication) == 10 else "timestamp" if published_at else "unknown")
        result = {**payload, "evidence_id": uuid.uuid4().hex, "source": source,
                  "kind": kind, "known_at": known_at,
                  "published_at": published_at, "fetched_at": fetched_at,
                  "available_at": available_at, "history_incomplete": incomplete,
                  "publication_precision": precision,
                  "fetch_time_basis": "import_time_only" if fetch_time_missing else "source_recorded"}
        underlying_asof = payload.get("as_of") or snapshot.get("as_of") or snapshot.get("data_as_of")
        result["as_of"] = timestamp(underlying_asof or available_at or fetched_at)
        if result["as_of"] > fetched_at:
            raise ValueError("future data timestamp cannot be known at fetch time")
        result["data_time_basis"] = "source_recorded" if underlying_asof else "availability_or_fetch_only"
        conn.execute("INSERT INTO research_evidence VALUES(?,?,?,?)",
                     (result["evidence_id"], result["as_of"], known_at, _json(result)))
        return _view(result)

    def _import_evidence(self, conn, payload, known_at, report_asof):
        """An invalid provider timestamp is quarantined, not silently repaired."""
        try:
            if not isinstance(payload, dict):
                raise ValueError("snapshot must be an object")
            return self._record_evidence(conn, payload, known_at)
        except ValueError as exc:
            return self._record_evidence(conn, {
                "source": str(payload.get("source") or "invalid_source") if isinstance(payload, dict) else "invalid_source",
                "kind": "quarantined_snapshot", "as_of": report_asof,
                "fetched_at": known_at, "available_at": None, "published_at": None,
                "snapshot": payload, "history_incomplete": True,
                "validation_error": str(exc), "provenance": {"original_times_not_overwritten": True}}, known_at)

    def get_evidence(self, evidence_id: str, as_of=None):
        cutoff = self._cutoff(as_of)
        with self._connect() as conn:
            return self._payload(conn.execute("SELECT payload_json FROM research_evidence WHERE evidence_id=? AND known_at<=? AND as_of<=?", (evidence_id, cutoff, cutoff)).fetchone())

    def list_evidence(self, *, as_of=None, limit=100) -> list[dict]:
        cutoff = self._cutoff(as_of)
        with self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM research_evidence WHERE known_at<=? AND as_of<=? ORDER BY known_at DESC,rowid DESC LIMIT ?", (cutoff, cutoff, self._limit(limit))).fetchall()
            return [self._payload(row) for row in rows]

    def record_judgment(self, payload: dict) -> dict:
        with self._connect() as conn:
            return self._record_judgment(conn, payload, self._now())

    def _record_judgment(self, conn, payload, known_at):
        code = _required_text(payload.get("stock_code"), "stock_code")
        as_of = timestamp(payload.get("as_of"))
        thesis = _required_text(payload.get("thesis"), "thesis")
        horizon = _required_text(str(payload.get("horizon") or "unknown"), "horizon")
        refs = _ids(payload.get("supporting_evidence")) + _ids(payload.get("counter_evidence"))
        incomplete = bool(payload.get("history_incomplete")) or not refs
        for evidence_id in refs:
            ev = self._payload(conn.execute("SELECT payload_json FROM research_evidence WHERE evidence_id=?", (evidence_id,)).fetchone())
            if ev is None:
                raise ValueError(f"unknown evidence: {evidence_id}")
            if ev["as_of"] > as_of or (ev.get("available_at") and ev["available_at"] > as_of):
                raise ValueError("future evidence cannot support past judgment")
            incomplete = incomplete or ev["history_incomplete"]
        versions = {key: str(payload.get(key) or "unknown") for key in ("model_version", "strategy_version", "prompt_version", "data_version")}
        incomplete = incomplete or any(value == "unknown" for value in versions.values())
        result = {**payload, **versions, "judgment_id": uuid.uuid4().hex, "stock_code": code,
                  "as_of": as_of, "known_at": known_at, "horizon": horizon, "thesis": thesis,
                  "supporting_evidence": payload.get("supporting_evidence") or [],
                  "counter_evidence": payload.get("counter_evidence") or [],
                  "invalidations": payload.get("invalidations") or [],
                  "provenance": payload.get("provenance") or {},
                  "history_incomplete": bool(incomplete)}
        from src.research.maturity import build_forecast_spec, normalize_forecast_spec
        result["forecast_spec"] = normalize_forecast_spec(payload.get("forecast_spec"), judgment=result)
        spec = result["forecast_spec"]
        if spec["status"] == "registered" and spec["reference_evidence_id"] not in refs:
            result["forecast_spec"] = {**spec, "status": "excluded", "reason": "reference_evidence_not_linked"}
        elif spec["status"] == "registered":
            source = self._payload(conn.execute("SELECT payload_json FROM research_evidence WHERE evidence_id=?",
                                               (spec["reference_evidence_id"],)).fetchone())
            actual = build_forecast_spec({}, stock_code=code, snapshots=[source])
            if any(spec.get(field) != actual.get(field) for field in ("reference_price", "reference_as_of", "reference_evidence_id", "reference_basis")):
                result["forecast_spec"] = {**spec, "status": "excluded", "reason": "reference_evidence_mismatch"}
        conn.execute("INSERT INTO research_judgments VALUES(?,?,?,?,?)", (result["judgment_id"], code, as_of, known_at, _json(result)))
        return _view(result)

    @staticmethod
    def _limit(limit):
        return max(1, min(int(limit), 1000))

    def get_judgment(self, judgment_id: str, as_of=None):
        cutoff = self._cutoff(as_of)
        with self._connect() as conn:
            return self._payload(conn.execute("SELECT payload_json FROM research_judgments WHERE judgment_id=? AND known_at<=? AND as_of<=?", (judgment_id, cutoff, cutoff)).fetchone())

    def list_judgments(self, *, stock_code=None, as_of=None, limit=100) -> list[dict]:
        cutoff = self._cutoff(as_of)
        sql = "SELECT payload_json FROM research_judgments WHERE known_at<=? AND as_of<=?"
        args = [cutoff, cutoff]
        if stock_code:
            sql += " AND stock_code=?"
            args.append(stock_code)
        sql += " ORDER BY as_of DESC,known_at DESC,rowid DESC LIMIT ?"
        with self._connect() as conn:
            return [self._payload(row) for row in conn.execute(sql, (*args, self._limit(limit)))]

    def append_review(self, judgment_id: str, payload: dict) -> dict:
        with self._connect() as conn:
            judgment = self._payload(conn.execute("SELECT payload_json FROM research_judgments WHERE judgment_id=?", (judgment_id,)).fetchone())
            if judgment is None:
                raise ValueError("unknown judgment_id")
            return self._append_review(conn, judgment, payload, self._now())

    def _append_review(self, conn, judgment, payload, known_at):
        result = normalize_review(payload, judgment)
        result.update(review_id=uuid.uuid4().hex, judgment_id=judgment["judgment_id"], known_at=known_at)
        conn.execute("INSERT INTO research_reviews VALUES(?,?,?,?,?)", (result["review_id"], result["judgment_id"], result["as_of"], known_at, _json(result)))
        return _view(result)

    def list_reviews(self, judgment_id=None, *, as_of=None, limit=100) -> list[dict]:
        cutoff = self._cutoff(as_of)
        sql = "SELECT payload_json FROM research_reviews WHERE known_at<=? AND as_of<=?"
        args = [cutoff, cutoff]
        if judgment_id:
            sql += " AND judgment_id=?"
            args.append(judgment_id)
        with self._connect() as conn:
            return [self._payload(row) for row in conn.execute(sql + " ORDER BY known_at DESC,rowid DESC LIMIT ?", (*args, self._limit(limit)))]

    def get_prediction_scorecard(self, *, as_of=None) -> dict:
        """Read all eligible rows, independently of paginated UI list limits."""
        from src.research.scorecard import build_scorecard
        cutoff = min(self._cutoff(as_of), self._now())
        with self._connect() as conn:
            judgments = [self._payload(row) for row in conn.execute(
                "SELECT payload_json FROM research_judgments WHERE known_at<=? AND as_of<=? ORDER BY as_of,known_at,rowid",
                (cutoff, cutoff))]
            reviews = [self._payload(row) for row in conn.execute(
                "SELECT payload_json FROM research_reviews WHERE known_at<=? AND as_of<=? "
                "ORDER BY known_at DESC,rowid DESC", (cutoff, cutoff))]
        return build_scorecard(judgments, reviews, as_of=cutoff)

    def review_matured_predictions(self, *, as_of, calendar_provider, observation_provider) -> dict:
        """Append idempotent direction reviews; never create orders or edit PnL.

        calendar_provider(start_date, end_date) returns a complete list of ISO
        exchange session dates in the inclusive interval, or None on failure.
        observation_provider(code, target_date, reference_as_of) returns the raw
        target closing quote contract in maturity.maturity_review, or None.
        No external provider is invoked while holding a database transaction.
        Existing verified outcomes are frozen; missing data can be retried.
        """
        from src.research.maturity import (PROTOCOL_VERSION, cohort_key, local_day,
                                           maturity_target, maturity_review)
        from src.research.outcomes import OutcomeBudgetExhausted
        cutoff = min(self._cutoff(as_of), self._now())
        with self._connect() as conn:
            judgments = [self._payload(row) for row in conn.execute(
                "SELECT payload_json FROM research_judgments WHERE known_at<=? AND as_of<=? "
                "ORDER BY as_of,known_at,rowid", (cutoff, cutoff))]
            reviews = [self._payload(row) for row in conn.execute(
                "SELECT payload_json FROM research_reviews WHERE known_at<=? AND as_of<=? "
                "ORDER BY known_at DESC,rowid DESC", (cutoff, cutoff))]
            attempts = {row["judgment_id"]: row["last_attempt_at"] for row in conn.execute(
                "SELECT judgment_id,last_attempt_at FROM research_maturity_attempts WHERE protocol_version=?",
                (PROTOCOL_VERSION,))}
        terminal = {row["judgment_id"] for row in reviews
                    if row.get("review_kind") == PROTOCOL_VERSION
                    and str(row.get("review_id", "")).startswith("maturity-")
                    and row.get("prediction_status") in {"correct", "incorrect"}}
        result = {"as_of": cutoff, "protocol_version": PROTOCOL_VERSION,
                  "created_count": 0, "pending_count": 0, "excluded_count": 0,
                  "duplicate_count": 0, "calendar_unavailable_count": 0,
                  "already_reviewed_count": 0, "unavailable_count": 0, "review_ids": [],
                  "deferred_count": 0,
                  "affects_trading": False}
        calendars, seen, queue = {}, set(), []
        # Preserve first-forecast cohort selection before retry scheduling.
        # Reordering raw judgments would allow a later revision to replace it.
        for item in judgments:
            key = cohort_key(item)
            if key is None:
                result["excluded_count"] += 1
                continue
            if key in seen:
                result["duplicate_count"] += 1
                continue
            seen.add(key)
            if item.get("history_incomplete"):
                result["excluded_count"] += 1
                continue
            if item["judgment_id"] in terminal:
                result["already_reviewed_count"] += 1
                continue
            queue.append(item)
        # Queue metadata is deliberately separate from immutable evidence and
        # reviews. Identical inconclusive retries have one review ID, but still
        # advance their last-attempt time so old missing data cannot starve new
        # forecasts. Stable sorting preserves original cohort order on ties.
        queue.sort(key=lambda item: attempts.get(item["judgment_id"], ""))

        def mark_attempt(item):
            with self._connect() as conn:
                conn.execute("""INSERT INTO research_maturity_attempts VALUES(?,?,?)
                    ON CONFLICT(judgment_id,protocol_version) DO UPDATE SET
                    last_attempt_at=MAX(last_attempt_at,excluded.last_attempt_at)""",
                    (item["judgment_id"], PROTOCOL_VERSION, self._now()))

        for index, item in enumerate(queue):
            start, end = local_day(item["as_of"]), local_day(cutoff)
            if (start, end) not in calendars:
                try:
                    calendars[start, end] = calendar_provider(start, end)
                except OutcomeBudgetExhausted:
                    result["deferred_count"] = len(queue) - index
                    break
                except Exception:
                    # Providers may include credentials in exception text.
                    calendars[start, end] = None
            target, status = maturity_target(item, as_of=cutoff, sessions=calendars[start, end])
            if status == "calendar_unavailable":
                mark_attempt(item)
                result["calendar_unavailable_count"] += 1
                continue
            if status == "pending":
                mark_attempt(item)
                result["pending_count"] += 1
                continue
            try:
                observation = observation_provider(item["stock_code"], target,
                                                   item["forecast_spec"]["reference_as_of"])
            except OutcomeBudgetExhausted:
                result["deferred_count"] = len(queue) - index
                break
            except Exception:
                observation = None
            mark_attempt(item)
            payload = maturity_review(item, target=target, observation=observation, as_of=cutoff)
            if payload["prediction_status"] == "inconclusive":
                result["unavailable_count"] += 1
            # Stable across retry times. Changed evidence is a new immutable
            # review, never an UPDATE. Concurrent retries share the primary key.
            identity = {key: value for key, value in payload.items() if key != "as_of"}
            review_id = "maturity-" + hashlib.sha256(_json([item["judgment_id"], identity]).encode()).hexdigest()
            known_at = self._now()
            normalized = normalize_review(payload, item)
            normalized.update(review_id=review_id, judgment_id=item["judgment_id"], known_at=known_at)
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute("SELECT 1 FROM research_reviews WHERE review_id=?", (review_id,)).fetchone()
                if existing:
                    continue
                # Another worker might have completed while the provider ran.
                outcomes = conn.execute("SELECT payload_json FROM research_reviews WHERE judgment_id=?",
                                        (item["judgment_id"],)).fetchall()
                if any((r := self._payload(row)).get("review_kind") == PROTOCOL_VERSION
                       and str(r.get("review_id", "")).startswith("maturity-")
                       and r.get("prediction_status") in {"correct", "incorrect"} for row in outcomes):
                    continue
                conn.execute("INSERT INTO research_reviews VALUES(?,?,?,?,?)",
                    (review_id, item["judgment_id"], normalized["as_of"], known_at, _json(normalized)))
            result["created_count"] += 1
            result["review_ids"].append(review_id)
        return result

    def create_lesson(self, payload: dict) -> dict:
        with self._connect() as conn:
            return self._create_lesson(conn, payload, self._now())

    def _create_lesson(self, conn, payload, known_at):
        title = _required_text(payload.get("title"), "title")
        body = _required_text(payload.get("body"), "body")
        for judgment_id in payload.get("judgment_ids", []):
            if not conn.execute("SELECT 1 FROM research_judgments WHERE judgment_id=?", (judgment_id,)).fetchone():
                raise ValueError("unknown judgment_id")
        result = {**payload, "lesson_id": uuid.uuid4().hex, "title": title, "body": body,
                  "known_at": known_at, "status": "candidate", "affects_trading": False}
        conn.execute("INSERT INTO research_lessons VALUES(?,?,?)", (result["lesson_id"], result["known_at"], _json(result)))
        return _view(result)

    def generate_candidate_lessons(self, *, source_id: str, as_of=None) -> dict:
        """Group observed review diagnostics, without inventing market rules.

        At most four candidate notes per source/judgment set. Repeated closing
        jobs/CLI imports do not create duplicates; a human still has to review
        and explicitly transition a candidate. No note can alter risk controls.
        """
        source_id = _required_text(source_id, "source_id")
        if not source_id.startswith("evaluation:"):
            source_id = "evaluation:" + source_id
        cutoff, known_at = self._cutoff(as_of), self._now()
        definitions = {
            "not_triggered": ("推荐未触发，需要检查执行条件", "条判断没有触发实际进场；这不代表预测失败或持仓亏损。应核对计划价、有效期和执行回执，不因此放宽风控。"),
            "history_incomplete": ("历史判断缺少完整证据", "条判断的历史快照或版本记录不完整；这不代表这些判断正确或错误。应保留缺口，不能补写当时未知的信息或纳入已验证胜率。"),
            "source_insufficient": ("研究假设尚无可追溯支持快照", "条判断没有完整的支持证据引用；这不代表市场没有机会。应优先补齐来源、公开时间和数据快照，再评估假设。"),
            "pnl_unclosed": ("执行已有证据但净盈亏尚未闭合", "条判断已有买入成交证据，但复盘输入不足以确认已实现净盈亏；这不代表盈利或亏损。必须从同一账本匹配卖出和双边费用后确认。"),
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT payload_json FROM research_reviews WHERE known_at<=? AND as_of<=? ORDER BY known_at DESC,rowid DESC", (cutoff, cutoff)).fetchall()
            latest = {}
            for row in rows:
                review = self._payload(row)
                if review.get("provenance", {}).get("source_id") == source_id:
                    latest.setdefault(review["judgment_id"], review)
            grouped = {kind: [] for kind in definitions}
            for judgment_id, review in latest.items():
                item = self._payload(conn.execute("SELECT payload_json FROM research_judgments WHERE judgment_id=? AND known_at<=?", (judgment_id, cutoff)).fetchone())
                if item is None:
                    continue
                if review["execution_status"] == "not_triggered":
                    grouped["not_triggered"].append(review)
                if review["prediction_status"] == "history_incomplete" or item["history_incomplete"]:
                    grouped["history_incomplete"].append(review)
                if not item.get("supporting_evidence"):
                    grouped["source_insufficient"].append(review)
                if review["execution_status"] in {"filled", "closed"} and review["net_pnl_status"] in {"unavailable", "unrealized", "history_incomplete"}:
                    grouped["pnl_unclosed"].append(review)
            lesson_ids, created = [], 0
            for kind, reviews in grouped.items():
                if not reviews:
                    continue
                judgment_ids = sorted({review["judgment_id"] for review in reviews})
                origin_key = hashlib.sha256(_json([source_id, kind, judgment_ids]).encode()).hexdigest()
                previous = conn.execute("SELECT lesson_id FROM research_lesson_origins WHERE origin_key=?", (origin_key,)).fetchone()
                if previous:
                    lesson_ids.append(previous["lesson_id"])
                    continue
                title, diagnostic = definitions[kind]
                lesson = self._create_lesson(conn, {"title": title, "body": f"本次 {len(judgment_ids)} " + diagnostic,
                    "judgment_ids": judgment_ids, "review_ids": [review["review_id"] for review in reviews],
                    "sample_count": len(judgment_ids), "diagnostic_kind": kind,
                    "provenance": {"source_id": source_id, "as_of": cutoff, "origin_key": origin_key,
                                   "generator": "deterministic_review_diagnostics_v1"},
                    "validation_scope": "unvalidated_operational_observation",
                    "requires_human_review": True}, known_at)
                conn.execute("INSERT INTO research_lesson_origins VALUES(?,?)", (origin_key, lesson["lesson_id"]))
                lesson_ids.append(lesson["lesson_id"])
                created += 1
            return {"source_id": source_id, "created_count": created, "lesson_ids": lesson_ids,
                    "reviewed_judgment_count": len(latest), "status": "candidate", "affects_trading": False}

    def list_lessons(self, *, status=None, as_of=None, limit=100) -> list[dict]:
        if status and status not in {"candidate", "validated", "rejected", "retired"}:
            raise ValueError("invalid lesson status")
        cutoff = self._cutoff(as_of)
        with self._connect() as conn:
            # Apply historical state before filtering/limiting, never leak a later approval.
            rows = conn.execute("SELECT payload_json FROM research_lessons WHERE known_at<=? ORDER BY known_at DESC,rowid DESC", (cutoff,)).fetchall()
            output = []
            for row in rows:
                result = self._payload(row)
                event = self._payload(conn.execute("SELECT payload_json FROM research_lesson_events WHERE lesson_id=? AND known_at<=? ORDER BY known_at DESC,event_id DESC LIMIT 1", (result["lesson_id"], cutoff)).fetchone())
                if event:
                    result.update(status=event["status"], status_event=event)
                if status is None or result["status"] == status:
                    output.append(result)
                if len(output) >= self._limit(limit):
                    break
            return output

    def transition_lesson(self, lesson_id: str, status: str, *, actor: str,
                          reason: str, confirmed: bool = False) -> dict:
        if confirmed is not True:
            raise ValueError("explicit human confirmation is required")
        actor, reason = _required_text(actor, "actor"), _required_text(reason, "reason")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lesson = self._payload(conn.execute("SELECT payload_json FROM research_lessons WHERE lesson_id=?", (lesson_id,)).fetchone())
            if lesson is None:
                raise ValueError("unknown lesson_id")
            previous = self._payload(conn.execute("SELECT payload_json FROM research_lesson_events WHERE lesson_id=? ORDER BY event_id DESC LIMIT 1", (lesson_id,)).fetchone())
            old_status = previous["status"] if previous else "candidate"
            allowed = {"candidate": {"validated", "rejected", "retired"}, "validated": {"retired"}, "rejected": {"retired"}, "retired": set()}
            if status not in allowed[old_status]:
                raise ValueError(f"invalid lesson transition: {old_status} -> {status}")
            event = {"lesson_id": lesson_id, "from_status": old_status, "status": status,
                     "actor": actor, "reason": reason, "known_at": self._now(), "confirmed": True}
            conn.execute("INSERT INTO research_lesson_events(lesson_id,known_at,payload_json) VALUES(?,?,?)", (lesson_id, event["known_at"], _json(event)))
            return _view({**lesson, "status": status, "status_event": event, "affects_trading": False})

    def import_report(self, report: dict, source_id: str) -> dict:
        """Archive report claims; do not reconstruct missing historical evidence.

        A revised source creates another immutable version. Content-identical
        retries are no-ops. A report's prose is not treated as a provider snapshot.
        """
        source_id = _required_text(source_id, "source_id")
        # The hook writes its receipt back into JSON after importing; that local
        # receipt is not a new research opinion when the CLI reads it again.
        canonical_report = {key: value for key, value in report.items() if key not in {"research_archive", "errors"}}
        if isinstance(canonical_report.get("source_status"), dict):
            canonical_report["source_status"] = {key: value for key, value in canonical_report["source_status"].items()
                                                  if key not in {"tracking", "feishu", "notification", "research_archive"}}
        checksum = hashlib.sha256(_json(canonical_report).encode()).hexdigest()
        known_at = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._payload(conn.execute("SELECT payload_json FROM research_imports WHERE source_id=? AND checksum=?", (source_id, checksum)).fetchone())
            if existing:
                return {**existing, "imported": False}
            as_of_raw = report.get("data_as_of") or report.get("created_at") or report.get("date")
            as_of = timestamp(as_of_raw)
            evidence_ids = []
            # The original producer's availability times are preserved only when
            # explicitly present. Legacy published_at may have meant fetch time.
            for news in report.get("news_sources") or []:
                if not isinstance(news, dict):
                    continue
                ev = self._import_evidence(conn, {"source": str(news.get("source") or news.get("url") or source_id),
                    "kind": "historical_news_reference", "as_of": as_of,
                    "published_at": news.get("published_at"), "fetched_at": news.get("fetched_at") or known_at,
                    "available_at": None, "snapshot": news, "history_incomplete": True,
                    "provenance": {"source_id": source_id, "report_checksum": checksum,
                                   "publication_time_verified": False}}, known_at, as_of)
                evidence_ids.append(ev["evidence_id"])
            # Full provider snapshots are only accepted through this explicit field;
            # existing report/summary fields are never relabeled as original data.
            verified_snapshots = []
            snapshot_gaps = []
            for snapshot in report.get("evidence_snapshots") or []:
                ev = self._import_evidence(conn, snapshot, known_at, as_of)
                evidence_ids.append(ev["evidence_id"])
                if not ev["history_incomplete"] and ev["available_at"] <= as_of and ev["as_of"] <= as_of:
                    verified_snapshots.append(ev)
                else:
                    snapshot_gaps.append(ev["evidence_id"])
            judgment_ids = []
            base_incomplete = bool(snapshot_gaps) or bool(report.get("analysis_degraded"))
            history_incomplete = base_incomplete or not bool(verified_snapshots)
            for rec in report.get("stock_recommendations") or []:
                stock_code = rec.get("stock_code") or rec.get("code")
                stock_evidence_ids = [ev["evidence_id"] for ev in verified_snapshots if _supports_stock(ev, stock_code, as_of)]
                reason = rec.get("thesis") or rec.get("reason") or rec.get("recommendation_reason") or rec.get("reasons") or "原报告未保存明确研究假设"
                if not isinstance(reason, str):
                    reason = _json(reason)
                from src.research.maturity import build_forecast_spec
                forecast_spec = build_forecast_spec(rec, stock_code=stock_code,
                    snapshots=[ev for ev in verified_snapshots if ev["evidence_id"] in stock_evidence_ids])
                item = self._record_judgment(conn, {"stock_code": stock_code,
                    "stock_name": rec.get("name"), "as_of": as_of, "horizon": rec.get("horizon") or "unknown",
                    "horizon_days": rec.get("horizon_days"),
                    "forecast_spec": forecast_spec,
                    "thesis": reason, "supporting_evidence": stock_evidence_ids, "counter_evidence": [],
                    "context_evidence": [ev["evidence_id"] for ev in verified_snapshots if ev["evidence_id"] not in stock_evidence_ids],
                    "claim_verification_status": "not_verified", "evidence_coverage": "input_snapshot_only",
                    "invalidations": rec.get("invalidations") or [],
                    "model_version": report.get("model_version") or "unknown",
                    "strategy_version": report.get("strategy_version") or "unknown",
                    "prompt_version": report.get("prompt_version") or "unknown",
                    "data_version": report.get("data_version") or "unknown",
                    "source_report_id": report.get("recommendation_id") or source_id,
                    "source_run_id": report.get("run_id"), "history_incomplete": base_incomplete or not bool(stock_evidence_ids),
                    "provenance": {"source_id": source_id, "report_checksum": checksum,
                        "imported_at": known_at, "original_report_as_of": as_of_raw,
                        "report_date": report.get("date"), "report_type": report.get("type"),
                        "snapshot_gaps": snapshot_gaps,
                        "original_recommendation": rec, "historical_import": not bool(report.get("evidence_snapshots"))}}, known_at)
                judgment_ids.append(item["judgment_id"])
                history_incomplete = history_incomplete or item["history_incomplete"]
            summary = {"import_id": uuid.uuid4().hex, "source_id": source_id, "checksum": checksum,
                       "known_at": known_at, "imported": True, "judgment_ids": judgment_ids,
                       "evidence_ids": evidence_ids, "history_incomplete": history_incomplete,
                       "warnings": ["历史导入不能证明当时系统已掌握这些信息；缺失的原始快照、反证和版本不回填猜测。"]}
            conn.execute("INSERT INTO research_imports VALUES(?,?,?,?,?)", (summary["import_id"], source_id, checksum, known_at, _json(summary)))
            return summary

    def review_from_evaluation(self, evaluation: dict, *, source_id: str) -> dict:
        """Append an honest daily observation, not a fictional completed trade.

        Existing evaluator returns mark-to-close percentages and may include
        theoretical fills. Neither is actual realized account profit, and a
        daily mark is not the completion of a multi-day prediction horizon.
        """
        source_id = "evaluation:" + _required_text(source_id, "source_id")
        checksum = hashlib.sha256(_json(evaluation).encode()).hexdigest()
        known_at = self._now()
        as_of = timestamp(evaluation.get("evaluated_at") or evaluation.get("date"), end_of_day=True)
        rec_date = _required_text(evaluation.get("recommendation_date"), "recommendation_date")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._payload(conn.execute("SELECT payload_json FROM research_imports WHERE source_id=? AND checksum=?", (source_id, checksum)).fetchone())
            if existing:
                return {**existing, "imported": False}
            rows = conn.execute("SELECT payload_json FROM research_judgments WHERE as_of<=? AND known_at<=? ORDER BY known_at DESC,rowid DESC", (as_of, known_at)).fetchall()
            by_code = {}
            for row in rows:
                item = self._payload(row)
                provenance = item.get("provenance", {})
                if provenance.get("report_date") != rec_date:
                    continue
                if evaluation.get("recommendation_type") and provenance.get("report_type") != evaluation["recommendation_type"]:
                    continue
                by_code.setdefault(item["stock_code"], item)
            has_ledger = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trading_trades'").fetchone())
            reviews, unmatched = [], 0
            for observation in evaluation.get("stock_results") or []:
                item = by_code.get(observation.get("code"))
                if not item:
                    unmatched += 1
                    continue
                execution = observation.get("execution_status", "unknown")
                if execution == "theoretical_trigger":
                    execution = "unknown"
                if execution in {"filled", "closed"}:
                    trade = conn.execute("SELECT code,action,executed_at FROM trading_trades WHERE trade_id=?", (observation.get("trade_id"),)).fetchone() if has_ledger else None
                    if not trade or trade["code"] != item["stock_code"] or trade["action"] != "buy" or timestamp(trade["executed_at"]) > as_of:
                        execution = "unknown"
                    else:
                        # A buy fill alone does not establish that it was closed.
                        execution = "filled"
                if execution not in {"unknown", "not_triggered", "rejected", "cancelled", "filled", "history_incomplete"}:
                    execution = "unknown"
                incomplete = item["history_incomplete"] or observation.get("status") == "history_incomplete"
                prediction = "history_incomplete" if incomplete else "pending"
                if not incomplete and (observation.get("status") in {"error", "data_error", "path_ambiguous"} or observation.get("date_mismatch")):
                    prediction = "inconclusive"
                review = self._append_review(conn, item, {"as_of": as_of,
                    "prediction_status": prediction, "execution_status": execution,
                    "net_pnl_status": "unavailable", "net_pnl_after_costs": None,
                    "observed_recommendation_return_pct": observation.get("return_pct"),
                    "return_basis": observation.get("return_basis", "unknown"),
                    "source_execution_status": observation.get("execution_status"),
                    "source_result_status": observation.get("status"),
                    "trade_ids": [observation["trade_id"]] if execution == "filled" else [],
                    "provenance": {"source_id": source_id, "evaluation_checksum": checksum,
                                   "recommendation_date": rec_date, "evaluation_date": evaluation.get("date")},
                    "limitations": ["日度标记不代表预测期限已结束。", "估计退出成本后的推荐收益率不等于账户已实现净盈亏。"]}, known_at)
                reviews.append(review["review_id"])
            summary = {"import_id": uuid.uuid4().hex, "source_id": source_id, "checksum": checksum,
                       "known_at": known_at, "imported": True, "review_ids": reviews,
                       "review_count": len(reviews), "unmatched_count": unmatched,
                       "warnings": ["没有可对应原判断的股票不会创建伪历史判断。"] if unmatched else []}
            conn.execute("INSERT INTO research_imports VALUES(?,?,?,?,?)", (summary["import_id"], source_id, checksum, known_at, _json(summary)))
            return summary

    def create_experiment(self, payload: dict) -> dict:
        from src.research.experiments import evaluate_experiment
        known_at = self._now()
        requested_cutoff = timestamp(payload["as_of"], end_of_day=True) if payload.get("as_of") else known_at
        effective_cutoff = min(requested_cutoff, known_at)
        result = {"experiment_id": uuid.uuid4().hex, "known_at": known_at,
                  "name": str(payload.get("name") or "unnamed"), "input": payload,
                  "input_checksum": hashlib.sha256(_json(payload).encode()).hexdigest(),
                  "evaluation_as_of": effective_cutoff,
                  "result": evaluate_experiment({**payload, "as_of": effective_cutoff}), "auto_promoted": False}
        result["status"] = result["result"]["status"]
        with self._connect() as conn:
            conn.execute("INSERT INTO research_experiments VALUES(?,?,?)", (result["experiment_id"], result["known_at"], _json(result)))
        return _view(result)

    def list_experiments(self, *, as_of=None, limit=100) -> list[dict]:
        with self._connect() as conn:
            return [self._payload(row) for row in conn.execute("SELECT payload_json FROM research_experiments WHERE known_at<=? ORDER BY known_at DESC,rowid DESC LIMIT ?", (self._cutoff(as_of), self._limit(limit)))]

    def get_summary(self, *, as_of=None) -> dict:
        cutoff = self._cutoff(as_of)
        result = {"as_of": cutoff, "auto_promotion_enabled": False}
        with self._connect() as conn:
            for name in ("judgments", "lessons", "experiments", "reviews", "evidence"):
                where = "known_at<=?"
                args = [cutoff]
                if name in {"judgments", "reviews", "evidence"}:
                    where += " AND as_of<=?"
                    args.append(cutoff)
                result[f"{name}_count"] = conn.execute(f"SELECT COUNT(*) FROM research_{name} WHERE {where}", args).fetchone()[0]
        return result
