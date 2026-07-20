# -*- coding: utf-8 -*-
"""FastAPI后端"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import os
import logging
import threading
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.models import ApiResponse

app = FastAPI(title="Stock Intelligence Dashboard")
logger = logging.getLogger("stock_intelligence.feishu")
ANALYSIS_REGEN_LOCK = threading.Lock()

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
async def reset_account(confirm: bool = False, initial_cash: float = 4000):
    """重置整个账户（归档旧数据，创建新账户）"""
    if not confirm:
        return ApiResponse.fail("需要确认：此操作将归档所有数据并创建新账户，添加 ?confirm=true 确认")

    if initial_cash != 4000:
        return ApiResponse.fail("当前正式模拟账户初始资金固定为¥4,000")
    import shutil
    from src.paper_trading.trading_service import TradingService
    archive_dir = DATA_DIR / "archive" / f"reset_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    db_file = DATA_DIR / "stock_intelligence.db"
    if db_file.exists():
        shutil.copy2(db_file, archive_dir / "stock_intelligence.db")
    result = TradingService().initialize_account(4000, reset=True)
    return {"success": True, "archive_dir": str(archive_dir), "account": result}


# API: 获取持仓
@app.get("/api/positions")
async def get_positions(date_str: Optional[str] = None):
    from src.paper_trading.trading_service import TradingService
    return TradingService().get_positions(date_str or date.today().isoformat())


# API: 获取今日推荐（自动补全实时价格，自动回退到最近可用日期）
@app.get("/api/recommendation")
async def get_recommendation(date_str: Optional[str] = None):
    from src.reporting.report_store import load_report
    from datetime import timedelta

    target = date_str or date.today().isoformat()
    report = load_report(target, "morning")

    # If no report for target date, try previous days (up to 3 days back)
    if not report:
        for offset in range(1, 4):
            fallback = (date.fromisoformat(target) - timedelta(days=offset)).isoformat()
            report = load_report(fallback, "morning")
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
    from src.reporting.report_store import load_report
    from datetime import timedelta

    today = date.today()
    result = []

    # Try up to 3 days back for each type
    for day_offset in range(0, 4):
        target = (today - timedelta(days=day_offset))

        # Try closing first (more recent analysis)
        closing = load_report(target.isoformat(), "closing")
        if closing:
            _enrich_stock_prices(closing)
            result.append({
                "label": f"{target.isoformat()} 收盘分析 (16:45)",
                "period": f"{target.isoformat()}_closing",
                "time": "16:45",
                "date": target.isoformat(),
                "type": "closing",
                "data": closing,
            })

        # Try morning
        morning = load_report(target.isoformat(), "morning")
        if morning:
            _enrich_stock_prices(morning)
            result.append({
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

    # Sort: morning first, then closing (chronological within same day)
    result.sort(key=lambda r: (r["date"], r["type"]))
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
    for position in service.get_positions(date.today().isoformat()):
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
    for original in service.get_positions(date.today().isoformat()):
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
                            "current_price": track.get("current_price", 0),
                            "actual_return_pct": track.get("actual_return_pct", 0),
                            "status": track.get("status", ""),
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
    return TradingService().ledger.list_trades(limit=limit)


# API: 自动操盘单日日志
@app.get("/api/paper/auto-trades/{date_str}")
def get_auto_trade_log(date_str: str):
    """Get auto-trade log for a specific date"""
    from src.paper_trading.trading_service import TradingService
    log = TradingService().ledger.list_trades(trade_date=date_str)
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

    with open(config_file) as f:
        strategy = yaml.safe_load(f)

    # Update factor weights
    if "factor_weights" in data:
        strategy.setdefault("factor_weights", {}).update(data["factor_weights"])

    # Update sector allocations
    if "sector_allocations" in data:
        for name, values in data["sector_allocations"].items():
            if name in strategy.get("sector_allocations", {}):
                strategy["sector_allocations"][name].update(values)

    # Update custom params (horizon ratios, market ratios, price threshold)
    if "custom_params" in data:
        strategy.setdefault("custom_params", {}).update(data["custom_params"])

    with open(config_file, "w") as f:
        yaml.dump(strategy, f, allow_unicode=True, default_flow_style=False)

    return {"status": "ok", "strategy": strategy}


# API: 资产分析数据（同步I/O，用def避免阻塞事件循环）
@app.get("/api/analysis/asset")
def get_asset_analysis():
    from src.paper_trading.trading_service import TradingService
    service = TradingService()
    account = service.get_account()
    snapshots = service.ledger.snapshots()
    by_day = {}
    for snapshot in snapshots:
        by_day[snapshot["captured_at"][:10]] = float(snapshot["total_equity"])
    by_day[date.today().isoformat()] = account["total_equity"]
    daily = []
    for day, equity in sorted(by_day.items()):
        daily.append({
            "date": day,
            "total_equity": equity,
            "total_return": round(equity - account["initial_cash"], 2),
            "total_return_pct": round((equity / account["initial_cash"] - 1) * 100, 4),
            "positions_count": len(service.get_positions(day)),
        })
    def aggregate(key_fn):
        groups = {}
        for row in daily:
            groups.setdefault(key_fn(row["date"]), []).append(row)
        return [{
            "period": key,
            "start_equity": rows[0]["total_equity"],
            "end_equity": rows[-1]["total_equity"],
            "return_pct": round((rows[-1]["total_equity"] / rows[0]["total_equity"] - 1) * 100, 4),
        } for key, rows in sorted(groups.items())]
    return {
        "initial_cash": account["initial_cash"],
        "current_equity": daily[-1]["total_equity"],
        "total_return": daily[-1]["total_return"],
        "total_return_pct": daily[-1]["total_return_pct"],
        "max_drawdown_pct": service.get_performance_metrics()["max_drawdown_pct"],
        "daily": daily,
        "weekly": aggregate(lambda d: f"{datetime.fromisoformat(d).isocalendar()[0]}-W{datetime.fromisoformat(d).isocalendar()[1]:02d}"),
        "monthly": aggregate(lambda d: d[:7]),
    }


# 挂载静态文件
dashboard_dir = PROJECT_ROOT / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=8080)
