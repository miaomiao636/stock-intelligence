# -*- coding: utf-8 -*-
"""Champion/Challenger策略版本注册与受控晋级。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

from src.storage.trading_ledger import TradingLedger


class StrategyRegistry:
    def __init__(self, data_dir: Path = None):
        self.ledger = TradingLedger(Path(data_dir or Path(__file__).parents[2] / "data"))

    def register(self, version_id: str, role: str, config: Dict) -> Dict:
        if role not in {"champion", "challenger"}:
            raise ValueError("新策略只能注册为champion或challenger")
        now = datetime.now().astimezone().isoformat()
        with self.ledger.transaction() as conn:
            if role == "champion" and conn.execute(
                "SELECT 1 FROM strategy_versions WHERE role='champion'"
            ).fetchone():
                raise ValueError("champion已存在")
            conn.execute(
                "INSERT INTO strategy_versions(version_id,role,config_json,created_at) VALUES(?,?,?,?)",
                (version_id, role, json.dumps(config, ensure_ascii=False, sort_keys=True), now),
            )
        return self.get(version_id)

    def get(self, version_id: str) -> Dict:
        with self.ledger.connect() as conn:
            row = conn.execute("SELECT * FROM strategy_versions WHERE version_id=?", (version_id,)).fetchone()
            if not row:
                raise ValueError("策略版本不存在")
            data = dict(row)
            data["config"] = json.loads(data.pop("config_json"))
            data["metrics"] = json.loads(data.pop("metrics_json")) if data.get("metrics_json") else None
            return data

    @staticmethod
    def promotion_eligibility(metrics: Dict) -> Dict:
        checks = {
            "closed_trades": int(metrics.get("closed_trades", 0)) >= 100,
            "shadow_days": int(metrics.get("shadow_days", 0)) >= 20,
            "net_expectancy": float(metrics.get("net_expectancy", 0)) > 0,
            "profit_factor": float(metrics.get("profit_factor", 0)) >= 1.2,
            "max_drawdown": float(metrics.get("max_drawdown_pct", 100)) <= 10,
            "outperforms_champion": bool(metrics.get("outperforms_champion", False)),
            "rule_violations": int(metrics.get("rule_violations", 1)) == 0,
        }
        return {"eligible": all(checks.values()), "checks": checks}

    def promote(self, version_id: str, metrics: Dict) -> Dict:
        candidate = self.get(version_id)
        if candidate["role"] != "challenger":
            raise ValueError("只有challenger可以晋级")
        eligibility = self.promotion_eligibility(metrics)
        if not eligibility["eligible"]:
            failed = [name for name, passed in eligibility["checks"].items() if not passed]
            raise ValueError("策略未达到晋级门槛: " + ", ".join(failed))
        now = datetime.now().astimezone().isoformat()
        with self.ledger.transaction() as conn:
            conn.execute("UPDATE strategy_versions SET role='archived' WHERE role='champion'")
            conn.execute(
                "UPDATE strategy_versions SET role='champion',metrics_json=?,promoted_at=? WHERE version_id=? AND role='challenger'",
                (json.dumps(metrics, ensure_ascii=False, sort_keys=True), now, version_id),
            )
        return self.get(version_id)

