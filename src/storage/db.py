# -*- coding: utf-8 -*-
"""SQLite 数据库管理"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "stock_intelligence.db"


def get_connection():
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # 推荐记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id TEXT UNIQUE NOT NULL,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL,
            data_as_of TEXT,
            strategy_version TEXT,
            market_regime TEXT,
            sector_data TEXT,
            stock_data TEXT,
            news_data TEXT,
            raw_json TEXT,
            checksum TEXT
        )
    """)
    
    # 评估结果表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            recommendation_date TEXT NOT NULL,
            recommendation_type TEXT,
            metrics_data TEXT,
            raw_json TEXT,
            checksum TEXT
        )
    """)
    
    # 策略权重历史表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategy_weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            factor_weights TEXT,
            sector_allocations TEXT,
            raw_json TEXT
        )
    """)

    # A3: holdings / transactions 表已废弃（从未被读写，持仓/交易全走JSON文件）
    # 如需恢复双写，在此重建并接入 portfolio/executor

    # A3: 以下三表为审计副本（只写不读），读取走 JSON 文件：
    #   - recommendations: report_store.py 写入
    #   - evaluations: evaluator.py 写入
    #   - news_sources: news.py 写入

    # 推荐记录表（审计副本）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id TEXT UNIQUE NOT NULL,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL,
            data_as_of TEXT,
            strategy_version TEXT,
            market_regime TEXT,
            sector_data TEXT,
            stock_data TEXT,
            news_data TEXT,
            raw_json TEXT,
            checksum TEXT
        )
    """)

    # 评估结果表（审计副本）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            recommendation_date TEXT NOT NULL,
            recommendation_type TEXT,
            metrics_data TEXT,
            raw_json TEXT,
            checksum TEXT
        )
    """)

    # 新闻来源表（审计副本）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT UNIQUE NOT NULL,
            title TEXT,
            source TEXT,
            url TEXT,
            published_at TEXT,
            fetched_at TEXT,
            summary TEXT,
            impact_direction TEXT,
            affected_sectors TEXT,
            confidence TEXT,
            category TEXT,
            source_tier TEXT,
            is_duplicate INTEGER DEFAULT 0,
            canonical_source_url TEXT,
            related_source_urls TEXT,
            raw_json TEXT
        )
    """)

    # 创建索引以优化查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_date ON recommendations(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_date_type ON recommendations(date, type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_evaluations_date ON evaluations(date)")
    # 一个交易日/报告类型只有一份审计副本；清理旧版本留下的重复行后再加约束。
    cursor.execute("""
        DELETE FROM recommendations
        WHERE id NOT IN (SELECT MAX(id) FROM recommendations GROUP BY date, type)
    """)
    cursor.execute("""
        DELETE FROM evaluations
        WHERE id NOT IN (
            SELECT MAX(id) FROM evaluations
            GROUP BY date, recommendation_date, recommendation_type
        )
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendations_business_key ON recommendations(date, type)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluations_business_key ON evaluations(date, recommendation_date, recommendation_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news_sources(published_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_category ON news_sources(category)")

    conn.commit()
    conn.close()


def check_db():
    """检查数据库状态"""
    if not DB_PATH.exists():
        return {"status": "not_found", "message": "数据库不存在"}
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # 检查表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row["name"] for row in cursor.fetchall()]
    
    # 检查记录数
    counts = {}
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "status": "ok",
        "path": str(DB_PATH),
        "tables": tables,
        "counts": counts
    }
