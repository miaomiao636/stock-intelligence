# -*- coding: utf-8 -*-
"""新闻采集模块"""

import os
from datetime import datetime
from typing import Dict, List, Optional

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False


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
        
        news_list = []
        for item in response.get("results", []):
            news_list.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("url", "").split("/")[2] if item.get("url") else "",
                "published_at": datetime.now().isoformat(),
                "summary": item.get("content", "")[:200],
                "fetched_at": datetime.now().isoformat(),
            })
        
        return news_list
    except Exception as e:
        return [{"error": str(e)}]


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
