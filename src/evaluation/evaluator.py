# -*- coding: utf-8 -*-
"""评估器模块"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

from src.data_collectors.market_data import get_stock_data, get_index_data
from src.evaluation.metrics import calculate_return, calculate_metrics
from src.evaluation.leak_guard import validate_no_future_data


class Evaluator:
    """评估器"""
    
    def __init__(self):
        self.data_dir = Path(__file__).parent.parent.parent / "data"
    
    def evaluate_recommendation(
        self,
        recommendation: Dict,
        target_date: Optional[str] = None
    ) -> Dict:
        """评估单条推荐"""
        
        if target_date is None:
            target_date = date.today().isoformat()
        
        stock_results = []
        
        for stock in recommendation.get("stock_recommendations", []):
            result = self._evaluate_stock(stock, target_date)
            stock_results.append(result)
        
        # 计算质量状态
        total = len(stock_results)
        valid_results = [r for r in stock_results if r.get("status") != "error"]
        error_results = [r for r in stock_results if r.get("status") == "error"]
        valid_count = len(valid_results)
        error_count = len(error_results)
        error_rate = error_count / total if total > 0 else 0
        
        # 确定评估状态
        if valid_count < 3:
            eval_status = "error"
        elif error_rate > 0.2:
            eval_status = "degraded"
        else:
            eval_status = "success"
        
        # 计算基准收益（使用沪深300指数）
        benchmark_data = get_index_data("000300")  # 沪深300指数
        benchmark_return = benchmark_data.get("change_pct", 0)
        
        # 计算综合指标（只使用有效样本）
        metrics = calculate_metrics(valid_results, benchmark_return)
        
        return {
            "date": target_date,
            "recommendation_date": recommendation.get("date"),
            "recommendation_type": recommendation.get("type"),
            "status": eval_status,
            "quality": {
                "total": total,
                "valid": valid_count,
                "error": error_count,
                "error_rate": round(error_rate, 2),
                "min_valid_required": 3,
            },
            "stock_results": stock_results,
            "metrics": metrics,
            "benchmark_return_pct": benchmark_return,
            "evaluated_at": datetime.now().isoformat(),
        }
    
    def _evaluate_stock(self, stock: Dict, target_date: str) -> Dict:
        """评估单只股票

        注意：当前实现使用最新数据，而非target_date的历史数据
        这是一个已知限制，将在后续版本中修复

        TODO: 实现按target_date获取历史OHLC数据
        """

        code = stock.get("code", "")
        name = stock.get("name", "")
        entry_price = stock.get("timing", {}).get("entry_price")
        target_return = stock.get("target_return_pct", 0)
        stop_loss = stock.get("stop_loss_pct", -3.0)

        # 获取当前行情（注意：这里使用最新数据，不是target_date的数据）
        current_data = get_stock_data(code)

        if "error" in current_data:
            return {
                "code": code,
                "name": name,
                "status": "error",
                "reason": current_data["error"],
                "evaluation_note": "数据获取失败",
            }

        current_price = current_data.get("close", 0)
        change_pct = current_data.get("change_pct", 0)
        data_date = current_data.get("date", "")

        # 计算收益率
        if entry_price and entry_price > 0:
            return_pct = calculate_return(entry_price, current_price)
        else:
            # 没有入场价，使用当日涨跌幅
            return_pct = change_pct

        # 判断是否达标（含中间状态）
        is_hit = return_pct >= target_return
        is_stopped = return_pct <= stop_loss
        is_near_target = return_pct >= target_return * 0.7 if target_return > 0 else False
        is_profitable = return_pct > 0

        # 确定状态（细化中间状态）
        if is_hit:
            status = "hit"
        elif is_stopped:
            status = "stopped"
        elif is_near_target:
            status = "near_target"
        elif is_profitable:
            status = "profitable"
        else:
            status = "active"

        # 检查数据日期是否匹配目标日期
        date_mismatch = data_date != target_date if data_date else True

        return {
            "code": code,
            "name": name,
            "sector": stock.get("sector", ""),
            "action": stock.get("action", ""),
            "entry_price": entry_price,
            "current_price": current_price,
            "return_pct": round(return_pct, 2),
            "target_return_pct": target_return,
            "stop_loss_pct": stop_loss,
            "is_hit": is_hit,
            "is_stopped": is_stopped,
            "status": status,
            "change_pct": round(change_pct, 2),
            "data_date": data_date,
            "target_date": target_date,
            "date_mismatch": date_mismatch,
            "evaluation_note": "使用最新数据评估（非目标日期）" if date_mismatch else "按目标日期评估",
        }
    
    def save_evaluation(self, evaluation: Dict) -> Path:
        """保存评估结果"""
        date_str = evaluation.get("date", date.today().isoformat())
        eval_dir = self.data_dir / "evaluations" / date_str
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        eval_file = eval_dir / "closing.json"
        
        with open(eval_file, "w", encoding="utf-8") as f:
            json.dump(evaluation, f, ensure_ascii=False, indent=2)
        
        # 同步写入SQLite
        try:
            from src.storage.db import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO evaluations 
                (date, recommendation_date, recommendation_type, metrics_data, raw_json, checksum)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                date_str,
                evaluation.get("recommendation_date", ""),
                evaluation.get("recommendation_type", ""),
                json.dumps(evaluation.get("metrics", {}), ensure_ascii=False),
                json.dumps(evaluation, ensure_ascii=False),
                "",  # checksum
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  ⚠️  SQLite写入失败: {e}")
        
        return eval_file
