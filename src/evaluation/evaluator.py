# -*- coding: utf-8 -*-
"""评估器模块"""

import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.data_collectors.market_data import get_stock_data_on, get_index_data_on
from src.evaluation.metrics import calculate_return, calculate_metrics
from src.evaluation.leak_guard import validate_no_future_data
from src.utils.cost_calculator import calculate_trade_costs


class Evaluator:
    """评估器"""
    
    def __init__(self, data_dir: Path = None, execution_lookup: Callable = None):
        self.data_dir = Path(data_dir or Path(__file__).parent.parent.parent / "data")
        self.execution_lookup = execution_lookup or self._lookup_filled_buy

    def _lookup_filled_buy(self, recommendation_id: str, code: str) -> Optional[Dict]:
        """只读查询推荐对应的真实买入成交；不存在账本时返回 None。"""
        if not recommendation_id:
            return None
        db_path = self.data_dir / "stock_intelligence.db"
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT o.order_id,o.recommendation_id,o.code,o.stop_price,o.target_price,
                           o.instrument_type,t.trade_id,t.quantity,t.price AS fill_price,
                           t.amount AS fill_amount,t.fees AS entry_fees,t.slippage AS entry_slippage,
                           t.executed_at,t.trade_date
                    FROM trading_orders o
                    JOIN trading_trades t ON t.order_id=o.order_id
                    WHERE o.recommendation_id=? AND o.code=?
                      AND o.action='buy' AND o.status='filled'
                    ORDER BY t.executed_at DESC LIMIT 1
                    """,
                    (recommendation_id, code),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    @staticmethod
    def _is_actionable(stock: Dict) -> bool:
        """Return whether a recommendation was eligible to enter."""
        action = str(stock.get("action") or "").strip().lower()
        return (
            action in {"", "setup_ready", "buy"}
            and stock.get("trade_eligible", True) is not False
        )
    
    def evaluate_recommendation(
        self,
        recommendation: Dict,
        target_date: Optional[str] = None
    ) -> Dict:
        """评估单条推荐"""
        
        if target_date is None:
            target_date = date.today().isoformat()
        
        stock_results = []
        all_stocks = recommendation.get("stock_recommendations", [])
        actionable_stocks = [stock for stock in all_stocks if self._is_actionable(stock)]

        for stock in actionable_stocks:
            result = self._evaluate_stock(stock, target_date, recommendation)
            stock_results.append(result)
        
        # 计算质量状态
        total = len(stock_results)
        valid_results = [
            r for r in stock_results
            if r.get("status") not in {"error", "not_triggered"}
        ]
        error_results = [r for r in stock_results if r.get("status") == "error"]
        not_triggered_results = [r for r in stock_results if r.get("status") == "not_triggered"]
        valid_count = len(valid_results)
        error_count = len(error_results)
        not_triggered_count = len(not_triggered_results)
        error_rate = error_count / total if total > 0 else 0
        
        # 未触发计划价属于正常空仓结果，不应把 no-trade 日误报为评估故障。
        scoreable_total = total - not_triggered_count - error_count
        min_valid_required = min(3, scoreable_total) if scoreable_total > 0 else 0
        if total > 0 and error_count == total:
            eval_status = "error"
        elif error_rate > 0.2:
            eval_status = "degraded"
        else:
            eval_status = "success"
        
        # 计算目标交易日基准收益（使用沪深300指数）。历史评估不得回退到最新行情。
        benchmark_data = get_index_data_on("000300", target_date)
        benchmark_error = benchmark_data.get("error")
        benchmark_return = benchmark_data.get("change_pct", 0) if not benchmark_error else 0
        if benchmark_error and eval_status == "success":
            eval_status = "degraded"
        
        # 计算综合指标（只使用有效样本）
        metrics = calculate_metrics(valid_results, benchmark_return)
        
        evaluation = {
            "date": target_date,
            "recommendation_date": recommendation.get("date"),
            "recommendation_type": recommendation.get("type"),
            "status": eval_status,
            "quality": {
                "total": total,
                "source_total": len(all_stocks),
                "excluded_non_actionable": len(all_stocks) - total,
                "valid": valid_count,
                "not_triggered": not_triggered_count,
                "error": error_count,
                "error_rate": round(error_rate, 2),
                "min_valid_required": min_valid_required,
            },
            "stock_results": stock_results,
            "metrics": metrics,
            "benchmark_return_pct": benchmark_return,
            "benchmark_data_date": benchmark_data.get("date", ""),
            "benchmark_status": "error" if benchmark_error else "ok",
            "benchmark_error": benchmark_error,
            "evaluated_at": datetime.now().isoformat(),
        }

        leak_guard = validate_no_future_data(recommendation, evaluation)
        evaluation["leak_guard"] = leak_guard
        if not leak_guard["valid"]:
            evaluation["status"] = "error"
        return evaluation
    
    def _evaluate_stock(
        self,
        stock: Dict,
        target_date: str,
        recommendation: Dict = None,
    ) -> Dict:
        """使用 target_date 的收盘数据评估单只股票。"""

        code = stock.get("code", "")
        name = stock.get("name", "")
        recommendation = recommendation or {}
        recommendation_id = stock.get("recommendation_id")
        if not recommendation_id and recommendation.get("run_id"):
            recommendation_id = f"REC-{recommendation['run_id']}-{code}"
        execution = self.execution_lookup(recommendation_id, code) if recommendation_id else None
        if execution and str(execution.get("trade_date") or "") > target_date:
            execution = None
        timing = stock.get("timing") if isinstance(stock.get("timing"), dict) else {}
        raw_entry_price = stock.get("entry_price") or timing.get("entry_price")
        try:
            planned_entry_price = float(raw_entry_price)
        except (TypeError, ValueError):
            planned_entry_price = 0.0
        if planned_entry_price <= 0 and not execution:
            return {
                "code": code,
                "name": name,
                "status": "error",
                "reason": "缺少有效进场价，无法按统一口径计算推荐收益",
                "data_date": "",
                "target_date": target_date,
                "return_basis": "missing_entry_price",
                "evaluation_note": "已拒绝用当日涨跌幅替代推荐收益",
            }
        try:
            target_return = float(stock.get("target_return_pct") or 0)
        except (TypeError, ValueError):
            target_return = 0.0
        try:
            stop_loss = float(stock.get("stop_loss_pct") or -3.0)
        except (TypeError, ValueError):
            stop_loss = -3.0
        if stop_loss > 0:
            stop_loss = -stop_loss

        current_data = get_stock_data_on(code, target_date)

        if "error" in current_data:
            return {
                "code": code,
                "name": name,
                "status": "error",
                "reason": current_data["error"],
                "data_date": current_data.get("date", ""),
                "target_date": target_date,
                "evaluation_note": "目标交易日数据获取失败，已拒绝用最新价替代",
            }

        current_price = current_data.get("close", 0)
        change_pct = current_data.get("change_pct", 0)
        data_date = current_data.get("date", "")

        if execution:
            entry_price = float(execution["fill_price"])
            target_price = float(execution.get("target_price") or 0)
            stop_price = float(execution.get("stop_price") or 0)
            quantity = int(execution.get("quantity") or 0)
            exit_cost = calculate_trade_costs(
                current_price * quantity,
                "sell",
                execution.get("instrument_type") or "stock",
            )
            exit_value = (
                current_price * exit_cost["effective_price_factor"] * quantity
                - exit_cost["fees"]
            )
            entry_value = float(execution.get("fill_amount") or entry_price * quantity)
            entry_outlay = entry_value + float(execution.get("entry_fees") or 0)
            return_pct = (
                (exit_value - entry_outlay) / entry_outlay * 100
                if entry_outlay > 0 else 0.0
            )
            return_basis = "actual_fill_to_close_after_estimated_exit_costs"
            execution_status = "filled"
        else:
            day_low = float(current_data.get("low") or 0)
            if day_low > 0 and day_low > planned_entry_price:
                return {
                    "code": code,
                    "name": name,
                    "sector": stock.get("sector", ""),
                    "action": stock.get("action", ""),
                    "planned_entry_price": planned_entry_price,
                    "current_price": current_price,
                    "return_pct": None,
                    "status": "not_triggered",
                    "execution_status": "not_triggered",
                    "change_pct": round(change_pct, 2),
                    "data_date": data_date,
                    "target_date": target_date,
                    "date_mismatch": data_date != target_date if data_date else True,
                    "return_basis": "no_position_no_return",
                    "evaluation_note": (
                        f"目标日最低价¥{day_low:.2f}未触及计划进场价¥{planned_entry_price:.2f}"
                    ),
                }
            day_open = float(current_data.get("open") or 0)
            entry_price = min(planned_entry_price, day_open) if day_open > 0 else planned_entry_price
            target_price = float(
                stock.get("target_price") or timing.get("target_observation_price") or 0
            )
            stop_price = float(stock.get("stop_loss_price") or timing.get("stop_loss_price") or 0)
            return_pct = calculate_return(entry_price, current_price)
            return_basis = "triggered_limit_entry_to_close"
            execution_status = "theoretical_trigger"

        if target_price > entry_price:
            target_return = (target_price / entry_price - 1) * 100
        if 0 < stop_price < entry_price:
            stop_loss = (stop_price / entry_price - 1) * 100

        # 判断是否达标（含中间状态）
        is_hit = current_price >= target_price if target_price > 0 else return_pct >= target_return
        is_stopped = current_price <= stop_price if stop_price > 0 else return_pct <= stop_loss
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

        # 日期不匹配由 leak_guard 统一失败关闭。
        date_mismatch = data_date != target_date if data_date else True

        return {
            "code": code,
            "name": name,
            "sector": stock.get("sector", ""),
            "action": stock.get("action", ""),
            "entry_price": entry_price,
            "planned_entry_price": planned_entry_price or None,
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
            "return_basis": return_basis,
            "execution_status": execution_status,
            "order_id": execution.get("order_id") if execution else None,
            "trade_id": execution.get("trade_id") if execution else None,
            "evaluation_note": (
                "数据日期与目标日期不一致"
                if date_mismatch
                else "按实际成交与成本评估" if execution else "计划价已触发，按理论限价成交评估"
            ),
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
            cursor.execute(
                "DELETE FROM evaluations WHERE date=? AND recommendation_date=? AND recommendation_type=?",
                (
                    date_str,
                    evaluation.get("recommendation_date", ""),
                    evaluation.get("recommendation_type", ""),
                ),
            )
            
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
