# -*- coding: utf-8 -*-
"""盘后流程模块"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Optional

from src.data_collectors.trading_calendar import is_trading_day
from src.data_collectors.market_data import get_market_overview_on
from src.evaluation.evaluator import Evaluator
from src.reporting.report_store import load_report


def run_closing_pipeline(
    dry_run: bool = False,
    force: bool = False,
    date_str: str = None
) -> Dict:
    """执行盘后流程"""

    today = date_str if date_str else date.today().isoformat()
    errors = []
    warnings = []
    source_status = {}

    # 1. 交易日历检查
    target_date = date.fromisoformat(today) if today else date.today()
    if not is_trading_day(target_date):
        return {"status": "skip", "reason": "非交易日"}
    source_status["trading_calendar"] = "ok"

    # 2. 检查当天morning.json
    morning_report = load_report(today, "morning")
    if not morning_report:
        return {"status": "error", "reason": "未找到当天盘前推荐，请先运行morning"}
    source_status["morning_report"] = "ok"

    # 3. 获取收盘行情
    try:
        market_data = get_market_overview_on(today)
        if "error" in market_data:
            source_status["market_data"] = "error"
            errors.append(f"市场数据获取失败: {market_data['error']}")
        else:
            source_status["market_data"] = "ok"
    except Exception as e:
        source_status["market_data"] = "error"
        errors.append(f"市场数据获取失败: {e}")
        market_data = {"error": str(e)}

    # 4. 执行评估
    try:
        evaluator = Evaluator()
        evaluation = evaluator.evaluate_recommendation(morning_report, today)

        # 检查评估的实际状态
        eval_status = evaluation.get("status", "unknown")
        eval_error_rate = evaluation.get("quality", {}).get("error_rate", 0)

        benchmark_error = evaluation.get("benchmark_error")
        eval_error_count = evaluation.get("quality", {}).get("error", 0)
        eval_total = evaluation.get("quality", {}).get("total", 0)

        if eval_status == "error":
            source_status["evaluation"] = "error"
            if eval_error_count:
                warnings.append(
                    f"个股评估失败 {eval_error_count}/{eval_total}，error_rate={eval_error_rate:.1%}"
                )
        elif eval_status == "degraded":
            source_status["evaluation"] = "degraded"
            if eval_error_count:
                warnings.append(
                    f"个股评估缺失 {eval_error_count}/{eval_total}，error_rate={eval_error_rate:.1%}"
                )
        else:
            source_status["evaluation"] = "ok"

        if benchmark_error:
            warnings.append(f"沪深300基准数据不可用: {benchmark_error}")
        elif eval_status in {"error", "degraded"} and not eval_error_count:
            warnings.append("评估质量未达标，请查看评估详情")

    except Exception as e:
        source_status["evaluation"] = "error"
        errors.append(f"评估异常: {e}")
        evaluation = {"status": "error", "error": str(e)}

    # 5. 保存评估结果（只在评估成功或降级时保存完整评估，错误时仅保存摘要）
    auto_sell_trades = []  # 兼容旧报告字段；新流程不再直接自动卖出
    exit_alerts = []
    eval_status = evaluation.get("status", "unknown") if isinstance(evaluation, dict) else "error"

    # 5.1 保存收盘报告到recommendations目录（Dashboard需要，无论评估状态如何都保存）
    if not dry_run:
        try:
            from src.reporting.report_store import save_report
            closing_report = dict(morning_report)
            closing_report["type"] = "closing"
            closing_report["evaluation"] = evaluation
            closing_report["market_data"] = market_data
            closing_report["created_at"] = datetime.now().isoformat()
            save_report(closing_report, "closing")
            source_status["closing_report"] = "ok"
        except Exception as e:
            warnings.append(f"盘后报告保存失败: {e}")
            source_status["closing_report"] = "error"

    # 5.2 保存完整评估结果（仅在评估成功/降级时）
    if not dry_run and eval_status in ["success", "degraded"]:
        try:
            eval_file = evaluator.save_evaluation(evaluation)
            source_status["save"] = "ok"
        except Exception as e:
            source_status["save"] = "error"
            errors.append(f"保存失败: {e}")

    # 6. SQLite 账本盯市 + 生成飞书卖出计划。历史补跑不得触碰实时持仓。
    if not dry_run:
        if today != date.today().isoformat():
            source_status["position_update"] = "skipped_historical_run"
            source_status["exit_plan"] = "skipped_historical_run"
        else:
            try:
                from src.paper_trading.workflow import PaperTradingWorkflow

                trading_result = PaperTradingWorkflow().intraday_check()
                trading_state = trading_result.get("status", "error")
                exit_alerts = trading_result.get("alerts", [])
                if trading_state == "success":
                    source_status["position_update"] = "ok"
                    source_status["exit_plan"] = (
                        f"alerts_{len(exit_alerts)}" if exit_alerts else "no_change"
                    )
                elif trading_state == "disabled":
                    source_status["position_update"] = "disabled"
                    source_status["exit_plan"] = "disabled"
                else:
                    source_status["position_update"] = "error"
                    errors.append(f"SQLite持仓盯市失败: {trading_result.get('reason', trading_state)}")
            except Exception as e:
                source_status["position_update"] = "error"
                errors.append(f"SQLite持仓盯市失败: {e}")
                print(f"  ⚠️  SQLite持仓盯市失败: {e}")

    # 7. 更新推荐跟踪（无论评估状态都执行）
    if not dry_run:
        if today != date.today().isoformat():
            source_status["tracking"] = "skipped_historical_run"
        else:
            try:
                from src.tracking.tracker import RecommendationTracker
                tracker = RecommendationTracker()
                tracking_result = tracker.track_daily(today)
                source_status["tracking"] = "ok"
                summary = tracking_result.get("summary", {})
                print(f"  📊 跟踪更新: 已结束样本胜率{summary.get('win_rate_pct', 0)}%")
            except Exception as e:
                source_status["tracking"] = "error"
                errors.append(f"跟踪更新失败: {e}")

    # 8. 生成策略建议（只在评估状态为success时）
    if not dry_run and evaluation.get("status") == "success":
        try:
            from src.strategy.advisor import StrategyAdvisor
            advisor = StrategyAdvisor()
            advisories = advisor.generate_advisories(evaluation)
            if advisories:
                advisor.save_advisories(advisories)
                source_status["strategy_advisory"] = f"generated_{len(advisories)}"
            else:
                source_status["strategy_advisory"] = "no_advisories"
        except Exception as e:
            source_status["strategy_advisory"] = "error"
            warnings.append(f"策略建议生成失败: {e}")

    # 确定整体状态
    if errors:
        pipeline_status = "error"
    elif warnings:
        pipeline_status = "degraded"
    else:
        pipeline_status = "success"

    # 确定数据质量状态
    eval_status = source_status.get("evaluation", "unknown")
    if eval_status == "error":
        data_quality = "error"
    elif eval_status == "degraded":
        data_quality = "degraded"
    else:
        data_quality = "ok"

    return {
        "status": pipeline_status,
        "run_status": "completed",
        "data_quality_status": data_quality,
        "trading_status": source_status.get("position_update", "unknown"),
        "evaluation_status": eval_status,
        "evaluation": evaluation,
        "market_data": market_data,
        "source_status": source_status,
        "errors": errors,
        "warnings": warnings,
        "auto_sell_trades": auto_sell_trades if not dry_run else [],
        "exit_alerts": exit_alerts if not dry_run else [],
    }
