# -*- coding: utf-8 -*-
"""FastAPI后端"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Literal
from zoneinfo import ZoneInfo

import os
import math
import logging
import threading
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from src.models import ApiResponse

app = FastAPI(title="Stock Intelligence Dashboard")
logger = logging.getLogger("stock_intelligence.feishu")
ANALYSIS_REGEN_LOCK = threading.Lock()
STRATEGY_CONFIG_LOCK = threading.Lock()

# API Key 鉴权：读取接口可直接看盘；所有会改变状态的接口仍只接受 X-API-Key。
API_KEY = os.getenv("API_KEY", "")

# 飞书回调用Verification Token独立鉴权。
from fastapi.middleware.cors import CORSMiddleware  # P2: 原为死导入，保留以便后续按需 app.add_middleware 启用

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    callback_path = "/api/feishu/callback"
    if request.url.path == callback_path:
        return await call_next(request)
    is_write = request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}
    if is_write and not API_KEY:
        return JSONResponse(status_code=503, content=ApiResponse.fail("写接口已安全关闭：请先配置 API_KEY"))
    if is_write and API_KEY:
        provided = request.headers.get("X-API-Key")
        if provided != API_KEY:
            return JSONResponse(status_code=401, content=ApiResponse.fail("未授权：API Key 无效或缺失"))
    return await call_next(request)

# Disable caching for all API responses
@app.middleware("http")
async def add_no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content=ApiResponse.fail(f"服务器内部错误: {str(exc)}")
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiResponse.fail(exc.detail)
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """P0-4: 参数校验失败（如非法 date_str）返回 400，而非 500"""
    return JSONResponse(
        status_code=400,
        content=ApiResponse.fail(f"参数校验失败: {str(exc)}")
    )


# 项目根目录
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"


# API模型
class AccountUpdate(BaseModel):
    cash: float


class PaperTrade(BaseModel):
    code: str
    name: str
    sector: str
    action: str  # buy/sell
    quantity: int
    price: float
    horizon: str
    reason: str


class OrderDecision(BaseModel):
    action: str
    actor: str = "dashboard_user"
    event_id: Optional[str] = None


# 健康检查
@app.get("/api/health")
def health_check():
    return ApiResponse.ok({
        "status": "ok",
        "version": "1.0.0",
        "endpoints": len(app.routes),
        "paper_trading_enabled": os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true",
        "api_writes_enabled": bool(API_KEY),
    })


# 首页
@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = PROJECT_ROOT / "dashboard" / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Dashboard not found</h1>"


# API: 获取账户信息
@app.get("/api/account")
async def get_account():
    from src.paper_trading.trading_service import TradingService
    return TradingService().get_account()


# API: 修改可用现金（不重置持仓、订单或历史记录）
@app.put("/api/account")
async def update_account(data: AccountUpdate):
    from src.paper_trading.trading_service import TradingService
    account = TradingService().update_available_cash(data.cash)
    return {"success": True, "account": account}


# API: 重置账户（危险操作）
@app.post("/api/account/reset")
async def reset_account(confirm: bool = False, initial_cash: Optional[float] = None):
    """重置整个账户（归档旧数据，创建新账户）"""
    if not confirm:
        return ApiResponse.fail("需要确认：此操作将归档所有数据并创建新账户，添加 ?confirm=true 确认")

    from src.strategy.position_limits import get_initial_cash
    configured_cash = get_initial_cash()
    initial_cash = configured_cash if initial_cash is None else float(initial_cash)
    if initial_cash != configured_cash:
        return ApiResponse.fail(f"当前正式模拟账户初始资金固定为¥{configured_cash:,.0f}")
    import shutil
    from src.paper_trading.trading_service import TradingService
    archive_dir = DATA_DIR / "archive" / f"reset_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    db_file = DATA_DIR / "stock_intelligence.db"
    if db_file.exists():
        shutil.copy2(db_file, archive_dir / "stock_intelligence.db")
    result = TradingService().initialize_account(configured_cash, reset=True)
    return {"success": True, "archive_dir": str(archive_dir), "account": result}


# API: 获取持仓
@app.get("/api/positions")
async def get_positions(date_str: Optional[str] = None):
    from src.paper_trading.trading_service import TradingService
    return TradingService().get_positions(date_str or date.today().isoformat())


# API: 获取今日推荐（自动补全实时价格，自动回退到最近可用日期）
def _recommendation_dates(target: date) -> List[date]:
    """Discover stored report days; a long holiday is not a missing-data error.

    Do not infer that a stored report day was a trading day. The UI presents the
    actual report date, and future/sidecar directories are never candidates.
    """
    from src.reporting import report_store
    days = {target - timedelta(days=offset) for offset in range(4)}
    directory = report_store.DATA_DIR / "recommendations"
    if directory.is_dir():
        for child in directory.iterdir():
            if not child.is_dir():
                continue
            try:
                day = date.fromisoformat(child.name)
            except ValueError:
                continue
            if day.isoformat() == child.name and day <= target:
                days.add(day)
    return sorted(days, reverse=True)


@app.get("/api/recommendation")
async def get_recommendation(date_str: Optional[str] = None):
    from src.reporting.report_store import load_report

    target = date_str or date.today().isoformat()
    report = None
    for day in _recommendation_dates(date.fromisoformat(target)):
        report = load_report(day.isoformat(), "morning")
        if report:
            break

    if not report:
        return ApiResponse.fail("No recommendation found")
    _enrich_stock_prices(report)
    return report


@app.post("/api/recommendation/regenerate")
def regenerate_morning_analysis():
    """手动重新生成当天AI分析；不创建订单，也不直接成交。"""
    if not ANALYSIS_REGEN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="分析任务正在运行，请稍后再试")
    try:
        from src.orchestrator import run_morning_pipeline
        result = run_morning_pipeline(dry_run=False, force=True, date_str=date.today().isoformat())
        llm_status = result.get("source_status", {}).get("llm", "unknown")
        return ApiResponse.ok({
            "status": result.get("status", "unknown"),
            "llm_status": llm_status,
            "errors": result.get("errors", []),
            "message": "AI分析已恢复" if llm_status == "success" else "重新分析完成，但LLM仍处于降级状态",
        })
    finally:
        ANALYSIS_REGEN_LOCK.release()


# API: 获取所有时段推荐（自动查找最近可用数据）
@app.get("/api/recommendations/all")
async def get_all_recommendations():
    from src.reporting.report_store import load_report, get_report_checksum

    today = date.today()
    result = []

    for target in _recommendation_dates(today):

        # 收盘分析
        closing = load_report(target.isoformat(), "closing")
        if closing:
            revision = get_report_checksum(closing)
            _enrich_stock_prices(closing)
            result.append({
                "revision": revision,
                "label": f"{target.isoformat()} 收盘分析 (16:45)",
                "period": f"{target.isoformat()}_closing",
                "time": "16:45",
                "date": target.isoformat(),
                "type": "closing",
                "data": closing,
            })

        # 13:15盘中复核
        afternoon = load_report(target.isoformat(), "afternoon")
        if afternoon:
            revision = get_report_checksum(afternoon)
            _enrich_stock_prices(afternoon)
            result.append({
                "revision": revision,
                "label": f"{target.isoformat()} 下午复核 (13:15)",
                "period": f"{target.isoformat()}_afternoon",
                "time": "13:15",
                "date": target.isoformat(),
                "type": "afternoon",
                "data": afternoon,
            })

        # Try morning
        morning = load_report(target.isoformat(), "morning")
        if morning:
            revision = get_report_checksum(morning)
            _enrich_stock_prices(morning)
            result.append({
                "revision": revision,
                "label": f"{target.isoformat()} 盘前推荐 (08:45)",
                "period": f"{target.isoformat()}_morning",
                "time": "08:45",
                "date": target.isoformat(),
                "type": "morning",
                "data": morning,
            })

        # If we found at least one report, stop searching
        if result:
            break

    # 同一天严格按盘前、下午、收盘排列。
    type_order = {"morning": 0, "afternoon": 1, "closing": 2}
    result.sort(key=lambda r: (r["date"], type_order.get(r["type"], 99)))
    return result


# 板块→ETF/平替映射（常用A股ETF和同板块低价股）
SECTOR_ETF_MAP = {
    "AI算力": {
        "etf": [{"code": "515070", "name": "人工智能ETF", "price_range": "1-3元"}],
        "alternatives": [
            {"code": "000977", "name": "浪潮信息", "reason": "同板块，价格更低"},
            {"code": "603019", "name": "中科曙光", "reason": "同板块，价格适中"},
        ]
    },
    "半导体": {
        "etf": [{"code": "512480", "name": "半导体ETF", "price_range": "1-2元"}],
        "alternatives": [
            {"code": "603501", "name": "豪威集团", "reason": "半导体设计龙头"},
        ]
    },
    "新能源车": {
        "etf": [{"code": "515030", "name": "新能源车ETF", "price_range": "1-2元"}],
        "alternatives": []
    },
    "消费": {
        "etf": [{"code": "159928", "name": "消费ETF", "price_range": "1-2元"}],
        "alternatives": []
    },
    "医药": {
        "etf": [{"code": "512010", "name": "医药ETF", "price_range": "1-2元"}],
        "alternatives": []
    },
    "金融": {
        "etf": [{"code": "510230", "name": "金融ETF", "price_range": "1-2元"}],
        "alternatives": [
            {"code": "601318", "name": "中国平安", "reason": "金融龙头"},
            {"code": "600036", "name": "招商银行", "reason": "银行龙头"},
        ]
    },
    "消费电子": {
        "etf": [{"code": "159732", "name": "消费电子ETF", "price_range": "1-2元"}],
        "alternatives": []
    },
    "周期": {
        "etf": [{"code": "510500", "name": "中证500ETF", "price_range": "5-8元"}],
        "alternatives": []
    },
}


# API: 获取股票平替建议（含真实价格）
@app.get("/api/stock/alternatives/{code}")
async def get_stock_alternatives(code: str, sector: str = "", max_price: float = 100):
    from src.reporting.report_store import load_report
    # Find stock's sector from today's recommendation
    report = load_report(date.today().isoformat(), "morning")
    if not report:
        # Try previous days
        from datetime import timedelta
        for offset in range(1, 4):
            report = load_report((date.today() - timedelta(days=offset)).isoformat(), "morning")
            if report:
                break
    stock_sector = sector
    if report:
        for s in report.get("stock_recommendations", []):
            if s.get("code") == code:
                stock_sector = s.get("sector", sector)
                break

    mapping = SECTOR_ETF_MAP.get(stock_sector, {})
    etfs = [dict(e) for e in mapping.get("etf", [])]
    alternatives = [dict(a) for a in mapping.get("alternatives", []) if a["code"] != code]

    # Fetch real prices for all alternatives via Tencent API
    all_codes = []
    for e in etfs:
        c = e["code"]
        all_codes.append(("sh" if c.startswith(("5", "6")) else "sz") + c)
    for a in alternatives:
        c = a["code"]
        all_codes.append(("sh" if c.startswith(("6",)) else "sz") + c)

    if all_codes:
        try:
            import urllib.request
            url = "https://qt.gtimg.cn/q=" + ",".join(all_codes)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=5) as resp:
                raw = resp.read().decode("gbk", errors="ignore")
            price_map = {}
            name_map = {}
            for line in raw.split(";"):
                line = line.strip()
                if "~" not in line:
                    continue
                parts = line.split("~")
                if len(parts) > 5:
                    try:
                        c = parts[2]
                        price_map[c] = float(parts[3])
                        name_map[c] = parts[1]
                    except (ValueError, IndexError):
                        pass
            # Apply prices to etfs
            for e in etfs:
                e["price"] = price_map.get(e["code"])
                if name_map.get(e["code"]):
                    e["verified_name"] = name_map[e["code"]]
            # Apply prices to alternatives
            for a in alternatives:
                a["price"] = price_map.get(a["code"])
                if name_map.get(a["code"]):
                    a["verified_name"] = name_map[a["code"]]
        except Exception as e:
            print(f"  Alternative price fetch failed: {e}")

    return {
        "code": code,
        "sector": stock_sector,
        "max_price": max_price,
        "etf_alternatives": etfs,
        "stock_alternatives": alternatives,
    }


def _enrich_stock_prices(report: dict):
    """附加最新行情，但绝不改写历史信号价、目标价或止损价。"""
    stocks = report.get("stock_recommendations", [])
    if not stocks:
        return
    try:
        from src.data_collectors.realtime_prices import fetch_realtime_prices
        quotes = fetch_realtime_prices([s.get("code") for s in stocks if s.get("code")])
        for stock in stocks:
            quote = quotes.get(stock.get("code"))
            if quote:
                stock["latest_price"] = quote["price"]
                stock["latest_quote_time"] = quote.get("quote_time")
                stock["latest_quote_source"] = quote.get("source")
    except Exception as e:
        print(f"  Price enrichment failed: {e}")


def _collect_live_quote_codes(service, lookback_days: int = 4) -> List[str]:
    """收集页面会展示的推荐与持仓代码，避免开放任意代码查询。"""
    from src.reporting.report_store import load_report

    codes = set()
    for offset in range(max(1, lookback_days)):
        target = (date.today() - timedelta(days=offset)).isoformat()
        for report_type in ("morning", "afternoon", "closing"):
            report = load_report(target, report_type)
            if not report:
                continue
            for stock in report.get("stock_recommendations", []):
                code = str(stock.get("code") or "").strip()
                if code.isdigit() and len(code) == 6:
                    codes.add(code)
    try:
        current_positions = service.get_positions(date.today().isoformat())
    except Exception:
        current_positions = []
    for position in current_positions:
        code = str(position.get("code") or "").strip()
        if code.isdigit() and len(code) == 6:
            codes.add(code)
    return sorted(codes)[:100]


def _market_session_status(now: datetime) -> str:
    """返回前端展示用的A股时段，不参与任何交易决策。"""
    if now.weekday() >= 5:
        return "closed"
    current = now.time().replace(tzinfo=None)
    if time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0):
        return "trading"
    if current < time(9, 30):
        return "pre_open"
    if time(11, 30) < current < time(13, 0):
        return "lunch_break"
    return "closed"


@app.get("/api/live/quotes")
def get_live_quotes():
    """返回页面每60秒使用的只读行情；绝不写账本、创建订单或触发分析。"""
    from src.data_collectors.market_data import get_realtime_market_overview
    from src.data_collectors.realtime_prices import fetch_realtime_prices
    from src.paper_trading.trading_service import TradingService

    service = TradingService()
    codes = _collect_live_quote_codes(service)
    quotes = fetch_realtime_prices(codes)
    positions = []
    live_market_value = 0.0
    try:
        stored_positions = service.get_positions(date.today().isoformat())
    except Exception:
        stored_positions = []
    for original in stored_positions:
        position = dict(original)
        quote = quotes.get(position.get("code"), {})
        current_price = float(quote.get("price") or position.get("current_price") or 0)
        quantity = int(position.get("quantity") or 0)
        avg_cost = float(position.get("avg_cost") or 0)
        market_value = round(current_price * quantity, 2)
        pnl_amount = round((current_price - avg_cost) * quantity, 2)
        pnl_pct = round((current_price / avg_cost - 1) * 100, 2) if avg_cost else 0.0
        position.update({
            "current_price": current_price,
            "market_value": market_value,
            "unrealized_pnl": pnl_amount,
            "unrealized_pnl_pct": pnl_pct,
            "latest_quote_time": quote.get("quote_time"),
            "latest_quote_source": quote.get("source"),
        })
        live_market_value += market_value
        positions.append(position)

    try:
        account = service.get_account()
        performance = service.get_performance_metrics()
        live_total_equity = round(float(account.get("cash") or 0) + live_market_value, 2)
        principal = float(performance.get("effective_principal") or account.get("initial_cash") or 0)
        net_return = round(live_total_equity - principal, 2)
        net_return_pct = round(net_return / principal * 100, 4) if principal else 0.0
        live_account = {
            **performance,
            "cash": float(account.get("cash") or 0),
            "market_value": round(live_market_value, 2),
            "total_equity": live_total_equity,
            "net_return": net_return,
            "net_return_pct": net_return_pct,
            "net_return_after_costs": net_return,
            "net_return_after_costs_pct": net_return_pct,
        }
    except RuntimeError:
        # 新部署尚未初始化模拟账户时，实时指数与推荐价格仍必须可用。
        live_account = None

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return {
        "fetched_at": now.isoformat(),
        "market_session": _market_session_status(now),
        "refresh_after_seconds": 60,
        "market_data": get_realtime_market_overview(),
        "quotes": quotes,
        "positions": positions,
        "account": live_account,
    }


# API: 获取评估结果
@app.get("/api/evaluation")
async def get_evaluation(date_str: Optional[str] = None):
    from src.utils.security import validate_date_str
    target = date_str or date.today().isoformat()
    validate_date_str(target, "date_str")  # P0-4: 防路径穿越
    eval_file = DATA_DIR / "evaluations" / target / "closing.json"
    if not eval_file.exists():
        return ApiResponse.fail("No evaluation found")
    import json
    with open(eval_file) as f:
        return json.load(f)


# API: 获取收益
@app.get("/api/performance")
async def get_performance(date_str: Optional[str] = None):
    from src.paper_trading.trading_service import TradingService
    service = TradingService()
    account = service.get_account()
    metrics = service.get_performance_metrics()
    return {
        **metrics,
        "cash": account["cash"],
        "market_value": account["market_value"],
        "realized_pnl": account["realized_pnl"],
        "unrealized_pnl": account["unrealized_pnl"],
        "positions_count": len(service.get_positions(date_str)),
    }


# API: 获取推荐详情
@app.get("/api/stock/{code}")
async def get_recommendation_detail(code: str):
    from src.reporting.report_store import load_report
    # 尝试今天和昨天的推荐
    for date_offset in [0, 1]:
        from datetime import timedelta
        target_date = (date.today() - timedelta(days=date_offset)).isoformat()
        report = load_report(target_date, "morning")
        if report:
            for stock in report.get("stock_recommendations", []):
                if stock.get("code") == code:
                    return stock
    
    raise HTTPException(status_code=404, detail=f"Stock {code} not found")


# API: 获取历史推荐（增强版，含跟踪数据）
@app.get("/api/recommendations")
async def get_recommendations(limit: int = 30, with_tracking: bool = False):
    import json
    rec_dir = DATA_DIR / "recommendations"
    if not rec_dir.exists():
        return []

    # Load tracking data if requested
    tracking_map = {}
    if with_tracking:
        track_file = DATA_DIR / "tracker"
        if track_file.exists():
            for tf in sorted(track_file.glob("*.json"), reverse=True)[:5]:
                try:
                    with open(tf) as f:
                        td = json.load(f)
                    for t in td.get("tracks", []):
                        key = f"{t.get('recommendation_date', '')}_{t.get('code', '')}"
                        tracking_map[key] = t
                except Exception as e:
                    print(f"  ⚠️ 加载跟踪数据失败: {e}")  # B1: 原为静默pass

    results = []
    for date_dir in sorted(rec_dir.iterdir(), reverse=True):
        if date_dir.is_dir():
            morning_file = date_dir / "morning.json"
            if morning_file.exists():
                with open(morning_file) as f:
                    data = json.load(f)

                    # Enrich stocks with tracking data
                stocks = data.get("stock_recommendations", [])
                enriched_stocks = []
                for s in stocks:
                    key = f"{date_dir.name}_{s.get('code', '')}"
                    track = tracking_map.get(key)
                    enriched = dict(s)
                    if track:
                        enriched["tracking"] = {
                            "current_price": track.get("current_price"),
                            "planned_entry_price": track.get("planned_entry_price"),
                            "entry_price": track.get("entry_price"),
                            "entry_date": track.get("entry_date"),
                            "actual_return_pct": track.get("actual_return_pct"),
                            "status": track.get("status", ""),
                            "execution_status": track.get("execution_status", "unknown"),
                            "return_basis": track.get("return_basis", ""),
                            "data_date": track.get("data_date", ""),
                            "failure_reason": track.get("failure_reason", ""),
                            "is_met_expectation": track.get("is_met_expectation", False),
                            "is_failed": track.get("is_failed", False),
                            "holding_days": track.get("holding_days", 0),
                        }
                    enriched_stocks.append(enriched)

                results.append({
                    "date": date_dir.name,
                    "type": "morning",
                    "sector_count": len(data.get("sector_recommendations", [])),
                    "stock_count": len(stocks),
                    "stocks": enriched_stocks,
                    "sectors": data.get("sector_recommendations", []),
                })
            if len(results) >= limit:
                break

    return results


# API: 推荐跟踪数据
@app.get("/api/tracking")
def get_tracking(date_str: Optional[str] = None):
    """Get recommendation tracking data"""
    from src.tracking.tracker import RecommendationTracker
    tracker = RecommendationTracker()

    if date_str:
        data = tracker.get_tracking(date_str)
    else:
        data = tracker.get_latest_tracking()

    if not data:
        return {**ApiResponse.fail("No tracking data found"), "tracks": [], "summary": {}}
    from src.research.health import tracking_freshness
    data = dict(data)
    data["freshness"] = tracking_freshness(data)
    data["summary"] = dict(data.get("summary") or {})
    data["summary"].setdefault("execution_coverage_pct", None)
    return data


# API: 周回顾
@app.get("/api/tracking/weekly")
def get_weekly_review(weeks: int = 4):
    """Get weekly performance reviews"""
    from src.tracking.tracker import RecommendationTracker
    tracker = RecommendationTracker()
    return tracker.get_weekly_review(weeks)


# API: 改进建议
@app.get("/api/tracking/suggestions")
def get_tracking_suggestions():
    """Get improvement suggestions based on tracking data"""
    from src.tracking.tracker import RecommendationTracker
    tracker = RecommendationTracker()
    return tracker.get_improvement_suggestions()


# API: 自动操盘日志
@app.get("/api/paper/auto-trades")
def get_auto_trades(limit: int = 30):
    """Get auto-trade logs"""
    from src.paper_trading.trading_service import TradingService
    # Preserve the legacy field for audit; display accounting-correct both-side costs.
    limit = max(1, min(limit, 1000))
    rows = TradingService().get_trade_accounting()["trades"]
    return [{**row, "legacy_realized_pnl": row.get("realized_pnl"),
             "realized_pnl": row.get("net_realized_pnl")} for row in reversed(rows[-limit:])]


# API: 自动操盘单日日志
@app.get("/api/paper/auto-trades/{date_str}")
def get_auto_trade_log(date_str: str):
    """Get auto-trade log for a specific date"""
    from src.paper_trading.trading_service import TradingService
    date.fromisoformat(date_str)
    rows = TradingService().get_trade_accounting()["trades"]
    log = [{**row, "legacy_realized_pnl": row.get("realized_pnl"),
            "realized_pnl": row.get("net_realized_pnl")} for row in rows if row.get("trade_date") == date_str]
    if not log:
        return ApiResponse.fail(f"No trade log for {date_str}")
    return log


# API: 手动触发自动操盘
@app.post("/api/paper/auto-trade")
async def trigger_auto_trade():
    """Manually trigger auto-trading based on latest recommendation"""
    return ApiResponse.fail("旧自动交易入口已停用；请使用09:35最终计划和飞书否决流程")


# API: 更新持仓价格
@app.post("/api/paper/update-positions")
async def update_positions():
    """Mark-to-market all positions"""
    from src.paper_trading.workflow import PaperTradingWorkflow
    return PaperTradingWorkflow().intraday_check()


# API: 检查卖出信号
@app.get("/api/paper/sell-signals")
def get_sell_signals():
    """Check for sell signals in current positions"""
    return ApiResponse.fail("旧卖出信号入口已停用；盘中风险事件统一进入飞书否决流程")


# API: 获取策略建议
@app.get("/api/advisories")
async def get_advisories():
    from src.strategy.store import StrategyStore
    store = StrategyStore()
    return store.get_pending_advisories()


# API: 执行模拟交易
@app.post("/api/paper/execute")
async def execute_paper_trade():
    return ApiResponse.fail("旧直接成交入口已停用；请使用 /api/paper/execute-due")


@app.get("/api/paper/orders")
def get_safe_orders(status: Optional[str] = None):
    """读取SQLite唯一账本中的订单。"""
    from src.paper_trading.trading_service import TradingService
    statuses = [status] if status else None
    return TradingService().ledger.list_orders(statuses=statuses)


@app.post("/api/paper/open")
def prepare_open_orders(date_str: Optional[str] = None):
    """09:35重新取价、创建订单并发送飞书交互卡片。"""
    from src.analysis.recovery import recover_degraded_morning_report
    from src.paper_trading.workflow import PaperTradingWorkflow
    from src.reporting.report_store import load_report
    target = date_str or date.today().isoformat()
    report = load_report(target, "morning")
    if not report:
        return ApiResponse.fail(f"未找到 {target} 的盘前报告")
    recovery = recover_degraded_morning_report(target, report=report)
    report = recovery.get("report") or report
    return PaperTradingWorkflow().prepare_final_orders(report, report.get("source_status", {}))


@app.post("/api/paper/orders/{order_id}/decision")
def decide_order(order_id: str, decision: OrderDecision):
    from src.paper_trading.trading_service import TradingService
    return TradingService().record_decision(
        order_id,
        decision.action,
        actor=decision.actor,
        event_id=decision.event_id,
    )


@app.post("/api/paper/execute-due")
def execute_due_orders():
    from src.paper_trading.workflow import PaperTradingWorkflow
    return PaperTradingWorkflow().execute_due_orders()


@app.post("/api/paper/intraday")
def run_intraday_check():
    from src.paper_trading.workflow import PaperTradingWorkflow
    return PaperTradingWorkflow().intraday_check()


@app.post("/api/feishu/callback")
async def feishu_callback(request: Request):
    """飞书卡片回调；此路由用Verification Token独立鉴权。"""
    from src.notifier.feishu import FeishuNotifier
    from src.notifier.feishu_actions import process_card_action

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    notifier = FeishuNotifier()
    if payload.get("type") == "url_verification":
        logger.info("feishu_callback type=url_verification")
        return {"challenge": payload.get("challenge")}
    action = notifier.parse_card_action(payload)
    if (
        action.get("action") == "ping"
        and notifier.is_legacy_card_callback(payload, dict(request.headers))
    ):
        logger.info("feishu_callback verified=legacy action=ping")
        return {"toast": {"type": "success", "content": "飞书回调链路正常"}}
    if not notifier.verify_callback(payload):
        logger.warning("feishu_callback verified=false")
        raise HTTPException(status_code=401, detail="飞书回调Token无效")
    action_name = action.get("action")
    logger.info(
        "feishu_callback verified=true action=%s has_order_id=%s has_event_id=%s",
        action_name if action_name in {"ping", "confirm", "veto", "pause_day"} else "unknown",
        bool(action.get("order_id")),
        bool(action.get("event_id")),
    )
    return process_card_action(action)


# API: 获取策略参数
@app.get("/api/strategy/params")
async def get_strategy_params():
    import yaml
    config_file = PROJECT_ROOT / "config" / "strategy.yaml"
    if not config_file.exists():
        return ApiResponse.fail("Strategy config not found")
    with open(config_file) as f:
        strategy = yaml.safe_load(f)
    return strategy


# API: 更新策略参数（实时调控）
@app.put("/api/strategy/params")
async def update_strategy_params(data: dict):
    import yaml
    config_file = PROJECT_ROOT / "config" / "strategy.yaml"
    if not config_file.exists():
        return ApiResponse.fail("Strategy config not found")

    def bounded_number(value, name, minimum=0.0, maximum=1.0):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{name}必须是数字")
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise HTTPException(status_code=400, detail=f"{name}必须在{minimum}到{maximum}之间")
        return number

    with STRATEGY_CONFIG_LOCK:
        with open(config_file, encoding="utf-8") as f:
            strategy = yaml.safe_load(f) or {}

        if "factor_weights" in data:
            for name, value in (data.get("factor_weights") or {}).items():
                strategy.setdefault("factor_weights", {})[name] = bounded_number(
                    value, f"factor_weights.{name}"
                )

        if "sector_allocations" in data:
            for name, values in (data.get("sector_allocations") or {}).items():
                if name in strategy.get("sector_allocations", {}) and isinstance(values, dict):
                    if "allocation" in values:
                        values = dict(values)
                        values["allocation"] = bounded_number(
                            values["allocation"], f"sector_allocations.{name}.allocation"
                        )
                    strategy["sector_allocations"][name].update(values)

        if "custom_params" in data:
            allowed_ratios = {
                "short_ratio", "medium_ratio", "long_ratio",
                "a_share_ratio", "hk_ratio", "us_ratio",
            }
            for name, value in (data.get("custom_params") or {}).items():
                if name == "max_stock_price":
                    value = bounded_number(value, name, minimum=1.0, maximum=1000.0)
                elif name in allowed_ratios:
                    value = bounded_number(value, name)
                else:
                    raise HTTPException(status_code=400, detail=f"不支持的策略参数: {name}")
                strategy.setdefault("custom_params", {})[name] = value

        temp_file = config_file.with_suffix(".yaml.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(strategy, f, allow_unicode=True, default_flow_style=False)
        os.replace(temp_file, config_file)

    return {"status": "ok", "strategy": strategy}


# API: 资产分析数据（同步I/O，用def避免阻塞事件循环）
@app.get("/api/analysis/asset")
def get_asset_analysis():
    from src.paper_trading.trading_service import TradingService

    service = TradingService()
    account = service.get_account()
    snapshots = service.ledger.snapshots()
    adjustments = service.ledger.cash_adjustments()
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(timezone)

    def parse_timestamp(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    adjustment_rows = sorted(
        (
            (parse_timestamp(row["created_at"]), float(row["amount"]))
            for row in adjustments
        ),
        key=lambda item: item[0],
    )
    initial_cash = float(account["initial_cash"])

    def principal_at(cutoff):
        return initial_cash + sum(
            amount for created_at, amount in adjustment_rows if created_at <= cutoff
        )

    def cash_flow_between(start, end):
        return sum(
            amount
            for created_at, amount in adjustment_rows
            if (start is None or created_at > start) and created_at <= end
        )

    by_day = {}
    for snapshot in snapshots:
        captured_at = parse_timestamp(snapshot["captured_at"])
        day = captured_at.date().isoformat()
        existing = by_day.get(day)
        if existing is None or captured_at > existing["captured_at"]:
            by_day[day] = {
                "captured_at": captured_at,
                "total_equity": float(snapshot["total_equity"]),
            }
    by_day[now.date().isoformat()] = {
        "captured_at": now,
        "total_equity": float(account["total_equity"]),
    }

    daily = []
    previous_equity = None
    previous_at = None
    for day, point in sorted(by_day.items()):
        captured_at = point["captured_at"]
        equity = point["total_equity"]
        effective_principal = principal_at(captured_at)
        if previous_equity is None:
            opening_equity = effective_principal
            external_cash_flow = 0.0
            invested_capital = effective_principal
            period_profit = equity - effective_principal
        else:
            opening_equity = previous_equity
            external_cash_flow = cash_flow_between(previous_at, captured_at)
            invested_capital = opening_equity + external_cash_flow
            period_profit = equity - invested_capital
        return_pct = (
            period_profit / invested_capital * 100 if invested_capital else 0.0
        )
        total_return = equity - effective_principal
        daily.append({
            "date": day,
            "total_equity": equity,
            "opening_equity": round(opening_equity, 2),
            "invested_capital": round(invested_capital, 2),
            "external_cash_flow": round(external_cash_flow, 2),
            "effective_principal": round(effective_principal, 2),
            "return_pct": round(return_pct, 4),
            "total_return": round(total_return, 2),
            "total_return_pct": round(
                total_return / effective_principal * 100, 4
            ) if effective_principal else 0.0,
            "positions_count": len(service.get_positions(day)),
        })
        previous_equity = equity
        previous_at = captured_at

    def aggregate(key_fn):
        groups = {}
        for row in daily:
            groups.setdefault(key_fn(row["date"]), []).append(row)
        aggregated = []
        for key, rows in sorted(groups.items()):
            external_cash_flow = sum(
                float(row["external_cash_flow"]) for row in rows
            )
            invested_capital = rows[0]["opening_equity"] + external_cash_flow
            period_profit = (
                rows[-1]["total_equity"]
                - rows[0]["opening_equity"]
                - external_cash_flow
            )
            aggregated.append({
                "period": key,
                "start_equity": rows[0]["opening_equity"],
                "end_equity": rows[-1]["total_equity"],
                "external_cash_flow": round(external_cash_flow, 2),
                "return_pct": round(
                    period_profit / invested_capital * 100, 4
                ) if invested_capital else 0.0,
            })
        return aggregated

    return {
        "initial_cash": initial_cash,
        "effective_principal": daily[-1]["effective_principal"],
        "current_equity": daily[-1]["total_equity"],
        "total_return": daily[-1]["total_return"],
        "total_return_pct": daily[-1]["total_return_pct"],
        "cash_flow_adjusted": True,
        "max_drawdown_pct": service.get_performance_metrics()["max_drawdown_pct"],
        "daily": daily,
        "weekly": aggregate(lambda d: f"{datetime.fromisoformat(d).isocalendar()[0]}-W{datetime.fromisoformat(d).isocalendar()[1]:02d}"),
        "monthly": aggregate(lambda d: d[:7]),
    }


class ResearchQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1200)
    stock_code: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    mode: Literal["facts", "experts"] = "facts"
    request_id: str = Field(default="", max_length=80)


def research_store():
    from src.research.store import ResearchStore
    return ResearchStore(DATA_DIR / "stock_intelligence.db")


def research_entry_plans():
    from src.paper_trading.entry_plans import EntryPlanStore
    from src.storage.trading_ledger import TradingLedger
    store = EntryPlanStore(TradingLedger(DATA_DIR))
    return store.get_summary(date.today().isoformat())


@app.get("/api/research/overview")
def research_overview():
    store = research_store()
    summary = store.get_summary()
    latest = store.list_judgments(limit=1)
    summary["data_as_of"] = latest[0]["as_of"] if latest else None
    return {"available": True, **summary, "entry_plans": research_entry_plans(),
            "limitations": ["只读研究，不是收益承诺", "旧报告缺失的原始证据不会自动补造", "实验结果需样本外和前向影子验证"]}


@app.get("/api/research/diagnostics")
def research_diagnostics(trade_date: Optional[date] = None):
    from src.research.diagnostics import build_diagnostics
    return build_diagnostics(DATA_DIR, today=trade_date)


@app.get("/api/research/scorecard")
def research_scorecard():
    from src.research.diagnostics import prediction_scorecard
    return prediction_scorecard(DATA_DIR / "stock_intelligence.db")


@app.get("/api/research/judgments")
def research_judgments(code: Optional[str] = Query(default=None, pattern=r"^\d{6}$"),
                       limit: int = Query(default=30, ge=1, le=100)):
    return {"items": research_store().list_judgments(stock_code=code, limit=limit)}


@app.get("/api/research/judgments/{judgment_id}")
def research_judgment(judgment_id: str):
    store = research_store()
    judgment = store.get_judgment(judgment_id)
    if judgment is None:
        raise HTTPException(404, "判断记录不存在")
    evidence = []
    for stance, key in (("supporting", "supporting_evidence"), ("counter", "counter_evidence")):
        for ref in judgment.get(key) or []:
            evidence_id = ref.get("evidence_id") if isinstance(ref, dict) else ref
            row = store.get_evidence(evidence_id)
            if row:
                evidence.append({**row, "stance": stance})
    judgment["evidence"] = evidence
    return {"judgment": judgment, "reviews": store.list_reviews(judgment_id=judgment_id)}


@app.get("/api/research/lessons")
def research_lessons():
    return {"items": research_store().list_lessons(limit=100)}


@app.get("/api/research/experiments")
def research_experiments():
    return {"items": research_store().list_experiments(limit=100)}


@app.get("/api/research/entry-plans")
def research_plans(trade_date: Optional[str] = None):
    from src.paper_trading.entry_plans import EntryPlanStore
    from src.storage.trading_ledger import TradingLedger
    if trade_date:
        date.fromisoformat(trade_date)
    store = EntryPlanStore(TradingLedger(DATA_DIR))
    target = trade_date or date.today().isoformat()
    return {"items": store.list_plans(trade_date=target), "summary": store.get_summary(target)}


@app.post("/api/research/chat")
def research_chat(body: ResearchQuestion):
    from src.research.assistant import ResearchAssistant, RequestBudget, BudgetExceeded
    from datetime import timezone
    store = research_store()
    now = datetime.now(timezone.utc).isoformat()
    judgments = store.list_judgments(stock_code=body.stock_code, as_of=now, limit=6)
    evidence, seen = [], set()
    for row in judgments:
        for ref in (row.get("supporting_evidence") or []) + (row.get("counter_evidence") or []):
            evidence_id = ref.get("evidence_id") if isinstance(ref, dict) else ref
            if evidence_id in seen or len(evidence) >= 5:
                continue
            seen.add(evidence_id)
            item = store.get_evidence(evidence_id, as_of=now)
            if item:
                evidence.append(item)
    from src.paper_trading.trading_service import TradingService
    accounting = TradingService(DATA_DIR).get_trade_accounting()["summary"]
    context = {
        "stock_code": body.stock_code,
        "as_of": max((str(row.get("as_of") or "") for row in judgments), default=None),
        "judgments": [{k: row.get(k) for k in ("id", "stock_code", "stock_name", "as_of", "thesis", "horizon", "history_incomplete")} for row in judgments],
        "lessons": store.list_lessons(as_of=now, limit=5),
        "evidence": evidence,
        "accounting": accounting,
        "entry_plans": research_entry_plans(),
        "sources": [{"label": f"判断 {row.get('id')} · {row.get('as_of')}"} for row in judgments],
        "limitations": ["只读取本系统已保存资料，不查询互联网；报价请查看行情页的取价时间。"],
    }
    assistant = ResearchAssistant(RequestBudget(DATA_DIR / "stock_intelligence.db"))
    try:
        return assistant.answer(body.question, context, body.mode, body.request_id)
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc)) from None


# 挂载静态文件
dashboard_dir = PROJECT_ROOT / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=8080)
