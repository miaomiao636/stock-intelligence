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


def format_json_report(
    date_str: str,
    report_type: str,
    market_data: Dict,
    news_list: List[Dict],
    sector_recommendations: List[Dict],
    stock_recommendations: List[Dict],
) -> Dict:
    """格式化JSON报告"""
    
    return {
        "date": date_str,
        "type": report_type,
        "run_id": f"{date_str}-{report_type}",
        "created_at": datetime.now().isoformat(),
        "data_as_of": datetime.now().isoformat(),
        "strategy_version": "1.0",
        "market_regime": market_data.get("market_regime", "neutral"),
        "market_data": market_data,
        "sector_recommendations": sector_recommendations,
        "stock_recommendations": stock_recommendations,
        "news_sources": news_list,
        "risk_warnings": ["模拟研究系统，不构成投资建议；交易前仍须重新取价与风控"],
    }
