# -*- coding: utf-8 -*-
"""报告存储模块"""

import json
import hashlib
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# 数据目录
DATA_DIR = Path(__file__).parent.parent.parent / "data"


def save_report(report: Dict, report_type: str = "morning") -> Path:
    """保存报告到JSON文件和SQLite"""
    date_str = report.get("date", datetime.now().strftime("%Y-%m-%d"))
    report_dir = DATA_DIR / "recommendations" / date_str
    report_dir.mkdir(parents=True, exist_ok=True)
    
    report_file = report_dir / f"{report_type}.json"
    
    # 写入JSON
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    # 同步写入SQLite
    conn = None
    try:
        from src.storage.db import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        run_id = report.get("run_id", "")
        recommendation_id = report.get("recommendation_id") or f"{run_id}:{report_type}"
        cursor.execute(
            "DELETE FROM recommendations WHERE date=? AND type=? AND recommendation_id<>?",
            (date_str, report_type, recommendation_id),
        )

        # 写入recommendations表
        cursor.execute("""
            INSERT OR REPLACE INTO recommendations
            (recommendation_id, date, type, run_id, created_at, data_as_of,
             strategy_version, market_regime, sector_data, stock_data, news_data, raw_json, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            recommendation_id,
            date_str,
            report_type,
            run_id,
            report.get("created_at", ""),
            report.get("data_as_of", ""),
            report.get("strategy_version", ""),
            report.get("market_regime", ""),
            json.dumps(report.get("sector_recommendations", []), ensure_ascii=False),
            json.dumps(report.get("stock_recommendations", []), ensure_ascii=False),
            json.dumps(report.get("news_sources", []), ensure_ascii=False),
            json.dumps(report, ensure_ascii=False),
            get_report_checksum(report),
        ))

        # 写入news_sources表
        for news in report.get("news_sources", []):
            if "error" not in news:
                dedup_key = news.get("dedup_key", "")
                if not dedup_key:
                    dedup_key = hashlib.sha256(
                        (news.get("url", "") + news.get("title", "")).encode()
                    ).hexdigest()[:16]

                cursor.execute("""
                    INSERT OR IGNORE INTO news_sources
                    (dedup_key, title, source, url, published_at, fetched_at,
                     summary, impact_direction, affected_sectors, confidence,
                     category, source_tier, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    dedup_key,
                    news.get("title", ""),
                    news.get("source", ""),
                    news.get("url", ""),
                    news.get("published_at", ""),
                    news.get("fetched_at", ""),
                    news.get("summary", ""),
                    news.get("impact_direction", ""),
                    json.dumps(news.get("affected_sectors", []), ensure_ascii=False),
                    news.get("confidence", ""),
                    news.get("category", ""),
                    news.get("source_tier", ""),
                    json.dumps(news, ensure_ascii=False),
                ))

        conn.commit()
    except Exception as e:
        print(f"  ⚠️  SQLite写入失败: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    
    return report_file


def load_report(date_str: str, report_type: str = "morning") -> Optional[Dict]:
    """加载报告"""
    from src.utils.security import validate_date_str, validate_report_type
    validate_date_str(date_str, "date_str")  # P0-4: 防路径穿越
    validate_report_type(report_type)
    report_file = DATA_DIR / "recommendations" / date_str / f"{report_type}.json"

    if not report_file.exists():
        return None

    try:
        with open(report_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  [WARN] 报告文件损坏: {report_file}: {e}")
        return None


def get_report_checksum(report: Dict) -> str:
    """计算报告校验和"""
    report_str = json.dumps(report, sort_keys=True)
    return hashlib.md5(report_str.encode()).hexdigest()


def save_run_artifact(
    run_id: str,
    input_snapshot: Dict,
    source_status: Dict,
    report: Dict,
    errors: list,
) -> Path:
    """保存运行产物"""
    run_dir = DATA_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 同一交易日补偿重跑前保留上一版证据，避免09:35覆盖08:45故障现场。
    artifact_names = ("input_snapshot.json", "source_status.json", "report.json", "errors.log")
    if (run_dir / "report.json").exists():
        archive_name = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:6]}"
        archive_dir = run_dir / "attempts" / archive_name
        archive_dir.mkdir(parents=True, exist_ok=False)
        for name in artifact_names:
            source = run_dir / name
            if source.exists():
                shutil.copy2(source, archive_dir / name)
    
    # 保存输入快照
    with open(run_dir / "input_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(input_snapshot, f, ensure_ascii=False, indent=2)
    
    # 保存数据源状态
    with open(run_dir / "source_status.json", "w", encoding="utf-8") as f:
        json.dump(source_status, f, ensure_ascii=False, indent=2)
    
    # 保存报告
    with open(run_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    # 保存错误日志
    with open(run_dir / "errors.log", "w", encoding="utf-8") as f:
        for error in errors:
            f.write(f"{error}\n")
    
    return run_dir
