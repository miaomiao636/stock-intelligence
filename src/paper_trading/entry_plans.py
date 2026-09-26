"""Same-ledger entry plans: a durable diagnostic queue, never a second account.

Schema is created only on the first write. Reading an old deployment with no
entry-plan table returns an empty list and does not manufacture historical plans.
"""

import hashlib
import json
from collections import Counter
from contextlib import closing


PENDING = {"waiting_trigger", "waiting_quote", "ready"}
TERMINAL = {"filled", "rejected", "expired"}


class EntryPlanStore:
    def __init__(self, ledger):
        self.ledger = ledger

    @staticmethod
    def _schema(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS paper_entry_plans (
            plan_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, run_id TEXT NOT NULL,
            recommendation_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
            stock_json TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_checked_at TEXT NOT NULL,
            check_count INTEGER NOT NULL DEFAULT 0, order_id TEXT,
            UNIQUE(trade_date,run_id,recommendation_id,code))""")

    def register(self, stock, *, run_id, trade_date, now, source_status=None):
        recommendation_id = stock.get("recommendation_id") or f"REC-{run_id}-{stock.get('code', '')}"
        identity = f"{trade_date}:{run_id}:{recommendation_id}:{stock.get('code', '')}"
        plan_id = "PLAN-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        snapshot = dict(stock, recommendation_id=recommendation_id, _entry_source_status=source_status or {})
        with self.ledger.transaction() as conn:
            self._schema(conn)
            conn.execute("""UPDATE paper_entry_plans SET status='expired',reason='同日新版研究计划已取代该计划',
                last_checked_at=? WHERE trade_date=? AND code=? AND plan_id<>?
                AND status IN ('ready','waiting_trigger','waiting_quote')""",
                (now.isoformat(), trade_date, stock.get("code") or "", plan_id))
            conn.execute("""INSERT OR IGNORE INTO paper_entry_plans
                (plan_id,trade_date,run_id,recommendation_id,code,name,stock_json,status,reason,
                 created_at,expires_at,last_checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (plan_id, trade_date, run_id, recommendation_id, stock.get("code") or "",
                 stock.get("name") or stock.get("code") or "", json.dumps(snapshot, ensure_ascii=False),
                 "ready", "等待首次风控与价格检查", now.isoformat(),
                 f"{trade_date}T15:00:00+08:00", now.isoformat()))
            row = conn.execute("SELECT * FROM paper_entry_plans WHERE plan_id=?", (plan_id,)).fetchone()
        return self._decode(row)

    def update(self, plan_id, status, reason, now, order_id=None):
        with self.ledger.transaction() as conn:
            conn.execute("""UPDATE paper_entry_plans SET status=?,reason=?,last_checked_at=?,
                check_count=check_count+1,order_id=COALESCE(?,order_id)
                WHERE plan_id=? AND status NOT IN ('filled','rejected','expired')""",
                (status, reason or "", now.isoformat(), order_id, plan_id))

    def reconcile_fills(self, now):
        """Recover a crash between atomic ledger fill and diagnostic-plan update."""
        if not self.list_plans(limit=1):
            return
        with self.ledger.transaction() as conn:
            match = """SELECT o.order_id FROM trading_orders o JOIN trading_trades t ON t.order_id=o.order_id
                WHERE o.status='filled' AND o.action='buy' AND o.code=paper_entry_plans.code
                AND o.run_id=paper_entry_plans.run_id AND o.recommendation_id=paper_entry_plans.recommendation_id
                AND t.trade_date=paper_entry_plans.trade_date"""
            conn.execute(f"""UPDATE paper_entry_plans SET status='filled',order_id=({match}),
                reason='根据已成交账本恢复计划状态',last_checked_at=?
                WHERE status<>'filled' AND EXISTS ({match})""", (now.isoformat(),))

    @staticmethod
    def _decode(row):
        value = dict(row)
        value["stock"] = json.loads(value.pop("stock_json"))
        return value

    def list_plans(self, trade_date=None, limit=100, statuses=None):
        with closing(self.ledger.connect()) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_entry_plans'").fetchone()
            if not exists:
                return []
            clauses, params = [], []
            if trade_date:
                clauses.append("trade_date=?")
                params.append(trade_date)
            if statuses:
                clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
                params.extend(statuses)
            query = "SELECT * FROM paper_entry_plans"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at DESC,plan_id"
            if limit is not None:
                query += " LIMIT ?"
                params.append(max(0, int(limit)))
            return [self._decode(row) for row in conn.execute(query, params)]

    def get_summary(self, trade_date=None):
        plans = self.list_plans(trade_date=trade_date, limit=None)
        counts = Counter(plan["status"] for plan in plans)
        return {"trade_date": trade_date, "total": len(plans), "by_status": dict(counts),
                "waiting": sum(counts[state] for state in PENDING), "filled": counts["filled"],
                "rejected": counts["rejected"], "expired": counts["expired"],
                "reason_counts": dict(Counter(plan["reason"] for plan in plans if plan["reason"]))}
