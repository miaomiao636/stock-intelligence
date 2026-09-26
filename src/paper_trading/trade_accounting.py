"""Read-only FIFO reconstruction. Execution amounts already include slippage.

Never rewrite historical cash or trades. Incomplete imports are explicitly marked
missing; they are not counted as zero-return trades or win-rate observations.
"""

from collections import defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP


def _money(value):
    return Decimal(str(value or 0))


def _rounded(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_fifo_accounting(trades):
    lots = defaultdict(deque)
    annotated = []
    # Stable ordering preserves SQLite row order for equal execution timestamps.
    for original in sorted(trades, key=lambda item: item.get("executed_at", "")):
        trade = dict(original)
        quantity = int(trade["quantity"])
        amount, fees = _money(trade["amount"]), _money(trade["fees"])
        trade.update(net_realized_pnl=None, matched_quantity=0, allocated_buy_fees=0.0)
        if trade["action"] == "buy":
            lots[trade["code"]].append({"quantity": quantity, "amount": amount, "fees": fees})
            trade["accounting_status"] = "not_applicable"
        else:
            remaining, basis, buy_fees = quantity, Decimal(0), Decimal(0)
            queue = lots[trade["code"]]
            while remaining and queue:
                lot = queue[0]
                used = min(remaining, lot["quantity"])
                fraction = Decimal(used) / lot["quantity"]
                used_amount = lot["amount"] * fraction
                used_fees = lot["fees"] * fraction
                basis += used_amount
                buy_fees += used_fees
                remaining -= used
                lot["quantity"] -= used
                lot["amount"] -= used_amount
                lot["fees"] -= used_fees
                if not lot["quantity"]:
                    queue.popleft()
            trade["matched_quantity"] = quantity - remaining
            trade["allocated_buy_fees"] = _rounded(buy_fees)
            trade["accounting_status"] = "complete" if not remaining else "missing_buy_history"
            if not remaining:
                trade["net_realized_pnl"] = _rounded(amount - fees - basis - buy_fees)
        annotated.append(trade)
    complete = [t for t in annotated if t["accounting_status"] == "complete"]
    missing = [t for t in annotated if t["accounting_status"] == "missing_buy_history"]
    realized = round(sum(t["net_realized_pnl"] for t in complete), 2)
    return {"trades": annotated, "summary": {
        "total_trades": len(annotated), "closed_trades": len(complete),
        "unmatched_sells": len(missing), "accounting_status": "incomplete" if missing else "complete",
        "realized_pnl": None if missing else realized, "known_realized_pnl": realized,
        "win_rate_pct": round(sum(t["net_realized_pnl"] > 0 for t in complete) / len(complete) * 100, 2) if complete else None,
        "unallocated_buy_fees": _rounded(sum((lot["fees"] for queue in lots.values() for lot in queue), Decimal(0))),
    }}
