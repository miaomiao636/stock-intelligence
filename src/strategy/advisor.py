# -*- coding: utf-8 -*-
"""策略建议器模块"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

import yaml


class StrategyAdvisor:
    """策略建议器"""
    
    def __init__(self):
        self.config_dir = Path(__file__).parent.parent.parent / "config"
        self.data_dir = Path(__file__).parent.parent.parent / "data"
    
    def generate_advisories(self, evaluation: Dict) -> List[Dict]:
        """根据评估结果生成策略调整建议"""
        
        advisories = []
        metrics = evaluation.get("metrics", {})
        stock_results = evaluation.get("stock_results", [])
        
        # 质量门槛：过滤错误样本
        valid_results = [
            r for r in stock_results
            if r.get("status") in {"hit", "stopped", "closed"}
        ]
        error_count = len(stock_results) - len(valid_results)
        
        # 如果有效样本数不足，不生成建议
        if len(valid_results) < 100:
            return advisories
        
        # 如果错误占比过高，不生成建议
        if len(stock_results) > 0 and error_count / len(stock_results) > 0.2:
            return advisories
        
        # 加载当前策略配置
        strategy = self._load_strategy()
        factor_weights = strategy.get("factor_weights", {})
        sector_allocations = strategy.get("sector_allocations", {})
        
        # 分析板块表现
        sector_performance = self._analyze_sector_performance(stock_results)
        
        # 生成板块配置建议
        sector_advisories = self._generate_sector_advisories(
            sector_performance, sector_allocations
        )
        advisories.extend(sector_advisories)
        
        # 生成因子权重建议
        factor_advisories = self._generate_factor_advisories(
            metrics, factor_weights
        )
        advisories.extend(factor_advisories)
        
        return advisories
    
    def _load_strategy(self) -> Dict:
        """加载策略配置"""
        strategy_file = self.config_dir / "strategy.yaml"
        if not strategy_file.exists():
            return {}
        
        with open(strategy_file) as f:
            return yaml.safe_load(f)
    
    def _analyze_sector_performance(self, stock_results: List[Dict]) -> Dict:
        """分析板块表现"""
        
        sector_stats = {}
        
        for stock in stock_results:
            sector = stock.get("sector", "未知")
            if sector not in sector_stats:
                sector_stats[sector] = {
                    "count": 0,
                    "hits": 0,
                    "stops": 0,
                    "total_return": 0,
                }
            
            sector_stats[sector]["count"] += 1
            sector_stats[sector]["total_return"] += stock.get("return_pct", 0)
            
            if stock.get("status") == "hit":
                sector_stats[sector]["hits"] += 1
            elif stock.get("status") == "stopped":
                sector_stats[sector]["stops"] += 1
        
        # 计算胜率
        for sector in sector_stats:
            stats = sector_stats[sector]
            stats["win_rate"] = (stats["hits"] / stats["count"] * 100) if stats["count"] > 0 else 0
            stats["avg_return"] = (stats["total_return"] / stats["count"]) if stats["count"] > 0 else 0
        
        return sector_stats
    
    def _generate_sector_advisories(
        self,
        sector_performance: Dict,
        sector_allocations: Dict
    ) -> List[Dict]:
        """生成板块配置建议"""
        
        advisories = []
        advisory_id = 1
        
        for sector, stats in sector_performance.items():
            # 检查target_name是否存在于策略配置
            if sector not in sector_allocations:
                continue  # 跳过不存在的板块
            
            current_allocation = sector_allocations.get(sector, {}).get("allocation", 0)
            win_rate = stats.get("win_rate", 0)
            avg_return = stats.get("avg_return", 0)
            
            # 胜率低于40%，建议降低配置
            if win_rate < 40 and stats["count"] >= 2:
                advisories.append({
                    "advisory_id": advisory_id,
                    "adjustment_type": "sector_allocation",
                    "target_name": sector,
                    "current_value": current_allocation,
                    "suggested_value": max(0.05, current_allocation - 0.03),
                    "reason": f"板块胜率仅{win_rate:.1f}%，低于40%阈值",
                    "evidence": [f"eval-{date.today().isoformat()}"],
                    "status": "pending",
                    "created_at": datetime.now().isoformat(),
                })
                advisory_id += 1
            
            # 胜率高于70%，建议提高配置
            elif win_rate > 70 and stats["count"] >= 2:
                advisories.append({
                    "advisory_id": advisory_id,
                    "adjustment_type": "sector_allocation",
                    "target_name": sector,
                    "current_value": current_allocation,
                    "suggested_value": min(0.25, current_allocation + 0.03),
                    "reason": f"板块胜率达{win_rate:.1f}%，高于70%阈值",
                    "evidence": [f"eval-{date.today().isoformat()}"],
                    "status": "pending",
                    "created_at": datetime.now().isoformat(),
                })
                advisory_id += 1
        
        return advisories
    
    def _generate_factor_advisories(
        self,
        metrics: Dict,
        factor_weights: Dict
    ) -> List[Dict]:
        """生成因子权重建议"""
        
        advisories = []
        advisory_id = 100  # 从100开始，避免与板块建议冲突
        
        # 如果整体胜率低于40%，建议调整news_sentiment权重
        win_rate = metrics.get("win_rate_pct", 0)
        if win_rate < 40:
            current_weight = factor_weights.get("news_sentiment", 0.10)
            advisories.append({
                "advisory_id": advisory_id,
                "adjustment_type": "factor_weight",
                "target_name": "news_sentiment",
                "current_value": current_weight,
                "suggested_value": max(0.05, current_weight - 0.02),
                "reason": f"整体胜率仅{win_rate:.1f}%，新闻情绪因子可能失效",
                "evidence": [f"eval-{date.today().isoformat()}"],
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            })
            advisory_id += 1
        
        return advisories
    
    def save_advisories(self, advisories: List[Dict]) -> Path:
        """保存策略建议"""
        date_str = date.today().isoformat()
        advisory_dir = self.data_dir / "strategy" / "advisories"
        advisory_dir.mkdir(parents=True, exist_ok=True)
        
        advisory_file = advisory_dir / f"{date_str}.json"
        
        with open(advisory_file, "w", encoding="utf-8") as f:
            json.dump(advisories, f, ensure_ascii=False, indent=2)
        
        return advisory_file
