# -*- coding: utf-8 -*-
"""主编排模块"""

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List

# Ensure .env is loaded
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.data_collectors.trading_calendar import is_trading_day, get_previous_trading_day
from src.data_collectors.market_data import get_market_overview, get_stock_data
from src.data_collectors.news import get_market_news, get_policy_news
from src.reporting.formatter import format_morning_report, format_json_report
from src.reporting.report_store import save_report, save_run_artifact, load_report
from src.analysis.synthesizer import Synthesizer


def persist_morning_outcome(report, run_id, source_status, errors):
    """Persist final component health after tracking, without overwriting a preserved good report."""
    if not source_status.get("report_preserved"):
        report["source_status"] = dict(source_status)
        report["errors"] = list(errors)
        save_report(report, "morning", archive_research=False)
    save_run_artifact(run_id=run_id, input_snapshot={"date": report.get("date"), "mode": "morning"},
                      source_status=source_status, report=report, errors=errors)


def run_morning_pipeline(dry_run: bool = False, force: bool = False, date_str: str = None) -> Dict:
    """执行盘前流程"""
    
    today = date_str if date_str else date.today().isoformat()
    run_id = f"{today}-morning"
    errors = []
    source_status = {}
    analysis_source = "unknown"
    llm_diagnostics = []
    
    # 1. 交易日历检查
    target_date = date.fromisoformat(today) if today else date.today()
    if not is_trading_day(target_date):
        return {"status": "skip", "reason": "非交易日"}
    source_status["trading_calendar"] = "ok"
    
    # 2. 检查是否已有当天报告
    existing_report = load_report(today, "morning")
    if existing_report and not force:
        return {"status": "exists", "report": existing_report}
    
    # 3. 检查昨日closing（bootstrap）
    yesterday = get_previous_trading_day(target_date).isoformat()
    yesterday_closing = load_report(yesterday, "closing")
    yesterday_review = None
    if not yesterday_closing:
        # bootstrap模式
        yesterday_review = None

    # 策略配置必须在候选池构建前加载，价格上限同时约束全市场扫描与最终推荐。
    try:
        import yaml
        strategy_file = Path(__file__).parent.parent / "config" / "strategy.yaml"
        with open(strategy_file, encoding="utf-8") as handle:
            strategy = yaml.safe_load(handle) or {}
        custom_params = dict(strategy.get("custom_params", {}))
        max_stock_price = float(custom_params.get("max_stock_price", 500) or 500)
    except Exception as exc:
        return {
            "status": "error",
            "reason": f"策略配置加载失败: {exc}",
            "errors": [f"策略配置加载失败: {exc}"],
        }
    
    # 4. 采集市场数据
    try:
        market_data = get_market_overview()
        if "error" in market_data:
            source_status["market_data"] = "error"
            errors.append(f"市场数据采集失败: {market_data['error']}")
            return {"status": "error", "reason": "市场数据采集失败", "errors": errors}
        else:
            source_status["market_data"] = "ok"
            from src.strategy.market_regime import detect_market_regime
            market_data["market_regime"] = detect_market_regime(market_data)
    except Exception as e:
        market_data = {"error": str(e)}
        source_status["market_data"] = "error"
        errors.append(f"市场数据采集失败: {e}")
        return {"status": "error", "reason": str(e), "errors": errors}

    # 4.1 构建全市场point-in-time候选池，再补充候选实时行情。
    try:
        from src.data_collectors.universe import get_ranked_candidates
        from src.data_collectors.realtime_prices import fetch_realtime_prices
        candidates = get_ranked_candidates(
            yesterday,
            limit=50,
            max_price=max_stock_price,
        )
        market_data["candidate_universe"] = candidates
        realtime_prices = fetch_realtime_prices([item["code"] for item in candidates])
        for item in candidates:
            if item["code"] in realtime_prices:
                realtime_prices[item["code"]]["sector"] = item["sector"]
                realtime_prices[item["code"]]["daily_amount"] = item["daily_amount"]
                realtime_prices[item["code"]]["market_cap"] = item["market_cap"]
        if realtime_prices:
            market_data["realtime_stock_prices"] = realtime_prices
            source_status["realtime_prices"] = f"ok_{len(realtime_prices)}"
            source_status["candidate_universe"] = f"ok_{len(candidates)}"
            print(f"  ✅ 实时行情: {len(realtime_prices)}只股票")
        else:
            source_status["realtime_prices"] = "empty"
            source_status["candidate_universe"] = "empty"
    except Exception as e:
        # 候选池失败时仍要生成并推送研究报告，但交易门禁保持关闭。
        candidates = []
        source_status["realtime_prices"] = "degraded"
        source_status["candidate_universe"] = "degraded"
        errors.append(f"全市场候选池/实时行情获取失败: {e}")
        print(f"  ⚠️  全市场候选池/实时行情获取失败: {e}")
    
    # 5. 采集新闻
    try:
        market_news = get_market_news()
        policy_news = get_policy_news()
        news_list = market_news + policy_news
        
        # 检查新闻是否有错误
        if any("error" in n for n in news_list):
            source_status["news"] = "degraded"
            errors.append("新闻源不可用，使用示例数据")
        else:
            source_status["news"] = "ok"
    except Exception as e:
        news_list = []
        source_status["news"] = "error"
        errors.append(f"新闻采集失败: {e}")
    
    # 6. 生成推荐（v0.2用LLM）
    sector_recommendations = []
    stock_recommendations = []
    price_validation = {}
    try:
        # 注入账户资金信息，让LLM根据资金量调整推荐策略
        try:
            from src.paper_trading.trading_service import TradingService
            acct = TradingService().get_account()
            custom_params["_account_cash"] = acct.get("cash", 0)
            custom_params["_account_equity"] = acct.get("total_equity", 0)
        except Exception as e:
            print(f"  ⚠️ 注入账户资金信息失败: {e}")  # B1: 原为静默pass

        synthesizer = Synthesizer()
        llm_result = synthesizer.analyze(
            market_data=market_data,
            news_list=news_list,
            factor_weights=strategy.get("factor_weights", {}),
            sector_allocations=strategy.get("sector_allocations", {}),
            custom_params=custom_params,
        )

        sector_recommendations = llm_result["data"].get("sector_recommendations", [])
        stock_recommendations = llm_result["data"].get("stock_recommendations", [])
        source_status["llm"] = llm_result["status"]
        analysis_source = llm_result.get("source", "unknown")
        llm_diagnostics = llm_result.get("diagnostics", [])

        # 模型输出后再次按代码获取真实行情。模型绝对价格一律不可直接进入交易。
        from src.analysis.price_guard import reconcile_recommendation_prices
        from src.data_collectors.realtime_prices import fetch_realtime_prices

        selected_codes = [
            str(stock.get("code"))
            for stock in stock_recommendations
            if stock.get("code")
        ]
        verified_quotes = fetch_realtime_prices(selected_codes)
        if verified_quotes:
            market_data.setdefault("realtime_stock_prices", {}).update(verified_quotes)
        price_validation = reconcile_recommendation_prices(
            stock_recommendations,
            verified_quotes,
            eligible_codes=[item.get("code") for item in candidates],
            max_price=max_stock_price,
        )
        validation_status = price_validation.get("status", "error")
        source_status["recommendation_prices"] = (
            validation_status
            if str(validation_status).startswith("ok_")
            else f"degraded_{validation_status}"
        )
        if price_validation.get("corrected"):
            print(f"  ✅ 已按真实行情校正{price_validation['corrected']}只股票的全部价格字段")
        if not str(validation_status).startswith("ok_"):
            errors.append(
                f"推荐价格仅校验{price_validation.get('verified', 0)}/"
                f"{price_validation.get('total', 0)}只，未验证信号已禁止交易"
            )

        # 策略调控过滤：周期偏好
        short_ratio = custom_params.get("short_ratio", 0.5)
        if short_ratio >= 0.7:
            # 短期为主：减少中长期推荐
            for s in stock_recommendations:
                if s.get("horizon") == "long" and s.get("action") == "setup_ready":
                    s["action"] = "track"
                    s["track_trigger"] = "策略调控设为短期为主，中长期降级为跟踪"
        
        if llm_result["status"] == "fallback":
            last_error = next(
                (item.get("error") for item in reversed(llm_diagnostics) if item.get("error")),
                "未返回具体错误",
            )
            errors.append(f"LLM不可用，使用fallback观察模式；最后错误: {last_error}")

        from src.strategy.market_regime import get_action_limits
        action_limit = get_action_limits(market_data.get("market_regime", "neutral"))["max_setup_ready"]
        setup_seen = 0
        for stock in stock_recommendations:
            if stock.get("action") == "setup_ready":
                setup_seen += 1
                if setup_seen > action_limit:
                    stock["action"] = "track"
                    stock["track_trigger"] = "受当前市场状态的可进场数量上限约束"
        
    except Exception as e:
        # 研判阶段异常时仍走同一套不可交易降级规范，避免字段漂移。
        fallback_result = Synthesizer()._use_fallback_template(market_data)
        fallback_data = fallback_result["data"]
        sector_recommendations = fallback_data.get("sector_recommendations", [])
        stock_recommendations = fallback_data.get("stock_recommendations", [])
        source_status["llm"] = "fallback"
        analysis_source = fallback_result.get("source", "static_template")
        llm_diagnostics = [{
            "phase": "orchestration",
            "attempt": 0,
            "status": "failed",
            "error": str(e)[:500],
        }]
        source_status["recommendation_prices"] = "degraded_error"
        errors.append(f"LLM调用失败，使用fallback模板: {e}")
    
    # 7. 生成报告
    report = format_json_report(
        date_str=today,
        report_type="morning",
        market_data=market_data,
        news_list=news_list,
        sector_recommendations=sector_recommendations,
        stock_recommendations=stock_recommendations,
    )
    report["source_status"] = dict(source_status)
    report["analysis_source"] = analysis_source
    report["analysis_degraded"] = source_status.get("llm") == "fallback"
    report["llm_diagnostics"] = llm_diagnostics
    report["price_validation"] = price_validation
    report["errors"] = list(errors)

    # 8. 保存报告（不覆盖已有的好数据）
    if not dry_run:
        # 如果新报告是fallback且已有更好的数据，不覆盖
        if source_status.get("llm") == "fallback" and existing_report:
            existing_llm = existing_report.get("source_status", {}).get("llm")
            existing_stocks = len(existing_report.get("stock_recommendations", []))
            new_stocks = len(stock_recommendations)
            if existing_llm == "success" or existing_stocks > new_stocks:
                print(f"  ℹ️  保留已有数据({existing_stocks}只)不覆盖fallback({new_stocks}只)")
                source_status["report_preserved"] = "existing_llm_success" if existing_llm == "success" else "richer_existing_report"
                report = existing_report
        if not source_status.get("report_preserved"):
            # Today's recommendation must be visible to the tracker before it runs.
            save_report(report, "morning")

    # 9. 08:45流程只生成盘前报告，绝不成交。
    # 最终订单必须由09:35任务重新取价、发送飞书卡片，再于确认后或09:40条件执行。
    auto_trade_result = {
        "status": "deferred_to_0935",
        "summary": {"total_trades": 0, "total_invested": 0},
    }
    source_status["auto_trade"] = "deferred_to_0935"

    # 10. 历史推荐跟踪
    tracking_result = None
    if not dry_run:
        try:
            from src.tracking.tracker import RecommendationTracker
            tracker = RecommendationTracker()
            tracking_result = tracker.track_daily(today)
            source_status["tracking"] = "ok"
            summary = tracking_result.get("summary", {})
            print(f"  📊 跟踪: {summary.get('total_tracked', 0)}只, "
                  f"胜率{summary.get('win_rate_pct', 0)}%")
        except Exception as e:
            source_status["tracking"] = "degraded"
            errors.append(f"推荐跟踪失败: {e}")
            print(f"  ⚠️  推荐跟踪失败: {e}")

    if not dry_run:
        persist_morning_outcome(report, run_id, source_status, errors)

    # A4: 根据 errors 正确判定最终状态（之前恒返回 success）
    has_errors = len(errors) > 0
    has_critical = any(s == "error" for s in source_status.values())
    final_status = "partial_success" if (has_errors and not has_critical) else ("error" if has_critical else "success")

    return {
        "status": final_status,  # A4: 修复 — 之前恒返回 "success"
        "report": report,
        "source_status": source_status,
        "errors": errors,
        "auto_trade": auto_trade_result,
        "tracking": tracking_result.get("summary") if tracking_result else None,
    }


def generate_sector_recommendations(market_data: Dict, news_list: List[Dict]) -> List[Dict]:
    """生成板块推荐（v0.1规则版）"""
    
    # 简单规则：根据新闻关键词推荐板块
    sectors = []
    
    # 检查新闻中的关键词
    news_text = " ".join([n.get("title", "") + n.get("summary", "") for n in news_list])
    
    if "AI" in news_text or "人工智能" in news_text:
        sectors.append({
            "sector_name": "AI算力",
            "rating": 4,
            "reason": "新闻中提及AI相关消息",
            "horizon": "short",
            "horizon_days": 3,
            "target_return_pct": 5.0,
        })
    
    if "半导体" in news_text or "芯片" in news_text:
        sectors.append({
            "sector_name": "半导体",
            "rating": 3,
            "reason": "新闻中提及半导体相关消息",
            "horizon": "short",
            "horizon_days": 3,
            "target_return_pct": 3.0,
        })
    
    # 如果没有匹配，返回默认推荐
    if not sectors:
        sectors.append({
            "sector_name": "金融",
            "rating": 3,
            "reason": "默认推荐",
            "horizon": "short",
            "horizon_days": 3,
            "target_return_pct": 2.0,
        })
    
    return sectors


def generate_stock_recommendations(market_data: Dict, news_list: List[Dict]) -> List[Dict]:
    """生成个股推荐（v0.1规则版）"""
    
    # v0.1只返回示例推荐，v0.2用LLM生成
    return [
        {
            "code": "000001",
            "name": "平安银行",
            "sector": "金融",
            "action": "watch",
            "reason": "v0.1示例推荐",
            "target_return_pct": 2.0,
        }
    ]
