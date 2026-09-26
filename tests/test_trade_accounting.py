from src.paper_trading.trading_service import TradingService


def _trade(trade_id, action, quantity, amount, fees, code="000001"):
    return {"trade_id": trade_id, "action": action, "quantity": quantity,
            "amount": amount, "price": amount / quantity, "fees": fees, "code": code,
            "executed_at": f"2026-07-{int(trade_id):02d}T09:35:00+08:00"}


def test_fifo_allocates_both_fees_across_partial_and_multiple_lots():
    from src.paper_trading.trade_accounting import calculate_fifo_accounting
    result = calculate_fifo_accounting([
        _trade("1", "buy", 200, 2000, 5), _trade("2", "buy", 100, 1100, 5),
        _trade("3", "sell", 100, 1050, 5), _trade("4", "sell", 200, 2300, 6)])
    sells = [t for t in result["trades"] if t["action"] == "sell"]
    assert [t["net_realized_pnl"] for t in sells] == [42.5, 186.5]
    assert result["summary"]["realized_pnl"] == 229
    assert result["summary"]["win_rate_pct"] == 100
    assert result["summary"]["unallocated_buy_fees"] == 0


def test_unmatched_sell_is_missing_not_a_zero_pnl_win_rate_sample():
    from src.paper_trading.trade_accounting import calculate_fifo_accounting
    result = calculate_fifo_accounting([_trade("1", "buy", 100, 1000, 5),
                                        _trade("2", "sell", 200, 2200, 6)])
    assert result["trades"][1]["accounting_status"] == "missing_buy_history"
    assert result["trades"][1]["net_realized_pnl"] is None
    assert result["trades"][1]["matched_quantity"] == 100
    assert result["summary"]["unmatched_sells"] == 1
    assert result["summary"]["win_rate_pct"] is None


def test_accounting_reads_all_rows_and_never_rewrites_cash_or_history(tmp_path, monkeypatch):
    service = TradingService(tmp_path)
    service.initialize_account(20000)
    monkeypatch.setattr(service.ledger, "list_trades", lambda **kw: (_ for _ in ()).throw(
        AssertionError("Accounting must not use a capped list")))
    assert service.get_trade_accounting()["summary"]["total_trades"] == 0
    assert service.get_performance_metrics()["closed_trades"] == 0
    assert service.get_account()["cash"] == 20000


def test_over_one_thousand_historical_trades_preserve_fees_and_correct_win_rate(tmp_path):
    from datetime import datetime, timedelta
    service = TradingService(tmp_path)
    service.initialize_account(20000)
    rows = []
    for i in range(1004):
        action = "buy" if i % 2 == 0 else "sell"
        stamp = (datetime(2026, 7, 13, 9, 35) + timedelta(seconds=i)).isoformat()
        rows.append((str(i), action, stamp))
    with service.ledger.transaction() as conn:
        conn.executemany("""INSERT INTO trading_orders
            (order_id,idempotency_key,run_id,recommendation_id,code,name,action,quantity,planned_price,status,created_at,updated_at)
            VALUES(?,?, 'r','rec','000001','test',?,100,10,'filled',?,?)""",
            [(i, i, action, stamp, stamp) for i, action, stamp in rows])
        conn.executemany("""INSERT INTO trading_trades
            (trade_id,order_id,code,action,quantity,price,amount,fees,realized_pnl,executed_at,trade_date)
            VALUES(?,?,'000001',?,100,10,?,5,?,?, '2026-07-13')""",
            [(i, i, action, 1000 if action == "buy" else 1006, None if action == "buy" else 1, stamp)
             for i, action, stamp in rows])
        conn.execute("UPDATE trading_accounts SET cash=17992,realized_pnl=502 WHERE account_id='default'")
    metrics = service.get_performance_metrics()
    assert metrics["closed_trades"] == 502
    assert metrics["total_fees"] == 5020
    assert metrics["trade_win_rate_pct"] == 0
    assert metrics["realized_pnl"] == -2008
    assert service.get_account()["cash"] == 17992
    assert service.ledger.get_account()["realized_pnl"] == 502
