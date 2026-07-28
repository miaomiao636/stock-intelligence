from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from deploy.import_trading_ledger import import_trading_ledger
from src.paper_trading.trading_service import TradingService


TZ = ZoneInfo("Asia/Shanghai")


def _quote(at):
    return {
        "code": "000001",
        "price": 10.0,
        "quote_time": at.isoformat(),
        "trade_date": at.date().isoformat(),
        "trade_status": "trading",
        "source_time_reliable": True,
    }


def test_import_replaces_only_trading_ledger_and_creates_backup(tmp_path):
    now = datetime(2026, 7, 28, 9, 35, tzinfo=TZ)
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source = TradingService(source_dir, now_provider=lambda: now)
    target = TradingService(target_dir, now_provider=lambda: now)
    source.initialize_account(8000)
    target.initialize_account(4000)
    order = source.propose_order(
        run_id="source-run",
        recommendation_id="REC-IMPORT",
        code="000001",
        name="平安银行",
        sector="金融",
        action="buy",
        quantity=100,
        planned_price=10,
        min_price=9.9,
        max_price=10.1,
        stop_price=9.7,
        target_price=10.6,
    )
    source.confirm_automatically(order["order_id"])
    assert source.execute_ready_order(order["order_id"], _quote(now))["success"]

    source_db = source_dir / "stock_intelligence.db"
    target_db = target_dir / "stock_intelligence.db"
    preview = import_trading_ledger(source_db, target_db)
    assert preview["applied"] is False
    assert target.get_positions() == []

    result = import_trading_ledger(
        source_db,
        target_db,
        apply=True,
        backup_dir=tmp_path / "backups",
    )

    assert result["applied"] is True
    assert Path(result["backup"]).is_file()
    assert target.get_account()["initial_cash"] == 8000
    assert target.get_positions()[0]["code"] == "000001"
    assert len(target.ledger.list_trades()) == 1
