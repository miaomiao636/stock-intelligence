# -*- coding: utf-8 -*-
"""报告格式化模块"""

import json
from datetime import datetime
from typing import Dict, List


def format_morning_report(
    date_str: str,
    market_data: Dict,
    news_list: List[Dict],
    sector_recommendations: List[Dict],
    stock_recommendations: List[Dict],
) -> str:
    """格式化盘前报告为飞书消息（纯文本）"""
    
    lines = []
    lines.append(f"🌅 盘前智能推荐 - {date_str}")
    lines.append("")
    
    # 市场数据
    lines.append("📈 市场概况")
    if "error" not in market_data:
        for code, data in market_data.get("indices", {}).items():
            change_pct = data.get("change_pct", 0)
            change_icon = "📈" if change_pct >= 0 else "📉"
            lines.append(f"  {change_icon} {data['name']}: {data['close']:.2f} ({change_pct:+.2f}%)")
    else:
        lines.append("  市场数据暂不可用")
    lines.append("")
    
    # 今日要闻（只显示3条重要的）
    lines.append("📰 今日要闻")
    news_count = 0
    for news in news_list[:5]:
        if "error" not in news and news_count < 3:
            title = news.get("title", "")
            source = news.get("source", "")
            # 只显示有实际内容的新闻
            if title and len(title) > 10:
                lines.append(f"  · {title[:50]}...（{source}）")
                news_count += 1
    if news_count == 0:
        lines.append("  暂无重要新闻")
    lines.append("")
    
    # 推荐板块
    lines.append("🎯 推荐板块")
    for sector in sector_recommendations:
        name = sector.get("sector_name", "")
        rating = "⭐" * sector.get("rating", 0)
        reason = sector.get("reason", "")[:40]
        lines.append(f"  · {name} {rating}")
        lines.append(f"    原因: {reason}...")
    lines.append("")
    
    # 推荐个股（标明板块和原因）
    lines.append("📈 推荐个股")
    for stock in stock_recommendations:
        code = stock.get("code", "")
        name = stock.get("name", "")
        sector = stock.get("sector", "")
        action = stock.get("action", "")
        reason = stock.get("reason", "")[:40]
        
        # 翻译action
        action_map = {
            "setup_ready": "可进场",
            "track": "跟踪观察",
            "hold": "继续持有",
            "watch": "观察",
            "avoid": "回避",
        }
        action_cn = action_map.get(action, action)
        
        lines.append(f"  · {code} {name}")
        lines.append(f"    板块: {sector} | 操作: {action_cn}")
        lines.append(f"    原因: {reason}...")
    lines.append("")
    
    lines.append("📌 免责声明: 以下为研究信号，不构成买卖建议，投资决策需自行判断。")
    
    return "\n".join(lines)


def format_afternoon_report(
    date_str: str,
    market_data: Dict,
    stock_recommendations: List[Dict],
    summary: Dict | None = None,
    degraded: bool = False,
) -> str:
    """格式化13:15盘中复核；明确说明仅分析、不直接成交。"""
    summary = summary if isinstance(summary, dict) else {}
    lines = [f"☀️ 下午盘中复核 - {date_str}", ""]
    if degraded:
        lines.extend([
            "⚠️ 盘中研判已降级",
            "  已刷新真实行情，但AI复核不可用；以下仅保留上午观点供观察，不触发交易。",
            "",
        ])
    lines.append("📈 13:15市场概况")
    indices = market_data.get("indices", {}) if isinstance(market_data, dict) else {}
    if indices:
        for code, item in indices.items():
            if not isinstance(item, dict):
                continue
            close = item.get("close")
            change = item.get("change_pct")
            close_text = f"{close:.2f}" if isinstance(close, (int, float)) else "--"
            change_text = f" ({change:+.2f}%)" if isinstance(change, (int, float)) else ""
            lines.append(f"  · {item.get('name') or code}: {close_text}{change_text}")
    else:
        lines.append("  盘中指数暂不可用")

    lines.extend([
        "",
        "🧭 相对上午的判断",
        f"  维持 {summary.get('maintain', 0)} · 升级 {summary.get('upgrade', 0)} · "
        f"降级 {summary.get('downgrade', 0)} · 取消 {summary.get('cancel', 0)}",
        "",
        "📋 个股复核",
    ])
    decision_map = {
        "maintain": "维持",
        "upgrade": "升级",
        "downgrade": "降级",
        "cancel": "取消",
    }
    for stock in stock_recommendations:
        decision = decision_map.get(stock.get("afternoon_decision"), "维持")
        price = stock.get("latest_price") or stock.get("current_price")
        price_text = f"¥{price:.2f}" if isinstance(price, (int, float)) else "--"
        since_morning = stock.get("since_morning_pct")
        change_text = f"，较上午{since_morning:+.2f}%" if isinstance(since_morning, (int, float)) else ""
        lines.append(
            f"  · {stock.get('name', '')}({stock.get('code', '')}) {decision} | {price_text}{change_text}"
        )
        reason = str(stock.get("incremental_basis") or stock.get("reason") or "")
        if reason:
            lines.append(f"    {reason[:80]}")
    if not stock_recommendations:
        lines.append("  暂无上午候选可复核")
    lines.extend([
        "",
        "🛡️ 本报告仅更新研究判断，不会绕过5分钟否决和既有风控直接成交。",
        "📌 免责声明: 以下为研究信号，不构成买卖建议。",
    ])
    return "\n".join(lines)


def format_closing_report(
    date_str: str,
    market_data: Dict,
    evaluation: Dict,
    warnings: List[str] | None = None,
    account_summary: Dict | None = None,
) -> str:
    """格式化盘后报告；辅助数据降级时仍生成可读报告。"""
    market_data = market_data if isinstance(market_data, dict) else {}
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    warnings = warnings if isinstance(warnings, list) else []
    account_summary = account_summary if isinstance(account_summary, dict) else {}
    lines = [f"🌆 盘后复盘 - {date_str}", ""]

    if evaluation.get("status") == "degraded" or warnings:
        lines.extend([
            "⚠️ 数据降级说明",
            "  部分辅助数据暂不可用，以下有效行情与评估结果仍正常推送。",
        ])
        for warning in warnings[:3]:
            lines.append(f"  · {warning}")
        lines.append("")

    lines.append("📈 今日行情")
    indices = market_data.get("indices", {})
    index_count = 0
    if "error" not in market_data and isinstance(indices, dict) and indices:
        for code, data in indices.items():
            if not isinstance(data, dict):
                continue
            index_count += 1
            name = data.get("name") or code
            close = data.get("close")
            change_pct = data.get("change_pct")
            if isinstance(close, (int, float)):
                close_text = f"{close:.2f}"
            else:
                close_text = "--"
            if isinstance(change_pct, (int, float)):
                change_icon = "📈" if change_pct >= 0 else "📉"
                change_text = f" ({change_pct:+.2f}%)"
            else:
                change_icon = "▫️"
                change_text = ""
            lines.append(f"  {change_icon} {name}: {close_text}{change_text}")
    if index_count == 0:
        lines.append("  市场指数数据暂不可用")
    lines.append("")

    lines.append("💰 模拟账户盘后结算（账户口径）")
    if account_summary.get("status") == "ok":
        cash = float(account_summary.get("cash") or 0)
        market_value = float(account_summary.get("market_value") or 0)
        total_equity = float(account_summary.get("total_equity") or 0)
        total_return = float(account_summary.get("total_return") or 0)
        total_return_pct = float(account_summary.get("total_return_pct") or 0)
        realized_pnl = account_summary.get("realized_pnl")
        realized_text = f"{realized_pnl:+,.2f}元" if isinstance(realized_pnl, (int, float)) else "未能核实（缺少完整成交证据）"
        unrealized_pnl = float(account_summary.get("unrealized_pnl") or 0)
        transaction_costs = float(account_summary.get("total_transaction_costs") or 0)
        return_sign = "+" if total_return >= 0 else "-"
        lines.extend([
            f"  可用现金: ¥{cash:,.2f}",
            f"  持仓市值: ¥{market_value:,.2f}",
            f"  总资产: ¥{total_equity:,.2f}",
            (
                "  现金流及交易成本调整后总收益: "
                f"{return_sign}¥{abs(total_return):,.2f} ({total_return_pct:+.2f}%)"
            ),
            f"  已实现盈亏: {realized_text}｜未实现盈亏: {unrealized_pnl:+,.2f}元",
            (
                f"  当前持仓: {int(account_summary.get('position_count') or 0)}只｜"
                f"最大回撤: {float(account_summary.get('max_drawdown_pct') or 0):.2f}%｜"
                f"累计交易成本: ¥{transaction_costs:,.2f}"
            ),
        ])
    else:
        lines.append("  ⚠️ 本报告未保存账户结算快照，不得据此判断账户盈亏。")
    lines.append("")

    quality = evaluation.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    metrics = evaluation.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    win_rate = metrics.get("win_rate_pct", 0)
    avg_return = metrics.get("avg_return_pct", 0)
    win_rate = win_rate if isinstance(win_rate, (int, float)) else 0
    avg_return = avg_return if isinstance(avg_return, (int, float)) else 0
    stock_results = evaluation.get("stock_results", [])
    stock_results = stock_results if isinstance(stock_results, list) else []
    valid_results = [item for item in stock_results if isinstance(item, dict) and item.get("status") != "error"]
    win_statuses = {"hit", "hit_target"}
    loss_statuses = {"stopped", "stopped_out", "deep_loss", "expired"}
    closed_results = [item for item in valid_results if item.get("status") in win_statuses | loss_statuses]
    closed_count = int(metrics.get("closed_recommendations", len(closed_results)) or 0)
    active_count = int(metrics.get("active_recommendations", len(valid_results) - len(closed_results)) or 0)
    winning_count = int(
        metrics.get(
            "winning_recommendations",
            sum(1 for item in closed_results if item.get("status") in win_statuses),
        ) or 0
    )
    lines.extend([
        "📊 今日推荐信号评估结果（非账户收益）",
        f"  评估状态: {evaluation.get('status', 'unknown')}",
        f"  可评估信号: {quality.get('valid', 0)}/{quality.get('total', 0)}",
        f"  已结束样本胜率: {win_rate:.1f}%（{winning_count}/{closed_count}）",
        f"  进行中样本: {active_count}",
        f"  信号平均浮动收益: {avg_return:.2f}%",
        "  注: 信号收益按推荐进场价计算，不等于模拟账户收益。",
        "",
        "📈 推荐信号表现",
    ])

    stock_count = 0
    if stock_results:
        for stock in stock_results:
            if not isinstance(stock, dict):
                continue
            stock_count += 1
            status = stock.get("status", "")
            status_icon = (
                "✅" if status in win_statuses
                else "❌" if status in loss_statuses
                else "⏳" if status in {"active", "near_target", "profitable"}
                else "⚠️"
            )
            return_pct = stock.get("return_pct", 0)
            return_text = f"{return_pct:.2f}%" if isinstance(return_pct, (int, float)) else "--"
            lines.append(f"  {status_icon} {stock.get('code', '')} {stock.get('name', '')}: {return_text}")
    if stock_count == 0:
        lines.append("  暂无可评估个股")

    lines.extend(["", "📌 免责声明: 以上为研究信号，不构成买卖建议。"])
    return "\n".join(lines)


def format_json_report(
    date_str: str,
    report_type: str,
    market_data: Dict,
    news_list: List[Dict],
    sector_recommendations: List[Dict],
    stock_recommendations: List[Dict],
) -> Dict:
    """格式化JSON报告"""
    
    import hashlib
    import os
    from pathlib import Path
    from datetime import timezone
    from src.analysis.prompts import MORNING_ANALYSIS_PROMPT, AFTERNOON_ANALYSIS_PROMPT
    root = Path(__file__).resolve().parents[2]
    strategy_file = root / "config" / "strategy.yaml"
    strategy_version = "sha256:" + hashlib.sha256(strategy_file.read_bytes()).hexdigest()
    observed_at = datetime.now(timezone.utc).isoformat()
    snapshots = [{"source": "market_collector", "kind": "market_snapshot",
                  "available_at": observed_at, "fetched_at": observed_at,
                  "published_at": None, "snapshot": market_data,
                  "history_incomplete": bool(market_data.get("error")) or not bool(market_data.get("realtime_stock_prices"))}]
    snapshots.extend({"source": item.get("url") or item.get("source") or "unknown",
                      "kind": "news", "published_at": item.get("published_at"),
                      "available_at": item.get("available_at"), "fetched_at": item.get("fetched_at"),
                      "snapshot": item} for item in news_list if "error" not in item)
    return {
        "date": date_str,
        "type": report_type,
        "run_id": f"{date_str}-{report_type}",
        "created_at": observed_at,
        "data_as_of": observed_at,
        "strategy_version": strategy_version,
        "model_version": os.getenv("LLM_MODEL", "mimo-v2.5-pro"),
        "prompt_version": "sha256:" + hashlib.sha256((AFTERNOON_ANALYSIS_PROMPT if report_type == "afternoon" else MORNING_ANALYSIS_PROMPT).encode()).hexdigest(),
        "data_version": "evidence-snapshot-v1",
        "evidence_snapshots": snapshots,
        "market_regime": market_data.get("market_regime", "neutral"),
        "market_data": market_data,
        "sector_recommendations": sector_recommendations,
        "stock_recommendations": stock_recommendations,
        "news_sources": news_list,
        "risk_warnings": ["模拟研究系统，不构成投资建议；交易前仍须重新取价与风控"],
    }
