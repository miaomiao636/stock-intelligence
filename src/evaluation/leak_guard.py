# -*- coding: utf-8 -*-
"""防未来数据泄漏检查"""

from datetime import datetime
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
    """验证评估没有使用未来数据"""
    rec_time = datetime.fromisoformat(recommendation.get("created_at", ""))
    eval_time = datetime.fromisoformat(evaluation.get("evaluated_at", ""))
    
    # 评估时间必须在推荐时间之后
    if eval_time < rec_time:
        return {
            "valid": False,
            "reason": "评估时间早于推荐时间",
        }
    
    # 检查所有数据时间戳
    for news in evaluation.get("news_used", []):
        news_time = datetime.fromisoformat(news.get("fetched_at", ""))
        if news_time > eval_time:
            return {
                "valid": False,
                "reason": f"新闻数据时间({news_time})晚于评估时间({eval_time})",
            }
    
    return {"valid": True, "reason": "数据时间校验通过"}
