# -*- coding: utf-8 -*-
"""模拟交易的 SQLite 唯一事实源。"""

from __future__ import annotations

import sqlite3
import uuid
import math
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional


class TradingLedger:
    """账户、订单、成交、批次持仓和权益快照的事务账本。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "stock_intelligence.db"
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trading_accounts (
                    account_id TEXT PRIMARY KEY,
                    initial_cash REAL NOT NULL CHECK(initial_cash > 0),
                    cash REAL NOT NULL CHECK(cash >= 0),
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_orders (
                    order_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    run_id TEXT NOT NULL,
                    recommendation_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sector TEXT,
                    action TEXT NOT NULL CHECK(action IN ('buy','sell')),
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    planned_price REAL NOT NULL CHECK(planned_price > 0),
                    min_price REAL,
                    max_price REAL,
                    stop_price REAL,
                    target_price REAL,
                    instrument_type TEXT NOT NULL DEFAULT 'stock',
                    horizon TEXT NOT NULL DEFAULT 'short',
                    reason TEXT,
                    status TEXT NOT NULL,
                    notified_at TEXT,
                    veto_deadline TEXT,
                    decision TEXT,
                    decision_actor TEXT,
                    decided_at TEXT,
                    reject_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_decisions (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES trading_orders(order_id),
                    action TEXT NOT NULL,
                    actor TEXT,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_trades (
                    trade_id TEXT PRIMARY KEY,
                    order_id TEXT UNIQUE NOT NULL REFERENCES trading_orders(order_id),
                    code TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    fees REAL NOT NULL,
                    slippage REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL,
                    executed_at TEXT NOT NULL,
                    trade_date TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS position_lots (
                    lot_id TEXT PRIMARY KEY,
                    buy_trade_id TEXT UNIQUE NOT NULL REFERENCES trading_trades(trade_id),
                    recommendation_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sector TEXT,
                    instrument_type TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    acquired_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity >= 0),
                    avg_cost REAL NOT NULL,
                    current_price REAL NOT NULL,
                    highest_price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT UNIQUE NOT NULL,
                    total_equity REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cash_adjustments (
                    adjustment_id TEXT PRIMARY KEY,
                    amount REAL NOT NULL,
                    previous_cash REAL NOT NULL,
                    new_cash REAL NOT NULL CHECK(new_cash >= 0),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_versions (
                    version_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('champion','challenger','archived')),
                    config_json TEXT NOT NULL,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL,
                    promoted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_orders_status ON trading_orders(status);
                CREATE INDEX IF NOT EXISTS idx_lots_code ON position_lots(code);
                CREATE INDEX IF NOT EXISTS idx_trades_date ON trading_trades(trade_date);
                CREATE INDEX IF NOT EXISTS idx_cash_adjustments_created_at ON cash_adjustments(created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_single_champion ON strategy_versions(role) WHERE role='champion';
                """
            )

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict]:
        return dict(row) if row else None

    def initialize_account(self, initial_cash: float, reset: bool = False) -> Dict:
        if initial_cash <= 0:
            raise ValueError("初始资金必须大于0")
        now = datetime.now().astimezone().isoformat()
        with self.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM trading_accounts WHERE account_id='default'"
            ).fetchone()
            if exists and not reset:
                return self.get_account(conn)
            if reset:
                for table in ("trading_decisions", "position_lots", "trading_trades", "trading_orders", "equity_snapshots", "cash_adjustments"):
                    conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "INSERT OR REPLACE INTO trading_accounts(account_id,initial_cash,cash,realized_pnl,created_at,updated_at) VALUES('default',?,?,?,?,?)",
                (initial_cash, initial_cash, 0.0, now, now),
            )
        return self.get_account()

    def get_account(self, conn: sqlite3.Connection = None) -> Dict:
        own = conn is None
        conn = conn or self.connect()
        try:
            row = conn.execute("SELECT * FROM trading_accounts WHERE account_id='default'").fetchone()
            if not row:
                raise RuntimeError("模拟账户尚未初始化")
            market_value, cost_value = conn.execute(
                "SELECT COALESCE(SUM(quantity * current_price),0),"
                "COALESCE(SUM(quantity * avg_cost),0) FROM position_lots WHERE quantity>0"
            ).fetchone()
            data = dict(row)
            data["market_value"] = round(float(market_value), 2)
            data["total_equity"] = round(data["cash"] + data["market_value"], 2)
            data["unrealized_pnl"] = round(float(market_value) - float(cost_value), 2)
            net_cash_adjustment = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM cash_adjustments"
            ).fetchone()[0]
            data["net_cash_adjustment"] = round(float(net_cash_adjustment), 2)
            data["adjusted_principal"] = round(
                float(data["initial_cash"]) + data["net_cash_adjustment"], 2
            )
            data["net_pnl_after_costs"] = round(
                data["total_equity"] - data["adjusted_principal"], 2
            )
            return data
        finally:
            if own:
                conn.close()

    def update_available_cash(self, cash: float) -> Dict:
        """调整可用现金，并以外部资金流记录保证收益率不被充值/取现污染。"""
        cash = float(cash)
        if not math.isfinite(cash) or cash <= 0:
            raise ValueError("可用现金必须大于0")
        cash = round(cash, 2)
        now = datetime.now().astimezone().isoformat()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT cash FROM trading_accounts WHERE account_id='default'"
            ).fetchone()
            if not row:
                raise RuntimeError("模拟账户尚未初始化")
            previous_cash = round(float(row["cash"]), 2)
            amount = round(cash - previous_cash, 2)
            if amount:
                conn.execute(
                    "INSERT INTO cash_adjustments(adjustment_id,amount,previous_cash,new_cash,created_at) VALUES(?,?,?,?,?)",
                    (f"CASH-{uuid.uuid4().hex}", amount, previous_cash, cash, now),
                )
            conn.execute(
                "UPDATE trading_accounts SET cash=?, updated_at=? WHERE account_id='default'",
                (cash, now),
            )
        return self.get_account()

    def cash_adjustments(self) -> List[Dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT amount, created_at FROM cash_adjustments ORDER BY created_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def create_order(self, payload: Dict, now: datetime) -> Dict:
        order_id = payload.get("order_id") or f"ORD-{now:%Y%m%d}-{uuid.uuid4().hex[:12]}"
        idem = payload.get("idempotency_key") or ":".join(
            [payload["run_id"], payload["recommendation_id"], payload["action"], payload["code"]]
        )
        values = {
            **payload,
            "order_id": order_id,
            "idempotency_key": idem,
            "status": "proposed",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        columns = [
            "order_id", "idempotency_key", "run_id", "recommendation_id", "code", "name",
            "sector", "action", "quantity", "planned_price", "min_price", "max_price",
            "stop_price", "target_price", "instrument_type", "horizon", "reason", "status",
            "created_at", "updated_at",
        ]
        try:
            with self.transaction() as conn:
                conn.execute(
                    f"INSERT INTO trading_orders({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    [values.get(c) for c in columns],
                )
        except sqlite3.IntegrityError as exc:
            existing = self.get_order_by_idempotency(idem)
            if existing:
                return existing
            raise ValueError(f"订单创建失败: {exc}") from exc
        return self.get_order(order_id)

    def get_order(self, order_id: str, conn: sqlite3.Connection = None) -> Optional[Dict]:
        own = conn is None
        conn = conn or self.connect()
        try:
            return self._row(conn.execute("SELECT * FROM trading_orders WHERE order_id=?", (order_id,)).fetchone())
        finally:
            if own:
                conn.close()

    def get_order_by_idempotency(self, key: str) -> Optional[Dict]:
        with self.connect() as conn:
            return self._row(conn.execute("SELECT * FROM trading_orders WHERE idempotency_key=?", (key,)).fetchone())

    def list_orders(self, statuses: List[str] = None, trade_date: str = None) -> List[Dict]:
        clauses, params = [], []
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if trade_date:
            clauses.append("substr(created_at,1,10)=?")
            params.append(trade_date)
        sql = "SELECT * FROM trading_orders"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at,order_id"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def update_order(self, order_id: str, status: str, now: datetime, **fields) -> Dict:
        fields = {**fields, "status": status, "updated_at": now.isoformat()}
        with self.transaction() as conn:
            if not self.get_order(order_id, conn):
                raise ValueError("订单不存在")
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(
                f"UPDATE trading_orders SET {assignments} WHERE order_id=?",
                [*fields.values(), order_id],
            )
        return self.get_order(order_id)

    def claim_order(self, order_id: str, allowed_status: str, now: datetime) -> bool:
        """以compare-and-set方式取得撮合权，防止cron与回调重复成交。"""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE trading_orders SET status='revalidating',updated_at=? WHERE order_id=? AND status=?",
                (now.isoformat(), order_id, allowed_status),
            )
            return cursor.rowcount == 1

    def pause_day(self, trade_date: str, now: datetime, actor: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE trading_orders SET status='paused_for_day',decision='pause_day',"
                "decision_actor=?,decided_at=?,updated_at=? "
                "WHERE substr(created_at,1,10)=? AND status IN ('proposed','pre_notified','final_notified','confirmed')",
                (actor, now.isoformat(), now.isoformat(), trade_date),
            )

    def is_day_paused(self, trade_date: str) -> bool:
        with self.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM trading_orders WHERE substr(created_at,1,10)=? AND status='paused_for_day' LIMIT 1",
                (trade_date,),
            ).fetchone() is not None

    def get_trade_by_order(self, order_id: str) -> Optional[Dict]:
        with self.connect() as conn:
            return self._row(conn.execute("SELECT * FROM trading_trades WHERE order_id=?", (order_id,)).fetchone())

    def list_trades(self, trade_date: str = None, limit: int = 100) -> List[Dict]:
        sql = "SELECT * FROM trading_trades"
        params = []
        if trade_date:
            sql += " WHERE trade_date=?"
            params.append(trade_date)
        sql += " ORDER BY executed_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def record_decision(self, order_id: str, action: str, actor: str, now: datetime, event_id: str) -> bool:
        with self.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO trading_decisions(event_id,order_id,action,actor,received_at) VALUES(?,?,?,?,?)",
                    (event_id, order_id, action, actor, now.isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def positions(self, as_of_date: str) -> List[Dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT code,MAX(name) name,MAX(sector) sector,MAX(instrument_type) instrument_type,
                       MAX(horizon) horizon,SUM(quantity) quantity,
                       SUM(quantity*avg_cost)/SUM(quantity) avg_cost,MAX(current_price) current_price,
                       SUM(quantity*current_price) market_value,
                       SUM(CASE WHEN instrument_type='stock' AND acquired_date>=? THEN 0 ELSE quantity END) available_quantity,
                       MAX(recommendation_id) recommendation_id,MIN(acquired_date) entry_date,MAX(highest_price) highest_price
                FROM position_lots WHERE quantity>0 GROUP BY code ORDER BY code
                """,
                (as_of_date,),
            ).fetchall()
            return [dict(row) for row in rows]

    def snapshots(self) -> List[Dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM equity_snapshots ORDER BY captured_at")]

    def update_market_prices(self, prices: Dict[str, float], now: datetime) -> None:
        with self.transaction() as conn:
            for code, price in prices.items():
                conn.execute(
                    "UPDATE position_lots SET current_price=?,highest_price=MAX(highest_price,?),updated_at=? WHERE code=? AND quantity>0",
                    (price, price, now.isoformat(), code),
                )
            if prices:
                conn.execute(
                    "UPDATE trading_accounts SET updated_at=? WHERE account_id='default'",
                    (now.isoformat(),),
                )

    def risk_levels(self, recommendation_id: str) -> Dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT stop_price,target_price FROM trading_orders WHERE recommendation_id=? AND action='buy' AND status='filled' ORDER BY updated_at DESC LIMIT 1",
                (recommendation_id,),
            ).fetchone()
            return dict(row) if row else {}
