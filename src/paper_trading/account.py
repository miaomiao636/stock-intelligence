# -*- coding: utf-8 -*-
"""模拟账户模块 - 原子读写 + 跨进程文件锁（P1-5）

原版直接 open+w 覆写，非原子且无锁，cron 与手动 API 并发会损坏 account.json。
本版引入 fcntl 文件锁 + 重读-修改-原子写（tempfile + os.replace），
保证读-改-写序列的原子性，杜绝丢失更新与文件损坏。
"""

import json
import os
import fcntl
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict


class PaperAccount:
    """模拟账户（并发安全）"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data" / "paper_trading"
        self.account_file = self.data_dir / "account.json"
        self._lock_file = self.data_dir / "account.lock"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._account = self._load_or_create()

    def _load_or_create(self) -> Dict:
        """加载或创建账户"""
        if self.account_file.exists():
            try:
                with open(self.account_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if data:  # 非空即合法
                        return data
            except (json.JSONDecodeError, IOError) as e:
                print(f"  [WARN] 账户文件损坏，重新创建: {e}")
        return self.init_account()

    def _atomic_write(self):
        """加锁 + 原子写（不重读），用于已知完整状态写入"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                self._account["updated_at"] = datetime.now().isoformat()
                temp_file = self.account_file.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self._account, f, ensure_ascii=False, indent=2)
                os.replace(str(temp_file), str(self.account_file))
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

    @contextmanager
    def _locked(self):
        """跨进程文件锁 + 重读-修改-原子写，保证读-改-写原子性（P1-5）

        进入时加锁并重读最新账户（避免基于过期内存快照修改），
        yield 给调用方修改 self._account，退出时原子写回。
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                # 重读最新状态，避免丢失更新
                if self.account_file.exists():
                    try:
                        with open(self.account_file, encoding="utf-8") as f:
                            data = json.load(f)
                            if data:
                                self._account = data
                    except (json.JSONDecodeError, IOError):
                        pass
                yield
                # 原子写
                self._account["updated_at"] = datetime.now().isoformat()
                temp_file = self.account_file.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self._account, f, ensure_ascii=False, indent=2)
                os.replace(str(temp_file), str(self.account_file))
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

    def init_account(self, initial_cash: float = None) -> Dict:
        if initial_cash is None:
            from src.strategy.position_limits import get_initial_cash
            initial_cash = get_initial_cash()  # B7: 从 strategy.yaml 读取
        """初始化账户"""
        self._account = {
            "account_id": "default",
            "initial_cash": initial_cash,
            "cash": initial_cash,
            "market_value": 0,
            "total_equity": initial_cash,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "max_position_pct": 0.15,
            "max_sector_pct": 0.35,
            "cash_reserve_pct": 0.20,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self._atomic_write()
        return self._account

    def get_account(self) -> Dict:
        """获取账户信息"""
        return self._account

    def update_equity(self, market_value: float, unrealized_pnl: float):
        """更新账户权益"""
        with self._locked():
            self._account["market_value"] = market_value
            self._account["unrealized_pnl"] = unrealized_pnl
            self._account["total_equity"] = self._account["cash"] + market_value

    def deduct_cash(self, amount: float) -> bool:
        """扣除现金（读-改-写原子）"""
        if amount <= 0:
            return False
        with self._locked():
            if self._account["cash"] < amount:
                return False
            self._account["cash"] -= amount
            self._account["total_equity"] = self._account["cash"] + self._account.get("market_value", 0)
            return True

    def add_cash(self, amount: float):
        """增加现金（读-改-写原子）"""
        if amount <= 0:
            return
        with self._locked():
            self._account["cash"] += amount
            self._account["total_equity"] = self._account["cash"] + self._account.get("market_value", 0)
