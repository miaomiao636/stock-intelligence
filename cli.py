# -*- coding: utf-8 -*-
"""Stock Intelligence CLI"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import click
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

REPORTABLE_STATUSES = {"success", "partial_success", "degraded"}


def _source_status_icon(status: str) -> str:
    """把来源状态映射为不会误报的CLI图标。"""
    value = str(status or "")
    if value in {"ok", "success"} or value.startswith("ok_"):
        return "✅"
    if value in {"degraded", "fallback"} or value.startswith("degraded_"):
        return "⚠️"
    if value.startswith("deferred_"):
        return "ℹ️"
    return "❌"


def _format_report_notification(date_str: str, mode: str, report: dict, result: dict | None = None) -> str:
    """为定时推送与手动补发生成同一份报告内容。"""
    result = result or {}
    if mode == "closing":
        from src.reporting.formatter import format_closing_report

        return format_closing_report(
            date_str=date_str,
            market_data=result.get("market_data") or report.get("market_data", {}),
            evaluation=result.get("evaluation") or report.get("evaluation", {}),
            warnings=result.get("warnings") or report.get("warnings", []),
            account_summary=result.get("account_summary") or report.get("account_summary", {}),
        )

    if mode == "afternoon":
        from src.reporting.formatter import format_afternoon_report

        return format_afternoon_report(
            date_str=date_str,
            market_data=report.get("market_data", {}),
            stock_recommendations=report.get("stock_recommendations", []),
            summary=report.get("afternoon_summary", {}),
            degraded=bool(report.get("analysis_degraded")),
        )

    from src.reporting.formatter import format_morning_report

    content = format_morning_report(
        date_str=date_str,
        market_data=report.get("market_data", {}),
        news_list=report.get("news_sources", []),
        sector_recommendations=report.get("sector_recommendations", []),
        stock_recommendations=report.get("stock_recommendations", []),
    )
    candidates = [
        stock for stock in report.get("stock_recommendations", [])
        if stock.get("action") == "setup_ready" and stock.get("trade_eligible", True)
    ]
    if candidates:
        content += "\n\n📌 08:50盘前候选（只读，不执行）\n"
        for stock in candidates[:4]:
            content += (
                f"  • {stock.get('name', '')}({stock.get('code', '')}) "
                f"参考价¥{stock.get('current_price') or stock.get('entry_price') or 0}\n"
            )
        content += "09:35将获取当日行情重算，并另发最终交互卡片。"
    return content


def _deliver_report_notification(date_str: str, mode: str, content: str, source: str) -> dict:
    """发送报告并保存回执；发送成功但回执落盘失败也视为需要人工关注。"""
    from src.notifier.feishu import FeishuNotifier
    from src.notifier.report_delivery import record_delivery

    notifier = FeishuNotifier()
    if notifier.is_available():
        notify_result = notifier.send_report(content, mode)
    else:
        notify_result = {"status": "error", "message": "飞书发送配置不可用"}
    try:
        record_delivery(date_str, mode, notify_result, source)
    except Exception as exc:
        if notify_result.get("status") == "success":
            return {"status": "error", "message": f"飞书已发送，但本地回执保存失败: {exc}"}
    return notify_result


@click.group()
def cli():
    """Stock Intelligence - 智能选股分析系统"""
    pass


@cli.command("reconcile")
def reconcile_workflow():
    """开机/唤醒后检查并补跑缺失的关键阶段。"""
    from src.automation.reconcile import run_reconcile

    result = run_reconcile()
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "error":
        raise click.ClickException(f"自动补偿失败: {result.get('stage') or 'unknown'}")


@cli.command()
def init():
    """初始化项目：创建目录、复制配置模板、初始化SQLite"""
    click.echo("正在初始化项目...")
    
    # 创建目录
    dirs = [
        "data/recommendations",
        "data/evaluations",
        "data/strategy",
        "data/portfolio",
        "data/news_sources",
        "data/runs",
    ]
    for d in dirs:
        (PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)
        click.echo(f"  ✅ 创建目录: {d}")
    
    # 检查.env
    env_file = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / ".env.example"
    if not env_file.exists() and env_example.exists():
        click.echo("  ⚠️  .env 文件不存在，请复制 .env.example 并填写配置")
    else:
        click.echo("  ✅ .env 文件已存在")
    
    # 初始化SQLite
    try:
        from src.storage.db import init_db
        init_db()
        click.echo("  ✅ SQLite 数据库已初始化")
    except Exception as e:
        click.echo(f"  ❌ SQLite 初始化失败: {e}")
    
    click.echo("\n初始化完成！")


@cli.command()
def doctor():
    """检查环境、依赖、API Key、路径、数据源"""
    click.echo("正在检查环境...\n")
    
    results = []
    
    # 检查Python版本
    if sys.version_info >= (3, 9):
        results.append(("PASS", f"Python {sys.version.split()[0]}"))
    else:
        results.append(("FAIL", f"Python {sys.version.split()[0]} (需要 3.9+)"))
    
    # 检查基础依赖包
    try:
        import dotenv
        import pydantic
        results.append(("PASS", "基础依赖包已安装"))
    except ImportError as e:
        results.append(("FAIL", f"基础依赖包缺失: {e}"))
    
    # 检查真实链路依赖
    try:
        import requests
        results.append(("PASS", "requests 已安装"))
    except ImportError:
        results.append(("FAIL", "requests 未安装"))
    
    try:
        import openai
        results.append(("PASS", "openai 已安装"))
    except ImportError:
        results.append(("FAIL", "openai 未安装"))
    
    try:
        import akshare
        results.append(("PASS", "akshare 已安装"))
    except ImportError:
        results.append(("FAIL", "akshare 未安装"))
    
    try:
        import tavily
        results.append(("PASS", "tavily 已安装"))
    except ImportError:
        results.append(("WARN", "tavily 未安装（可选）"))
    
    # 检查LLM是否可初始化
    try:
        from src.analysis.llm_client import LLMClient
        llm_client = LLMClient()
        if llm_client.is_available():
            results.append(("PASS", "LLM 客户端可初始化"))
        else:
            results.append(("WARN", "LLM 客户端不可用（检查API Key）"))
    except Exception as e:
        results.append(("FAIL", f"LLM 客户端初始化失败: {e}"))
    
    # 检查.env
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        results.append(("PASS", ".env 已配置"))
    else:
        results.append(("WARN", ".env 未配置"))
    
    # 检查SQLite
    db_file = PROJECT_ROOT / "data" / "stock_intelligence.db"
    if db_file.exists():
        results.append(("PASS", "SQLite 已初始化"))
    else:
        results.append(("WARN", "SQLite 未初始化（运行 init 命令）"))
    
    # 检查飞书配置
    from src.notifier.feishu import FeishuNotifier

    notifier = FeishuNotifier()
    feishu_url = os.getenv("FEISHU_WEBHOOK_URL")
    if feishu_url:
        results.append(("PASS", "飞书 Webhook 已配置（普通报告可推送）"))
    else:
        results.append(("WARN", "飞书 Webhook 未配置（普通报告将不推送）"))

    missing_interactive = notifier.interactive_missing_fields()
    if missing_interactive:
        results.append(("WARN", f"飞书交互卡片未就绪，缺少: {', '.join(missing_interactive)}"))
    else:
        results.append(("PASS", f"飞书交互卡片参数已配置（接收类型: {notifier.receive_id_type}）"))

    callback_url = notifier.callback_url()
    callback_health = notifier.check_callback_reachable(timeout=3)
    if callback_health.get("reachable"):
        if callback_health.get("transport") == "ws":
            results.append(("PASS", "飞书官方长连接已就绪（无需公网地址）"))
        else:
            results.append(("PASS", f"飞书公网回调可达: {callback_url}"))
    else:
        callback_status = (
            "FAIL"
            if os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true"
            else "WARN"
        )
        results.append((
            callback_status,
            f"飞书双向回调不可用: {callback_health.get('reason') or '未知原因'}",
        ))

    host = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
    if callback_health.get("transport") == "ws" and callback_health.get("reachable"):
        results.append(("PASS", "服务可继续仅监听本机；飞书事件由长连接接收"))
    elif host in {"127.0.0.1", "localhost"} and not callback_url:
        results.append(("WARN", "服务仅本机监听且没有 PUBLIC_BASE_URL，飞书无法从公网回调到 /api/feishu/callback"))
    elif host in {"127.0.0.1", "localhost"}:
        results.append(("WARN", "服务当前仅本机监听；请确认 PUBLIC_BASE_URL 通过反向代理或隧道转发到本机 8080"))
    else:
        results.append(("PASS", f"服务监听地址: {host}"))

    if os.getenv("API_KEY"):
        results.append(("PASS", "API_KEY 已配置（写接口可鉴权开启）"))
    else:
        results.append(("WARN", "API_KEY 未配置，写接口会保持关闭"))

    if os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true":
        results.append(("WARN", "PAPER_TRADING_ENABLED=true；确认飞书回调和风控验收通过后再实盘运行"))
    else:
        results.append(("PASS", "PAPER_TRADING_ENABLED=false（当前仍处于安全关闭状态）"))
    
    # 检查配置文件
    config_files = [
        "config/strategy.yaml",
        "config/sources.yaml",
        "config/eligibility.yaml",
        "config/horizons.yaml",
        "config/cost_model.yaml",
    ]
    for cf in config_files:
        if (PROJECT_ROOT / cf).exists():
            results.append(("PASS", f"{cf} 存在"))
        else:
            results.append(("WARN", f"{cf} 不存在"))
    
    # 输出结果
    click.echo("检查结果：")
    pass_count = 0
    warn_count = 0
    fail_count = 0
    
    for status, msg in results:
        if status == "PASS":
            click.echo(f"  ✅ PASS: {msg}")
            pass_count += 1
        elif status == "WARN":
            click.echo(f"  ⚠️  WARN: {msg}")
            warn_count += 1
        else:
            click.echo(f"  ❌ FAIL: {msg}")
            fail_count += 1
    
    click.echo(f"\n结果: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL")
    
    if fail_count > 0:
        click.echo("❌ 存在 FAIL 项，请修复后再运行")
        sys.exit(1)
    else:
        click.echo("✅ 可以运行")


@cli.group()
def config():
    """配置相关命令"""
    pass


@config.command()
def validate():
    """校验配置文件"""
    click.echo("正在校验配置...\n")
    
    try:
        import yaml
        
        # 校验strategy.yaml
        strategy_file = PROJECT_ROOT / "config" / "strategy.yaml"
        if strategy_file.exists():
            with open(strategy_file) as f:
                strategy = yaml.safe_load(f)
            
            # 检查因子权重总和
            factor_weights = strategy.get("factor_weights", {})
            total = sum(factor_weights.values())
            if abs(total - 1.0) < 0.01:
                click.echo("  ✅ 因子权重总和 = 1.0")
            else:
                click.echo(f"  ❌ 因子权重总和 = {total} (应为 1.0)")
            
            # 检查板块配置总和
            sector_allocations = strategy.get("sector_allocations", {})
            total = sum(s["allocation"] for s in sector_allocations.values())
            if abs(total - 1.0) < 0.01:
                click.echo("  ✅ 板块配置总和 = 1.0")
            else:
                click.echo(f"  ❌ 板块配置总和 = {total} (应为 1.0)")
        
        click.echo("\n配置校验完成！")
        
    except Exception as e:
        click.echo(f"  ❌ 配置校验失败: {e}")
        sys.exit(1)


@cli.command()
def demo():
    """用内置样例数据生成报告"""
    click.echo("正在生成样例报告...\n")
    
    # 创建样例报告
    from datetime import datetime
    
    sample_report = {
        "date": datetime.now().isoformat(),
        "type": "morning",
        "run_id": "demo-001",
        "created_at": datetime.now().isoformat(),
        "data_as_of": datetime.now().isoformat(),
        "strategy_version": "1.0",
        "market_regime": "neutral",
        "sector_recommendations": [
            {
                "recommendation_id": "REC-DEMO-001",
                "sector_name": "AI算力",
                "rating": 5,
                "reason": "英伟达财报超预期+国务院政策支持",
                "horizon": "short",
                "horizon_days": 3,
                "target_return_pct": 5.0,
                "entry_date": datetime.now().isoformat(),
                "expiry_date": datetime.now().isoformat(),
                "risk_level": "medium"
            }
        ],
        "stock_recommendations": [
            {
                "recommendation_id": "REC-DEMO-002",
                "code": "002049",
                "name": "紫光国微",
                "sector": "AI算力",
                "action": "setup_ready",
                "horizon": "short",
                "horizon_days": 3,
                "target_return_pct": 8.0,
                "reason": "AI芯片龙头",
                "timing": {
                    "entry_condition": "回踩5日均线附近",
                    "risk_exit_condition": "目标位180元，风险失效位158元"
                },
                "entry_date": datetime.now().isoformat(),
                "expiry_date": datetime.now().isoformat(),
                "status": "active"
            }
        ],
        "risk_warnings": ["这是样例报告，非真实数据"]
    }
    
    # 保存样例报告
    report_dir = PROJECT_ROOT / "data" / "recommendations" / datetime.now().strftime("%Y-%m-%d")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "morning_sample.json"
    
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(sample_report, f, ensure_ascii=False, indent=2)
    
    click.echo(f"  ✅ 样例报告已生成: {report_file}")
    click.echo("\n样例报告生成完成！")


@cli.command()
@click.option("--mode", type=click.Choice(["morning", "afternoon", "closing"]), required=True)
@click.option("--dry-run", is_flag=True, help="干运行，不写入文件")
@click.option("--no-notify", is_flag=True, help="不推送飞书")
@click.option("--force", is_flag=True, help="强制重新生成")
@click.option("--date", "date_str", help="指定日期 (YYYY-MM-DD)")
def daily(mode, dry_run, no_notify, force, date_str):
    """每日流程"""
    click.echo(f"正在执行 daily --mode {mode}...\n")
    
    if dry_run:
        click.echo("  ℹ️  干运行模式，不写入文件")
    
    if no_notify:
        click.echo("  ℹ️  不推送飞书")
    
    notification_failed = False

    # 执行真实数据流程
    try:
        from src.orchestrator import run_morning_pipeline
        
        if mode == "morning":
            result = run_morning_pipeline(dry_run=dry_run, force=force, date_str=date_str)
        elif mode == "afternoon":
            from src.analysis.afternoon_pipeline import run_afternoon_pipeline
            result = run_afternoon_pipeline(dry_run=dry_run, force=force, date_str=date_str)
        else:
            from src.evaluation.closing_pipeline import run_closing_pipeline
            result = run_closing_pipeline(dry_run=dry_run, force=force, date_str=date_str)
        
        # 显示结果
        if result["status"] == "skip":
            click.echo(f"  ℹ️  跳过: {result['reason']}")
        elif result["status"] == "exists":
            click.echo("  ℹ️  已有当天报告，返回已有报告")
        elif result["status"] in REPORTABLE_STATUSES:
            click.echo("  ✅ 数据采集完成")
            click.echo(f"  ✅ 报告生成成功")
            
            # 显示数据源状态
            for source, status in result.get("source_status", {}).items():
                icon = _source_status_icon(status)
                click.echo(f"  {icon} {source}: {status}")
            
            # 显示错误
            for error in result.get("errors", []):
                click.echo(f"  ⚠️  {error}")
            
            if not dry_run:
                click.echo("  ✅ 报告已保存到 data/recommendations/")
                
                # 推送飞书
                if not no_notify and result.get("status") != "error":
                    report_date = date_str or result.get("report", {}).get("date") or date.today().isoformat()
                    report_content = _format_report_notification(
                        report_date,
                        mode,
                        result.get("report", {}),
                        result,
                    )
                    notify_result = _deliver_report_notification(
                        report_date,
                        mode,
                        report_content,
                        source="daily",
                    )
                    if notify_result["status"] == "success":
                        click.echo("  ✅ 飞书推送成功（回执已保存）")
                    else:
                        notification_failed = True
                        click.echo(f"  ❌ 飞书推送失败: {notify_result.get('message') or notify_result.get('reason') or '未知错误'}")
        
    except Exception as e:
        click.echo(f"  ❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # 检查结果状态
    if notification_failed or result.get("status") in ["error", "partial"]:
        sys.exit(1)
    
    click.echo("\ndaily 命令执行完成！")


def load_evaluation(date_str: str):
    """加载评估结果"""
    eval_file = Path(__file__).parent / "data" / "evaluations" / date_str / "closing.json"
    if not eval_file.exists():
        return None
    with open(eval_file, "r", encoding="utf-8") as f:
        return json.load(f)


@cli.group()
def paper():
    """模拟操盘相关命令"""
    pass


@paper.command()
@click.option("--cash", type=float, default=None, help="初始资金（默认从strategy.yaml读取）")
def init(cash):
    """初始化模拟账户"""
    from src.paper_trading.trading_service import TradingService
    from src.strategy.position_limits import get_initial_cash
    service = TradingService()
    result = service.initialize_account(cash or get_initial_cash())
    click.echo(f"  ✅ 模拟账户已初始化")
    click.echo(f"  初始资金: {result['initial_cash']:,.0f}")


@paper.command("open")
@click.option("--date", "date_str", help="交易日 YYYY-MM-DD")
@click.option("--if-missing", is_flag=True, help="仅在没有成功状态回执时补跑")
def paper_open(date_str, if_missing):
    """09:35重新取价，通过风控后自动成交并推送结果。"""
    from src.analysis.recovery import recover_degraded_morning_report
    from src.notifier.feishu import FeishuNotifier
    from src.notifier.paper_delivery import has_successful_delivery, record_delivery
    from src.paper_trading.workflow import PaperTradingWorkflow
    from src.reporting.report_store import load_report

    target = date_str or date.today().isoformat()
    if if_missing and has_successful_delivery(target):
        click.echo(f"  ✅ {target} 09:35已有成功状态回执，无需重复执行")
        return

    report = load_report(target, "morning")
    if not report:
        workflow_result = {"status": "error", "orders": [], "reason": "当天盘前报告缺失"}
        notifier = FeishuNotifier()
        notify_result = (
            notifier.send_message(
                "09:35模拟交易流程异常",
                f"{target} 未找到当天盘前报告，未生成任何订单。系统将在09:45自动再检查一次。",
            )
            if notifier.is_available()
            else {"status": "error", "message": "飞书发送配置不可用"}
        )
        record_delivery(
            target,
            workflow_result,
            notify_result,
            source="watchdog" if if_missing else "scheduled",
        )
        raise click.ClickException(f"未找到 {target} 的盘前报告")
    recovery = recover_degraded_morning_report(target, report=report)
    report = recovery.get("report") or report
    if recovery["status"] == "recovered":
        click.echo("  ✅ 08:45的LLM降级报告已在09:35自动恢复")
    elif recovery["status"] == "still_degraded":
        click.echo("  ⚠️ 09:35补偿分析仍降级，将保持安全模式且不创建订单")
    result = PaperTradingWorkflow().prepare_final_orders(
        report,
        source_status=report.get("source_status", {}),
    )
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    workflow_status = result.get("status", "error")
    source = "watchdog" if if_missing else "scheduled"
    if workflow_status == "success":
        # 自动成交与飞书通知分开执行；通知失败时不能把阶段回执误记成功，
        # 09:45补跑会利用订单幂等键重新发送最终成交结果。
        notify_result = result.get("notification_result") or {"status": "success", "data": {}}
    else:
        if workflow_status == "safe_mode":
            summary = f"今日安全暂停新开仓。\n原因：{result.get('reason') or '风控条件未通过'}"
        elif workflow_status == "no_orders":
            rejected = result.get("rejected") if isinstance(result.get("rejected"), list) else []
            details = "\n".join(
                f"- {item.get('code') or '未知标的'}：{item.get('reason') or '未通过'}"
                for item in rejected[:4]
                if isinstance(item, dict)
            )
            equity = result.get("account_equity")
            equity_text = f"¥{equity:,.0f}" if isinstance(equity, (int, float)) else "当前"
            summary = f"已完成09:35重算，但没有符合{equity_text}账户仓位和风控条件的订单。"
            if details:
                summary += f"\n{details}"
        elif workflow_status == "disabled":
            summary = "模拟交易当前处于关闭状态，未生成任何订单。"
        else:
            summary = f"09:35模拟交易流程未完成：{result.get('reason') or workflow_status}"
        notifier = FeishuNotifier()
        notify_result = (
            notifier.send_message("09:35模拟交易状态", summary)
            if notifier.is_available()
            else {"status": "error", "message": "飞书发送配置不可用"}
        )

    try:
        record_delivery(target, result, notify_result, source=source)
    except Exception as exc:
        raise click.ClickException(f"09:35状态回执保存失败: {exc}") from exc

    if notify_result.get("status") != "success":
        raise click.ClickException(
            f"09:35状态未送达飞书: {notify_result.get('message') or notify_result.get('reason') or '未知错误'}"
        )
    if workflow_status not in {"success", "safe_mode", "no_orders", "disabled"}:
        raise click.ClickException(result.get("reason") or workflow_status)


@paper.command("execute-due")
def paper_execute_due():
    """09:40或确认后重新取价并执行到期订单。"""
    from src.paper_trading.workflow import PaperTradingWorkflow

    result = PaperTradingWorkflow().execute_due_orders()
    if result.get("results"):
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@paper.command("intraday")
def paper_intraday():
    """每30分钟更新净值、回撤和持仓风险。"""
    from src.paper_trading.workflow import PaperTradingWorkflow

    result = PaperTradingWorkflow().intraday_check()
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@paper.command()
def account():
    """查看账户"""
    from src.paper_trading.trading_service import TradingService
    data = TradingService().get_account()
    
    if not data:
        click.echo("  ❌ 账户未初始化，请先运行 paper init")
        return
    
    click.echo(f"\n💰 模拟账户")
    click.echo("=" * 40)
    click.echo(f"  账户ID: {data.get('account_id')}")
    click.echo(f"  初始资金: {data.get('initial_cash', 0):,.0f}")
    click.echo(f"  可用现金: {data.get('cash', 0):,.0f}")
    click.echo(f"  持仓市值: {data.get('market_value', 0):,.0f}")
    click.echo(f"  总资产: {data.get('total_equity', 0):,.0f}")
    click.echo(f"  未实现盈亏: {data.get('unrealized_pnl', 0):,.0f}")
    click.echo(f"  已实现盈亏: {data.get('realized_pnl', 0):,.0f}")


@paper.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def positions(date_str):
    """查看持仓"""
    from src.paper_trading.trading_service import TradingService
    pos_list = TradingService().get_positions(date_str)
    
    if not pos_list:
        click.echo("  ℹ️  当前没有持仓")
        return
    
    click.echo(f"\n📈 模拟持仓")
    click.echo("=" * 50)
    for pos in pos_list:
        pnl_icon = "📈" if pos.get("unrealized_pnl_pct", 0) >= 0 else "📉"
        click.echo(f"  {pnl_icon} {pos.get('name')} {pos.get('code')}")
        click.echo(f"     成本: {pos.get('avg_cost', 0):.2f} | 现价: {pos.get('current_price', 0):.2f}")
        click.echo(f"     数量: {pos.get('quantity')} | 盈亏: {pos.get('unrealized_pnl_pct', 0):.2f}%")


@paper.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def plan(date_str):
    """生成交易计划"""
    raise click.ClickException("旧的盘前计划器会写JSON订单，已停用；请在09:35使用 paper open")


@paper.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
@click.option("--status", default="pending", help="订单状态")
def orders(date_str, status):
    """查看订单"""
    from src.paper_trading.trading_service import TradingService
    order_list = TradingService().ledger.list_orders([status] if status else None, date_str)
    
    if not order_list:
        click.echo(f"  ℹ️  没有{status}状态的订单")
        return
    
    click.echo(f"\n📋 订单列表 ({status})")
    click.echo("=" * 50)
    for order in order_list:
        action = "买入" if order.get("action") == "buy" else "卖出"
        click.echo(f"  {order.get('order_id')}: {action} {order.get('name')} {order.get('code')}")
        click.echo(f"    数量: {order.get('quantity')} | 金额: {order.get('estimated_amount', 0):,.0f}")
        click.echo(f"    状态: {order.get('status')}")
        click.echo("")


@paper.command()
@click.argument("order_id")
def approve(order_id):
    """批准订单"""
    from src.paper_trading.trading_service import TradingService
    order = TradingService().record_decision(order_id, "confirm", actor="cli_user")
    click.echo(f"  ✅ 订单 {order_id} 已确认，状态: {order['status']}")


@paper.command()
@click.argument("order_id")
def reject(order_id):
    """拒绝订单"""
    from src.paper_trading.trading_service import TradingService
    order = TradingService().record_decision(order_id, "veto", actor="cli_user")
    click.echo(f"  ✅ 订单 {order_id} 已否决，状态: {order['status']}")


@paper.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def execute(date_str):
    """执行已批准的订单"""
    raise click.ClickException("旧的直接成交命令已停用；请使用 paper execute-due 走重新取价和风控流程")


@paper.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def update(date_str):
    """使用 SQLite 唯一账本更新持仓、净值和退出信号。"""
    from src.paper_trading.workflow import PaperTradingWorkflow
    click.echo(json.dumps(PaperTradingWorkflow().intraday_check(), ensure_ascii=False, indent=2))


@paper.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def performance(date_str):
    """查看 SQLite 账本的扣费后收益与最大回撤。"""
    if not date_str:
        date_str = date.today().isoformat()

    from src.paper_trading.trading_service import TradingService

    service = TradingService()
    result = service.get_performance_metrics()
    account_data = service.get_account()
    positions = service.get_positions(date_str)
    
    click.echo(f"\n📊 模拟账户收益 - {date_str}")
    click.echo("=" * 50)
    click.echo(f"  总资产: {result.get('total_equity', 0):,.0f}")
    click.echo(f"  扣费后收益: {result.get('net_return_after_costs', 0):,.2f} ({result.get('net_return_after_costs_pct', 0):.2f}%)")
    click.echo(f"  最大回撤: {result.get('max_drawdown_pct', 0):.2f}%")
    click.echo(f"  费用+滑点: {result.get('total_transaction_costs', 0):,.2f}")
    click.echo(f"  未实现盈亏: {account_data.get('unrealized_pnl', 0):,.2f}")
    click.echo(f"  已实现盈亏: {account_data.get('realized_pnl', 0):,.2f}")
    click.echo(f"  已平仓胜率: {result.get('trade_win_rate_pct', 0):.1f}%")
    click.echo(f"  持仓数量: {len(positions)}")


@cli.group()
def strategy():
    """策略相关命令"""
    pass


@strategy.command()
def advisories():
    """查看策略建议"""
    from src.strategy.store import StrategyStore
    store = StrategyStore()
    pending = store.get_pending_advisories()
    
    if not pending:
        click.echo("  ℹ️  没有待处理的策略建议")
        return
    
    click.echo(f"\n📋 待处理策略建议 ({len(pending)}条)")
    click.echo("=" * 50)
    
    for adv in pending:
        click.echo(f"\n  ID: {adv.get('advisory_id')}")
        click.echo(f"  类型: {adv.get('adjustment_type')}")
        click.echo(f"  目标: {adv.get('target_name')}")
        click.echo(f"  当前值: {adv.get('current_value')}")
        click.echo(f"  建议值: {adv.get('suggested_value')}")
        click.echo(f"  原因: {adv.get('reason')}")


@strategy.command()
@click.argument("advisory_id", type=int)
def accept(advisory_id):
    """接受策略建议"""
    from src.strategy.store import StrategyStore
    from src.strategy.versioning import StrategyVersioning
    
    store = StrategyStore()
    versioning = StrategyVersioning()
    
    # 获取建议
    pending = store.get_pending_advisories()
    advisory = next((a for a in pending if a.get("advisory_id") == advisory_id), None)
    
    if not advisory:
        click.echo(f"  ❌ 未找到待处理建议: {advisory_id}")
        sys.exit(1)
    
    # 应用建议
    result = versioning.apply_advisory(advisory)
    if result["success"]:
        store.update_advisory_status(advisory_id, "accepted")
        click.echo(f"  ✅ 已接受建议 {advisory_id}")
    else:
        click.echo(f"  ❌ 应用建议失败: {result['reason']}")
        sys.exit(1)


@strategy.command()
@click.argument("advisory_id", type=int)
def reject(advisory_id):
    """拒绝策略建议"""
    from src.strategy.store import StrategyStore
    
    store = StrategyStore()
    if store.update_advisory_status(advisory_id, "rejected"):
        click.echo(f"  ✅ 已拒绝建议 {advisory_id}")
    else:
        click.echo(f"  ❌ 未找到建议: {advisory_id}")


@strategy.command()
@click.option("--version", "version_str", help="版本号")
def rollback(version_str):
    """回滚策略版本"""
    from src.strategy.versioning import StrategyVersioning
    
    versioning = StrategyVersioning()
    
    if not version_str:
        # 显示可用版本
        versions = versioning.list_versions()
        if not versions:
            click.echo("  ℹ️  没有历史版本")
            return
        
        click.echo("\n📦 历史版本:")
        for v in versions[:10]:
            click.echo(f"  {v['version']} - {v['timestamp']} ({v['reason']})")
        return
    
    # 执行回滚
    if versioning.rollback(version_str):
        click.echo(f"  ✅ 已回滚到版本 {version_str}")
    else:
        click.echo(f"  ❌ 版本不存在: {version_str}")


@cli.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def evaluate(date_str):
    """查看评估结果"""
    if not date_str:
        date_str = date.today().isoformat()

    click.echo(f"正在查看 {date_str} 的评估结果...")

    # 加载评估结果
    from src.reporting.report_store import load_report
    evaluation = load_evaluation(date_str)

    if not evaluation:
        click.echo(f"  ❌ 未找到评估结果: {date_str}")
        return

    # 显示评估结果
    metrics = evaluation.get("metrics", {})
    click.echo(f"\n📊 评估结果 - {date_str}")
    click.echo("=" * 40)
    click.echo(f"  推荐数量: {metrics.get('total_recommendations', 0)}")
    click.echo(f"  平均收益: {metrics.get('avg_return_pct', 0):.2f}%")
    click.echo(f"  超额收益: {metrics.get('avg_excess_return_pct', 0):.2f}%")
    click.echo(f"  胜率: {metrics.get('win_rate_pct', 0):.1f}%")
    click.echo(f"  盈亏比: {metrics.get('profit_loss_ratio', 0):.2f}")
    click.echo(f"  基准收益: {metrics.get('benchmark_return_pct', 0):.2f}%")

    # 显示个股结果
    click.echo(f"\n📈 个股表现:")
    for stock in evaluation.get("stock_results", []):
        status_icon = "✅" if stock.get("status") == "hit" else "❌" if stock.get("status") == "stopped" else "⏳"
        click.echo(f"  {status_icon} {stock.get('code')} {stock.get('name')}: {stock.get('return_pct', 0):.2f}%")


@cli.group()
def track():
    """推荐跟踪相关命令"""
    pass


@track.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
def run(date_str):
    """运行推荐跟踪"""
    if not date_str:
        date_str = date.today().isoformat()

    click.echo(f"正在运行推荐跟踪 {date_str}...")

    from src.tracking.tracker import RecommendationTracker
    tracker = RecommendationTracker()
    result = tracker.track_daily(date_str)

    summary = result.get("summary", {})
    click.echo(f"\n📊 跟踪结果 - {date_str}")
    click.echo("=" * 50)
    click.echo(f"  总跟踪: {summary.get('total_tracked', 0)}")
    click.echo(f"  胜率: {summary.get('win_rate_pct', 0):.1f}%")
    click.echo(f"  达标: {summary.get('hit_target', 0)}")
    click.echo(f"  止损: {summary.get('stopped_out', 0)}")
    click.echo(f"  活跃: {summary.get('active', 0)}")
    click.echo(f"  平均收益: {summary.get('avg_return_pct', 0):.2f}%")
    click.echo(f"  最佳: +{summary.get('best_return', 0):.2f}%")
    click.echo(f"  最差: {summary.get('worst_return', 0):.2f}%")

    # Show failed stocks
    tracks = result.get("tracks", [])
    failed = [t for t in tracks if t.get("is_failed")]
    if failed:
        click.echo(f"\n❌ 未达标 ({len(failed)}只):")
        for t in failed:
            click.echo(f"  {t['code']} {t['name']}: {t['actual_return_pct']:.2f}% - {t.get('failure_reason', '')}")


@track.command()
def weekly():
    """查看周回顾"""
    from src.tracking.tracker import RecommendationTracker
    tracker = RecommendationTracker()
    reviews = tracker.get_weekly_review(4)

    if not reviews:
        click.echo("  暂无周回顾数据")
        return

    for week in reviews:
        click.echo(f"\n📅 {week.get('week_label', '')}")
        click.echo(f"  跟踪: {week.get('total_tracked', 0)} | 胜率: {week.get('win_rate_pct', 0):.1f}% | 平均收益: {week.get('avg_return_pct', 0):.2f}%")
        click.echo(f"  达标: {week.get('hit_target', 0)} | 止损: {week.get('stopped_out', 0)}")

        winners = week.get("top_winners", [])
        if winners:
            click.echo(f"  🏆 最佳: " + ", ".join(f"{w['name']} +{w['return_pct']}%" for w in winners))
        losers = week.get("top_losers", [])
        if losers:
            click.echo(f"  📉 最差: " + ", ".join(f"{l['name']} {l['return_pct']}%" for l in losers))


@track.command()
def suggestions():
    """查看改进建议"""
    from src.tracking.tracker import RecommendationTracker
    tracker = RecommendationTracker()
    suggestions = tracker.get_improvement_suggestions()

    if not suggestions:
        click.echo("  暂无改进建议（数据量不足）")
        return

    click.echo(f"\n💡 改进建议 ({len(suggestions)}条)")
    click.echo("=" * 50)
    for s in suggestions:
        prio = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(s["priority"], "⚪")
        click.echo(f"  {prio} [{s['type']}] {s['message']}")


@cli.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
@click.option("--mode", type=click.Choice(["morning", "afternoon", "closing"]), required=True)
@click.option("--if-missing", is_flag=True, help="仅在没有成功推送回执时补发")
def notify(date_str, mode, if_missing):
    """重试飞书推送"""
    if not date_str:
        from datetime import date
        date_str = date.today().isoformat()

    if if_missing:
        from src.notifier.report_delivery import has_successful_delivery

        if has_successful_delivery(date_str, mode):
            click.echo(f"  ✅ {date_str} {mode} 已有成功推送回执，无需补发")
            return
    
    click.echo(f"正在重试飞书推送 {date_str} {mode}...")
    
    # 加载报告
    from src.reporting.report_store import load_report
    report = load_report(date_str, mode)
    
    if not report:
        if if_missing:
            from src.notifier.feishu import FeishuNotifier

            notifier = FeishuNotifier()
            if notifier.is_available():
                notifier.send_message(
                    "报告生成异常",
                    f"{date_str} {mode} 报告未生成，漏推补偿无法发送，请检查定时任务日志。",
                )
        raise click.ClickException(f"未找到报告: {date_str}/{mode}")

    # 定时推送和手动补发共用格式，避免盘后报告误用盘前模板。
    report_content = _format_report_notification(date_str, mode, report)

    result = _deliver_report_notification(
        date_str,
        mode,
        report_content,
        source="watchdog" if if_missing else "manual_retry",
    )
    
    if result["status"] == "success":
        click.echo("  ✅ 飞书推送成功")
    else:
        raise click.ClickException(f"飞书推送失败: {result.get('message') or result.get('reason') or '未知错误'}")


@cli.command()
@click.option("--date", "date_str", help="日期 (YYYY-MM-DD)")
@click.option("--mode", type=click.Choice(["morning", "afternoon", "closing"]))
@click.option("--interactive", is_flag=True, help="优先通过飞书自建应用发送测试卡片")
def notify_test(date_str, mode, interactive):
    """测试飞书推送"""
    if not date_str:
        from datetime import date
        date_str = date.today().isoformat()
    
    if not mode:
        mode = "morning"
    
    click.echo(f"正在测试飞书推送...")
    
    from src.notifier.feishu import FeishuNotifier
    notifier = FeishuNotifier()
    
    if not notifier.is_available():
        click.echo("  ⚠️  飞书未配置（FEISHU_WEBHOOK_URL）")
        return
    
    if interactive:
        missing = notifier.interactive_missing_fields()
        if missing:
            click.echo(f"  ⚠️  交互卡片配置不完整，缺少: {', '.join(missing)}")
            return
        result = notifier.send_callback_probe()
    else:
        # 发送测试消息
        result = notifier.send_message("测试消息", "这是一条来自Stock Intelligence的测试消息")

    if result["status"] == "success":
        click.echo("  ✅ 飞书测试推送成功")
    else:
        click.echo(f"  ❌ 飞书测试推送失败: {result.get('message', '未知错误')}")


@cli.command()
def sources():
    """测试数据源"""
    click.echo("正在测试数据源...\n")
    
    has_error = False
    
    # 测试AkShare
    try:
        import akshare
        click.echo("  ✅ AkShare 已安装")
        
        # 测试交易日历
        from src.data_collectors.trading_calendar import is_trading_day
        if is_trading_day():
            click.echo("  ✅ 交易日历可用（今天是交易日）")
        else:
            click.echo("  ✅ 交易日历可用（今天不是交易日）")
        
        # 测试市场数据
        from src.data_collectors.market_data import get_market_overview
        market_data = get_market_overview()
        if "error" not in market_data:
            click.echo("  ✅ 市场数据可用")
        else:
            click.echo(f"  ❌ 市场数据: {market_data['error']}")
            has_error = True
        
    except ImportError:
        click.echo("  ❌ AkShare 未安装")
        has_error = True
    
    # 测试Tavily
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        try:
            from tavily import TavilyClient
            click.echo("  ✅ Tavily 已配置")
        except ImportError:
            click.echo("  ❌ Tavily 未安装（已配置API Key）")
            has_error = True
    else:
        click.echo("  ⚠️  Tavily 未配置（使用示例数据）")
    
    click.echo("\n数据源测试完成！")
    
    if has_error:
        sys.exit(1)


if __name__ == "__main__":
    cli()
