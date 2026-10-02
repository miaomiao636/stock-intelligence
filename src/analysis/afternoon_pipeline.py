# -*- coding: utf-8 -*-
"""13:15盘中复核：只生成独立报告，不直接创建或执行订单。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

import yaml

from src.analysis.synthesizer import Synthesizer
from src.data_collectors.market_data import get_realtime_market_overview
from src.data_collectors.news import get_market_news, get_policy_news
from src.data_collectors.realtime_prices import fetch_realtime_prices
from src.data_collectors.trading_calendar import is_trading_day
from src.reporting.formatter import format_json_report
from src.reporting.report_store import load_report, save_report, save_run_artifact
from src.strategy.market_regime import detect_market_regime


TZ = ZoneInfo("Asia/Shanghai")
ACTION_RANK = {
    "avoid": 0,
    "watch": 1,
    "reduce": 1,
    "track": 2,
    "hold": 2,
    "setup_ready": 3,
}


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decision(previous_action: str, current_action: str) -> str:
    if current_action == "avoid":
        return "cancel"
    previous_rank = ACTION_RANK.get(previous_action, 1)
    current_rank = ACTION_RANK.get(current_action, 1)
    if current_rank > previous_rank:
        return "upgrade"
    if current_rank < previous_rank:
        return "downgrade"
    return "maintain"


def _fallback_stocks(morning_stocks: List[Dict], quotes: Dict[str, Dict]) -> List[Dict]:
    """LLM不可用时保留上午观点用于展示，但明确禁止成交。"""
    result = []
    for original in morning_stocks:
        stock = deepcopy(original)
        code = str(stock.get("code") or "")
        quote = quotes.get(code, {})
        morning_price = _safe_float(stock.get("current_price") or stock.get("entry_price"))
        latest_price = _safe_float(quote.get("price")) or morning_price
        stock.update({
            "morning_price": morning_price,
            "current_price": latest_price,
            "latest_price": latest_price,
            "intraday_change_pct": quote.get("change_pct"),
            "since_morning_pct": round((latest_price / morning_price - 1) * 100, 2)
            if latest_price and morning_price else None,
            "afternoon_decision": "maintain",
            "trade_eligible": False,
            "decision_scope": "advisory_only",
            "degraded_reason": "盘中LLM不可用，保留上午观点供观察且禁止自动交易",
        })
        result.append(stock)
    return result


def _merge_llm_stocks(
    morning_stocks: List[Dict], llm_stocks: List[Dict], quotes: Dict[str, Dict]
) -> List[Dict]:
    """只允许复核上午候选；价格永远以实时源覆盖模型输出。"""
    by_code = {
        str(item.get("code") or ""): item
        for item in llm_stocks
        if isinstance(item, dict)
    }
    result = []
    for original in morning_stocks:
        code = str(original.get("code") or "")
        update = by_code.get(code)
        stock = deepcopy(original)
        # A new observation must not silently renew the morning forecast horizon.
        stock["forecast_direction"] = "unknown"
        stock["forecast_horizon_sessions"] = None
        if update:
            for key in (
                "action", "confidence", "horizon", "horizon_days", "reason",
                "forecast_direction", "forecast_horizon_sessions",
                "reason_news", "reason_policy", "reason_technical", "reason_fund",
                "incremental_basis", "track_trigger", "target_price",
                "stop_loss_price", "target_return_pct", "stop_loss_pct", "timing",
            ):
                if key in update:
                    stock[key] = update[key]
        quote = quotes.get(code, {})
        morning_price = _safe_float(original.get("current_price") or original.get("entry_price"))
        latest_price = _safe_float(quote.get("price")) or morning_price
        current_action = stock.get("action", original.get("action", "watch"))
        model_decision = update.get("afternoon_decision") if update else None
        decision = model_decision if model_decision in {
            "maintain", "upgrade", "downgrade", "cancel"
        } else _decision(original.get("action", "watch"), current_action)
        stock.update({
            "morning_price": morning_price,
            "current_price": latest_price,
            "latest_price": latest_price,
            "intraday_change_pct": quote.get("change_pct"),
            "since_morning_pct": round((latest_price / morning_price - 1) * 100, 2)
            if latest_price and morning_price else None,
            "afternoon_decision": decision,
            "trade_eligible": False,
            "decision_scope": "advisory_only",
        })
        if not update:
            stock["reason"] = "盘中复核未返回新的个股判断，暂时维持上午观点并继续观察。"
        result.append(stock)
    return result


def run_afternoon_pipeline(
    dry_run: bool = False,
    force: bool = False,
    date_str: str | None = None,
) -> Dict:
    """生成13:15盘中复核报告；任何降级都不会产生交易订单。"""
    today = date_str or date.today().isoformat()
    target_date = date.fromisoformat(today)
    run_id = f"{today}-afternoon"
    if not is_trading_day(target_date):
        return {"status": "skip", "reason": "非交易日"}

    existing = load_report(today, "afternoon")
    if existing and not force:
        return {"status": "exists", "report": existing}

    morning = load_report(today, "morning")
    if not morning:
        return {"status": "error", "reason": "缺少当天盘前报告，无法进行盘中复核"}

    errors: List[str] = []
    source_status: Dict[str, str] = {"trading_calendar": "ok", "morning_report": "ok"}
    morning_stocks = [
        item for item in morning.get("stock_recommendations", []) if isinstance(item, dict)
    ]
    codes = list(dict.fromkeys(str(item.get("code") or "") for item in morning_stocks))
    codes = [code for code in codes if code]

    try:
        market_data = get_realtime_market_overview()
        if not isinstance(market_data, dict) or market_data.get("error"):
            raise RuntimeError((market_data or {}).get("error") or "实时指数为空")
        market_data["market_regime"] = detect_market_regime(market_data)
        source_status["market_data"] = "ok"
    except Exception as exc:
        market_data = {"indices": {}, "market_regime": "neutral", "error": str(exc)}
        source_status["market_data"] = "degraded"
        errors.append(f"盘中指数获取失败: {exc}")

    quotes = fetch_realtime_prices(codes)
    source_status["realtime_prices"] = (
        f"ok_{len(quotes)}" if len(quotes) == len(codes) else f"degraded_{len(quotes)}_of_{len(codes)}"
    )
    if len(quotes) < len(codes):
        errors.append(f"盘中个股行情仅获取到 {len(quotes)}/{len(codes)} 只")
    market_data["realtime_stock_prices"] = quotes

    try:
        news_list = get_market_news() + get_policy_news()
        source_status["news"] = (
            "ok" if news_list and not any("error" in item for item in news_list) else "degraded"
        )
    except Exception as exc:
        news_list = []
        source_status["news"] = "degraded"
        errors.append(f"盘中新闻获取失败: {exc}")

    account = {}
    positions = []
    try:
        from src.paper_trading.trading_service import TradingService

        service = TradingService()
        account = service.get_account()
        positions = service.get_positions(None)
        source_status["portfolio"] = "ok"
    except Exception as exc:
        source_status["portfolio"] = "degraded"
        errors.append(f"持仓快照获取失败: {exc}")

    market_data["afternoon_context"] = {
        "as_of": datetime.now(TZ).isoformat(),
        "morning_market_regime": morning.get("market_regime"),
        "account": account,
        "positions": positions,
    }

    strategy_file = Path(__file__).resolve().parents[2] / "config" / "strategy.yaml"
    strategy = yaml.safe_load(strategy_file.read_text(encoding="utf-8")) or {}
    custom_params = dict(strategy.get("custom_params", {}))
    custom_params["_account_cash"] = account.get("cash", 0)
    custom_params["_account_equity"] = account.get("total_equity", 0)

    forecast_input_market_data = deepcopy(market_data)
    llm_result = Synthesizer().analyze_afternoon(
        market_data=market_data,
        morning_report={
            "market_regime": morning.get("market_regime"),
            "sector_recommendations": morning.get("sector_recommendations", []),
            "stock_recommendations": morning_stocks,
        },
        news_list=news_list,
        factor_weights=strategy.get("factor_weights", {}),
        sector_allocations=strategy.get("sector_allocations", {}),
        custom_params=custom_params,
    )
    source_status["llm"] = llm_result.get("status", "fallback")
    llm_success = llm_result.get("status") == "success"
    llm_data = llm_result.get("data") if isinstance(llm_result.get("data"), dict) else {}
    if llm_success:
        stocks = _merge_llm_stocks(morning_stocks, llm_data.get("stock_recommendations", []), quotes)
        sectors = llm_data.get("sector_recommendations") or deepcopy(
            morning.get("sector_recommendations", [])
        )
    else:
        stocks = _fallback_stocks(morning_stocks, quotes)
        sectors = deepcopy(morning.get("sector_recommendations", []))
        errors.append("盘中LLM不可用，已生成不可交易的观察性复核")

    report = format_json_report(
        date_str=today,
        report_type="afternoon",
        market_data=market_data,
        news_list=news_list,
        sector_recommendations=sectors,
        stock_recommendations=stocks,
        forecast_input_market_data=forecast_input_market_data,
    )
    report.update({
        "source_status": source_status,
        "analysis_source": llm_result.get("source", "unknown"),
        "analysis_degraded": not llm_success,
        "llm_diagnostics": llm_result.get("diagnostics", []),
        "morning_report_ref": f"{today}-morning",
        "afternoon_summary": {
            "maintain": sum(item.get("afternoon_decision") == "maintain" for item in stocks),
            "upgrade": sum(item.get("afternoon_decision") == "upgrade" for item in stocks),
            "downgrade": sum(item.get("afternoon_decision") == "downgrade" for item in stocks),
            "cancel": sum(item.get("afternoon_decision") == "cancel" for item in stocks),
            "trade_execution": "disabled_advisory_only",
        },
    })

    if not dry_run:
        if not (
            not llm_success
            and existing
            and existing.get("source_status", {}).get("llm") == "success"
        ):
            save_report(report, "afternoon")
        else:
            report = existing
            source_status["report_preserved"] = "existing_llm_success"
        save_run_artifact(
            run_id=run_id,
            input_snapshot={
                "date": today,
                "mode": "afternoon",
                "morning_report_ref": f"{today}-morning",
            },
            source_status=source_status,
            report=report,
            errors=errors,
        )

    return {
        "status": "success" if not errors else "partial_success",
        "report": report,
        "source_status": source_status,
        "errors": errors,
        "auto_trade": {"status": "disabled_advisory_only", "summary": {"total_trades": 0}},
    }
