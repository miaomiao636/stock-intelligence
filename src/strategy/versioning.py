# -*- coding: utf-8 -*-
"""策略版本管理模块"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import yaml


class StrategyVersioning:
    """策略版本管理"""
    
    def __init__(self):
        self.config_dir = Path(__file__).parent.parent.parent / "config"
        self.data_dir = Path(__file__).parent.parent.parent / "data" / "strategy"
    
    def save_version(self, reason: str = "manual") -> Path:
        """保存当前策略版本"""
        strategy_file = self.config_dir / "strategy.yaml"
        if not strategy_file.exists():
            raise FileNotFoundError("strategy.yaml不存在")
        
        # 读取当前配置
        with open(strategy_file) as f:
            strategy = yaml.safe_load(f)
        
        # 生成版本号
        version = datetime.now().strftime("%Y%m%d%H%M%S")
        
        # 保存到历史目录
        history_dir = self.data_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        
        version_file = history_dir / f"strategy_{version}.json"
        
        version_data = {
            "version": version,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "config": strategy,
        }
        
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(version_data, f, ensure_ascii=False, indent=2)
        
        return version_file
    
    def list_versions(self) -> list:
        """列出所有版本"""
        history_dir = self.data_dir / "history"
        if not history_dir.exists():
            return []
        
        versions = []
        for f in sorted(history_dir.glob("strategy_*.json"), reverse=True):
            with open(f) as fp:
                data = json.load(fp)
                versions.append({
                    "version": data.get("version"),
                    "timestamp": data.get("timestamp"),
                    "reason": data.get("reason"),
                    "file": str(f),
                })
        
        return versions
    
    def rollback(self, version: str) -> bool:
        """回滚到指定版本"""
        history_dir = self.data_dir / "history"
        version_file = history_dir / f"strategy_{version}.json"
        
        if not version_file.exists():
            return False
        
        # 读取版本配置
        with open(version_file) as f:
            version_data = json.load(f)
        
        # 先保存当前版本（以便恢复）
        self.save_version(reason=f"rollback_to_{version}")
        
        # 写入回滚配置
        strategy_file = self.config_dir / "strategy.yaml"
        with open(strategy_file, "w") as f:
            yaml.dump(version_data["config"], f, allow_unicode=True, default_flow_style=False)
        
        return True
    
    def apply_advisory(self, advisory: Dict) -> Dict:
        """应用单条策略建议，返回结果"""
        
        adj_type = advisory.get("adjustment_type")
        target = advisory.get("target_name")
        new_value = advisory.get("suggested_value")
        
        # 读取当前配置
        strategy_file = self.config_dir / "strategy.yaml"
        with open(strategy_file) as f:
            strategy = yaml.safe_load(f)
        
        # 验证target是否存在
        if adj_type == "sector_allocation":
            if target not in strategy.get("sector_allocations", {}):
                return {"success": False, "reason": f"板块'{target}'不存在于策略配置"}
        elif adj_type == "factor_weight":
            if target not in strategy.get("factor_weights", {}):
                return {"success": False, "reason": f"因子'{target}'不存在于策略配置"}
        else:
            return {"success": False, "reason": f"未知的调整类型: {adj_type}"}
        
        # 验证suggested_value范围
        if not (0 <= new_value <= 1):
            return {"success": False, "reason": f"建议值{new_value}不在0-1范围内"}
        
        # 在内存中复制策略并应用修改
        import copy
        new_strategy = copy.deepcopy(strategy)
        
        if adj_type == "sector_allocation":
            new_strategy["sector_allocations"][target]["allocation"] = new_value
        elif adj_type == "factor_weight":
            new_strategy["factor_weights"][target] = new_value
        
        # 校验权重是否合法
        factor_weights = new_strategy.get("factor_weights", {})
        sector_allocations = new_strategy.get("sector_allocations", {})
        
        if abs(sum(factor_weights.values()) - 1.0) > 0.01:
            return {"success": False, "reason": "因子权重总和不为1.0"}
        
        if abs(sum(s["allocation"] for s in sector_allocations.values()) - 1.0) > 0.01:
            return {"success": False, "reason": "板块配置总和不为1.0"}
        
        # 校验通过后，先保存版本
        self.save_version(reason=f"apply_advisory_{advisory.get('advisory_id')}")
        
        # 写入配置
        with open(strategy_file, "w") as f:
            yaml.dump(new_strategy, f, allow_unicode=True, default_flow_style=False)
        
        return {"success": True, "reason": "建议已应用"}
