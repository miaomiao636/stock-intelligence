# -*- coding: utf-8 -*-
"""模拟持仓模块 - 支持跨日持仓延续"""

import json
import shutil
import fcntl
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional


class PaperPortfolio:
    """模拟持仓

    存储结构：
    - current.json: 当前持仓（唯一状态源）
    - snapshots/YYYY-MM-DD.json: 每日快照（只读归档）
    """

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data" / "paper_trading"
        self.positions_dir = self.data_dir / "positions"
        self.snapshots_dir = self.positions_dir / "snapshots"
        self.current_file = self.positions_dir / "current.json"
        self._lock_file = self.positions_dir / "positions.lock"  # P1-5: 跨进程文件锁

        # 创建目录
        self.positions_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        # 迁移旧数据（如果存在）
        self._migrate_old_data()

    def _migrate_old_data(self):
        """迁移旧的按日期存储的持仓数据到新格式"""
        # 查找最新的日期文件
        date_files = sorted(self.positions_dir.glob("????-??-??.json"), reverse=True)

        if date_files and not self.current_file.exists():
            # 找到最新的日期文件，迁移为current.json
            latest_file = date_files[0]
            print(f"📦 迁移持仓数据: {latest_file.name} -> current.json")

            # 复制为current.json
            shutil.copy2(latest_file, self.current_file)

            # 移动旧文件到snapshots目录
            for date_file in date_files:
                snapshot_file = self.snapshots_dir / date_file.name
                if not snapshot_file.exists():
                    shutil.move(str(date_file), str(snapshot_file))

            print(f"✅ 迁移完成，共迁移 {len(date_files)} 个快照")

    @contextmanager
    def _locked(self):
        """跨进程文件锁，保证读-改-写原子性（P1-5）"""
        self.positions_dir.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

    def get_positions(self, date_str: str = None) -> List[Dict]:
        """获取持仓

        Args:
            date_str: 日期字符串，格式YYYY-MM-DD
                     - None或今天：返回当前持仓
                     - 历史日期：返回该日快照

        Returns:
            持仓列表
        """
        today_str = date.today().isoformat()

        # 查询当前持仓
        if date_str is None or date_str == today_str:
            return self._read_json(self.current_file)

        # 查询历史快照
        from src.utils.security import validate_date_str
        validate_date_str(date_str, "date_str")  # P0-4: 防路径穿越
        snapshot_file = self.snapshots_dir / f"{date_str}.json"
        return self._read_json(snapshot_file)

    def save_positions(self, positions: List[Dict], date_str: str = None):
        """保存持仓

        Args:
            positions: 持仓列表
            date_str: 日期字符串
                     - None或今天：更新当前持仓 + 保存快照
                     - 历史日期：只保存快照，不更新当前持仓
        """
        today_str = date.today().isoformat()
        snapshot_date = date_str or today_str

        # 保存每日快照（先写快照，再写当前持仓）
        # 如果快照写入失败，current.json不会被更新，保证一致性
        snapshot_file = self.snapshots_dir / f"{snapshot_date}.json"
        self._write_json(snapshot_file, positions)

        # 只有当日期是今天时才更新当前持仓
        if snapshot_date == today_str:
            self._write_json(self.current_file, positions)

    def add_position(self, position: Dict, date_str: str = None):
        """添加持仓

        Args:
            position: 持仓信息
            date_str: 日期字符串
        """
        with self._locked():  # P1-5: 读-改-写原子
            positions = self.get_positions(date_str)
            positions.append(position)
            self.save_positions(positions, date_str)

    def update_position(self, code: str, updates: Dict, date_str: str = None) -> bool:
        """更新持仓

        Args:
            code: 股票代码
            updates: 更新字段
            date_str: 日期字符串

        Returns:
            是否更新成功
        """
        with self._locked():  # P1-5: 读-改-写原子
            positions = self.get_positions(date_str)
            updated = False

            for pos in positions:
                if pos.get("code") == code:
                    pos.update(updates)
                    updated = True
                    break

            if updated:
                self.save_positions(positions, date_str)

            return updated

    def remove_position(self, code: str, date_str: str = None):
        """移除持仓

        Args:
            code: 股票代码
            date_str: 日期字符串
        """
        with self._locked():  # P1-5: 读-改-写原子
            positions = self.get_positions(date_str)
            positions = [p for p in positions if p.get("code") != code]
            self.save_positions(positions, date_str)

    def get_position(self, code: str, date_str: str = None) -> Optional[Dict]:
        """获取单个持仓

        Args:
            code: 股票代码
            date_str: 日期字符串

        Returns:
            持仓信息或None
        """
        positions = self.get_positions(date_str)
        for pos in positions:
            if pos.get("code") == code:
                return pos
        return None

    def get_historical_snapshots(self, days: int = 30) -> List[Dict]:
        """获取历史快照列表

        Args:
            days: 获取最近多少天的快照

        Returns:
            快照列表，每项包含date和positions
        """
        snapshots = []
        snapshot_files = sorted(self.snapshots_dir.glob("????-??-??.json"), reverse=True)

        for snapshot_file in snapshot_files[:days]:
            snapshot_date = snapshot_file.stem
            positions = self._read_json(snapshot_file)
            snapshots.append({
                "date": snapshot_date,
                "positions": positions,
                "count": len(positions)
            })

        return snapshots

    def _read_json(self, file_path: Path) -> List[Dict]:
        """读取JSON文件"""
        if not file_path.exists():
            return []

        try:
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 读取持仓文件失败: {file_path}, 错误: {e}")
            return []

    def _write_json(self, file_path: Path, data: List[Dict]):
        """写入JSON文件（原子操作）"""
        import tempfile
        import os

        try:
            # 先写入临时文件
            temp_file = file_path.with_suffix('.tmp')
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 原子替换原文件
            os.replace(str(temp_file), str(file_path))
        except IOError as e:
            print(f"⚠️ 写入持仓文件失败: {file_path}, 错误: {e}")
            # 清理临时文件
            temp_file = file_path.with_suffix('.tmp')
            if temp_file.exists():
                temp_file.unlink()
            raise
