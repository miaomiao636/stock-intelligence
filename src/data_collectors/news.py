# -*- coding: utf-8 -*-
"""新闻采集模块"""

import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from typing import Dict, List, Optional

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False


def normalize_news_item(item: Dict, now: datetime | None = None) -> Dict:
    """保留真实发布时间；搜索命中/抓取时间不是事件发生时间。"""
    now = now or datetime.now(timezone.utc)
    raw = item.get("published_date") or item.get("published_at")
    published = None
    if isinstance(raw, str):
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                published = parsedate_to_datetime(raw)
            except (ValueError, TypeError, OverflowError):
                pass
    if published and published.tzinfo is None:
        from zoneinfo import ZoneInfo
        published = published.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    age = now - published if published else None
    status = ("unknown_publication" if age is None else "future_publication" if age < timedelta(0)
              else "stale" if age > timedelta(days=7) else "recent")
    return {
        "title": str(item.get("title", ""))[:500], "url": str(item.get("url", ""))[:2000],
        "source": urlparse(str(item.get("url", ""))).hostname or "",
        "published_at": published.isoformat() if published else None,
        "publication_precision": "date" if isinstance(raw, str) and len(raw) == 10 else "timestamp_or_unknown",
        "fetched_at": now.isoformat(), "available_at": now.isoformat(),
        "summary": str(item.get("content", ""))[:200], "temporal_status": status,
        "event_eligible": status == "recent", "data_quality": "source_claimed_publication" if published else "unknown_publication",
    }


def search_news(query: str, max_results: int = 5, allow_sample: bool = False) -> List[Dict]:
    """搜索新闻"""
    api_key = os.getenv("TAVILY_API_KEY")
    
    if not api_key or not TAVILY_AVAILABLE:
        if allow_sample:
            # 返回示例数据
            return [
                {
                    "title": f"示例新闻: {query}",
                    "url": "https://example.com",
                    "source": "示例来源",
                    "published_at": datetime.now().isoformat(),
                    "summary": f"这是关于{query}的示例新闻摘要",
                    "fetched_at": datetime.now().isoformat(),
                    "data_quality": "sample",
                }
            ]
        else:
            # 真实模式，无新闻源
            return [{"error": "Tavily未配置，无法获取真实新闻", "data_quality": "error"}]
    
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        
        news_list = [normalize_news_item(item) for item in response.get("results", []) if isinstance(item, dict)]
        
        return news_list
    except Exception:
        return [{"error": "新闻源请求失败", "data_quality": "error"}]


def get_stock_news(stock_code: str, stock_name: str) -> List[Dict]:
    """获取个股新闻"""
    query = f"{stock_name} {stock_code} 股票 最新消息"
    return search_news(query)


def get_market_news() -> List[Dict]:
    """获取市场新闻"""
    query = "A股 市场 今日 最新消息"
    return search_news(query)


def get_policy_news() -> List[Dict]:
    """获取政策新闻"""
    query = "国务院 政策 经济 最新"
    return search_news(query)
