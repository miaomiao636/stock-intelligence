# -*- coding: utf-8 -*-
"""防未来数据泄漏检查"""

from datetime import date, datetime
from typing import Dict, List, Optional


def check_data_availability(
    recommendation_time: datetime,
    data_timestamp: datetime,
    data_type: str = "market"
) -> bool:
    """检查数据在推荐时是否可见"""
    if data_type == "news":
        # 新闻以较晚时间为准
        return data_timestamp <= recommendation_time
    elif data_type == "market":
        # 行情以收盘数据为准
        return data_timestamp <= recommendation_time
    else:
        return data_timestamp <= recommendation_time


def filter_evaluation_data(
    recommendation: Dict,
    all_data: List[Dict]
) -> List[Dict]:
    """过滤掉推荐时不可见的数据"""
    rec_time = datetime.fromisoformat(recommendation.get("created_at", ""))
    
    filtered = []
    for data in all_data:
        data_time = datetime.fromisoformat(data.get("timestamp", data.get("fetched_at", "")))
        if check_data_availability(rec_time, data_time, data.get("type", "market")):
            filtered.append(data)
    
    return filtered


def validate_no_future_data(recommendation: Dict, evaluation: Dict) -> Dict:
    """验证结果只使用目标交易日及以前的数据，并且评估顺序合法。"""
    try:
        target_date = date.fromisoformat(evaluation.get("date", ""))
    except (TypeError, ValueError):
        return {"valid": False, "reason": "评估目标日期缺失或无效"}

    rec_date_text = recommendation.get("date")
    if not rec_date_text and recommendation.get("created_at"):
        rec_date_text = str(recommendation["created_at"])[:10]
    try:
        recommendation_date = date.fromisoformat(rec_date_text or "")
    except (TypeError, ValueError):
        return {"valid": False, "reason": "推荐日期缺失或无效"}
    if target_date < recommendation_date:
        return {"valid": False, "reason": "评估目标日期早于推荐日期"}

    created_at = recommendation.get("created_at")
    evaluated_at = evaluation.get("evaluated_at")
    if created_at and evaluated_at:
        try:
            rec_time = datetime.fromisoformat(created_at)
            eval_time = datetime.fromisoformat(evaluated_at)
            if (rec_time.tzinfo is None) != (eval_time.tzinfo is None):
                rec_time = rec_time.replace(tzinfo=None)
                eval_time = eval_time.replace(tzinfo=None)
            if eval_time < rec_time:
                return {"valid": False, "reason": "评估时间早于推荐时间"}
        except (TypeError, ValueError):
            return {"valid": False, "reason": "推荐或评估时间格式无效"}

    for stock in evaluation.get("stock_results", []):
        data_date_text = stock.get("data_date")
        if not data_date_text:
            continue
        try:
            data_date = date.fromisoformat(str(data_date_text)[:10])
        except (TypeError, ValueError):
            return {"valid": False, "reason": f"{stock.get('code', '')} 行情日期无效"}
        if data_date > target_date:
            return {
                "valid": False,
                "reason": f"{stock.get('code', '')} 行情日期晚于目标日期",
            }
        if stock.get("status") != "error" and data_date != target_date:
            return {
                "valid": False,
                "reason": f"{stock.get('code', '')} 未使用目标交易日行情",
            }

    benchmark_date_text = evaluation.get("benchmark_data_date")
    if benchmark_date_text:
        try:
            benchmark_date = date.fromisoformat(str(benchmark_date_text)[:10])
        except (TypeError, ValueError):
            return {"valid": False, "reason": "基准行情日期无效"}
        if benchmark_date > target_date:
            return {"valid": False, "reason": "基准行情日期晚于目标日期"}
        if benchmark_date != target_date:
            return {"valid": False, "reason": "基准行情不是目标交易日数据"}

    for news in evaluation.get("news_used", []):
        fetched_at = news.get("fetched_at")
        if not fetched_at:
            continue
        try:
            news_date = datetime.fromisoformat(fetched_at).date()
        except (TypeError, ValueError):
            return {"valid": False, "reason": "评估新闻时间格式无效"}
        if news_date > target_date:
            return {"valid": False, "reason": "评估新闻晚于目标日期"}

    return {"valid": True, "reason": "目标日期与数据时间校验通过"}
