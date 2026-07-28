#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只迁移模拟交易账本，不覆盖报告、新闻和推荐数据。"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List


DELETE_ORDER = (
    "trading_decisions",
    "position_lots",
    "trading_trades",
    "trading_orders",
    "equity_snapshots",
    "cash_adjustments",
    "trading_accounts",
)
INSERT_ORDER = (
    "trading_accounts",
    "cash_adjustments",
    "trading_orders",
    "trading_decisions",
    "trading_trades",
    "position_lots",
    "equity_snapshots",
)


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _validate_schema(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    for table in INSERT_ORDER:
        source_columns = _columns(source, table)
        target_columns = _columns(target, table)
        if not source_columns:
            raise ValueError(f"源数据库缺少表: {table}")
        if source_columns != target_columns:
            raise ValueError(f"表结构不一致，禁止迁移: {table}")
    accounts = [
        row[0]
        for row in source.execute(
            "SELECT account_id FROM trading_accounts ORDER BY account_id"
        ).fetchall()
    ]
    if accounts != ["default"]:
        raise ValueError("源数据库必须且只能包含 default 模拟账户")


def _table_counts(conn: sqlite3.Connection, tables: Iterable[str]) -> Dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def import_trading_ledger(
    source_path: Path,
    target_path: Path,
    *,
    apply: bool = False,
    backup_dir: Path | None = None,
) -> Dict:
    source_path = Path(source_path).expanduser().resolve()
    target_path = Path(target_path).expanduser().resolve()
    if source_path == target_path:
        raise ValueError("源数据库和目标数据库不能是同一个文件")
    if not source_path.is_file():
        raise FileNotFoundError(f"源数据库不存在: {source_path}")
    if not target_path.is_file():
        raise FileNotFoundError(f"目标数据库不存在: {target_path}")

    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
        source.row_factory = sqlite3.Row
        target.row_factory = sqlite3.Row
        _validate_schema(source, target)
        counts = _table_counts(source, INSERT_ORDER)
        account = dict(source.execute(
            "SELECT initial_cash,cash,realized_pnl FROM trading_accounts WHERE account_id='default'"
        ).fetchone())
        positions = int(source.execute(
            "SELECT COUNT(*) FROM position_lots WHERE quantity>0"
        ).fetchone()[0])
        summary = {
            "source": str(source_path),
            "target": str(target_path),
            "account": account,
            "active_position_lots": positions,
            "table_counts": counts,
            "applied": False,
            "backup": None,
        }
        if not apply:
            return summary

        backup_dir = Path(backup_dir or target_path.parent / "backups").expanduser().resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{target_path.stem}-before-ledger-import-{timestamp}.db"
        shutil.copy2(target_path, backup_path)

        target.execute("PRAGMA foreign_keys=OFF")
        try:
            target.execute("BEGIN IMMEDIATE")
            for table in DELETE_ORDER:
                target.execute(f"DELETE FROM {table}")
            for table in INSERT_ORDER:
                columns = _columns(source, table)
                column_sql = ",".join(columns)
                placeholders = ",".join("?" for _ in columns)
                rows = source.execute(f"SELECT {column_sql} FROM {table}").fetchall()
                if rows:
                    target.executemany(
                        f"INSERT INTO {table}({column_sql}) VALUES({placeholders})",
                        [tuple(row[column] for column in columns) for row in rows],
                    )
            violations = target.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ValueError(f"迁移后外键校验失败: {violations[:3]}")
            target.commit()
        except Exception:
            target.rollback()
            raise
        finally:
            target.execute("PRAGMA foreign_keys=ON")

        summary["applied"] = True
        summary["backup"] = str(backup_path)
        return summary


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="包含本地交易账本的 SQLite 数据库")
    parser.add_argument(
        "--target",
        type=Path,
        default=project_root / "data" / "stock_intelligence.db",
        help="云端目标数据库",
    )
    parser.add_argument("--backup-dir", type=Path, default=Path("/opt/stock-intelligence-backups"))
    parser.add_argument("--apply", action="store_true", help="实际迁移；不加时只做预检")
    args = parser.parse_args()
    result = import_trading_ledger(
        args.source,
        args.target,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    print("交易账本迁移结果")
    print(f"  模式: {'已执行' if result['applied'] else '仅预检'}")
    print(f"  源: {result['source']}")
    print(f"  目标: {result['target']}")
    print(f"  账户: {result['account']}")
    print(f"  活跃持仓批次: {result['active_position_lots']}")
    print(f"  表记录数: {result['table_counts']}")
    if result["backup"]:
        print(f"  云端备份: {result['backup']}")


if __name__ == "__main__":
    main()
