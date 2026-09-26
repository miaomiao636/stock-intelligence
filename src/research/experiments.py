"""Small, deterministic research diagnostics, not a fitted prediction model.

Observations are explicit non-overlapping holding periods. `cost_bps` is the
complete estimated round-trip cost in basis points; returns are decimal ratios.
This module does not download data, train Qlib, infer tradeability, annualize a
sparse series, or change the champion strategy.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean

from src.research.review import timestamp


def walk_forward_splits(dates: list[str], *, train_size: int, test_size: int,
                        validation_size: int = 0, gap: int = 1,
                        step_size: int | None = None) -> list[dict]:
    """Sliding chronological windows. `gap` counts supplied trading dates.

    The caller must set gap >= the overlapping label horizon and purge any
    additional event overlap; this function does not infer a safe embargo.
    """
    for field, value, minimum in (("train_size", train_size, 1), ("test_size", test_size, 1), ("validation_size", validation_size, 0), ("gap", gap, 0)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"invalid {field}")
    step = test_size if step_size is None else step_size
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise ValueError("invalid step_size")
    normalized = [timestamp(day) for day in dates]
    if normalized != sorted(set(normalized)):
        raise ValueError("dates must be unique and strictly chronological")
    total = train_size + gap + test_size
    if validation_size:
        total += validation_size + gap
    folds = []
    for start in range(0, len(dates) - total + 1, step):
        train_end = start + train_size
        val_start = train_end + gap
        val_end = val_start + validation_size
        test_start = val_end + gap if validation_size else val_start
        folds.append({"train": dates[start:train_end], "validation": dates[val_start:val_end] if validation_size else [], "test": dates[test_start:test_start + test_size]})
    return folds


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _observations(observations: list[dict]) -> list[dict]:
    if not isinstance(observations, list) or not observations:
        raise ValueError("observations are required; data unavailable")
    required = {"stock_code", "as_of", "available_at", "entry_at", "exit_at", "label_known_at", "can_execute", "cost_bps", "benchmark_return", "factor_value", "forward_return", "data_version", "strategy_version"}
    result, seen = [], set()
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError("observation must be an object")
        missing = sorted(required - row.keys())
        if missing:
            raise ValueError("missing required fields: " + ", ".join(missing))
        copy = dict(row)
        for key in ("stock_code", "data_version", "strategy_version"):
            if not isinstance(row[key], str) or not row[key].strip() or row[key] == "unknown":
                raise ValueError(f"{key} must be explicit")
        for key in ("as_of", "available_at", "entry_at", "exit_at", "label_known_at"):
            copy[key] = timestamp(row[key])
        if copy["available_at"] > copy["as_of"]:
            raise ValueError("future feature: available_at exceeds as_of")
        if not copy["as_of"] < copy["entry_at"] < copy["exit_at"] <= copy["label_known_at"]:
            raise ValueError("observation time order must be as_of < entry < exit <= label_known_at")
        if not isinstance(row["can_execute"], bool):
            raise ValueError("can_execute must be explicit boolean")
        for key in ("cost_bps", "benchmark_return", "factor_value", "forward_return"):
            copy[key] = _number(row[key], key)
        if not 0 <= copy["cost_bps"] <= 10000:
            raise ValueError("cost_bps must be between 0 and 10000")
        if copy["forward_return"] < -1 or copy["benchmark_return"] < -1:
            raise ValueError("simple returns cannot be less than -1")
        identity = (copy["as_of"], copy["stock_code"])
        if identity in seen:
            raise ValueError("duplicate stock/as_of observation")
        seen.add(identity)
        result.append(copy)
    return result


def _ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    left = 0
    while left < len(order):
        right = left + 1
        while right < len(order) and values[order[right]] == values[order[left]]:
            right += 1
        rank = (left + 1 + right) / 2
        for index in order[left:right]:
            ranks[index] = rank
        left = right
    return ranks


def factor_rank_ic(observations: list[dict]) -> dict:
    """Cross-sectional Spearman IC per signal timestamp; ties use mean ranks."""
    groups = defaultdict(list)
    for row in _observations(observations):
        groups[row["as_of"]].append(row)
    periods = []
    for as_of, group in sorted(groups.items()):
        x = _ranks([row["factor_value"] for row in group])
        y = _ranks([row["forward_return"] for row in group])
        mx, my = mean(x), mean(y)
        denominator = math.sqrt(sum((v - mx) ** 2 for v in x) * sum((v - my) ** 2 for v in y))
        ic = sum((a - mx) * (b - my) for a, b in zip(x, y)) / denominator if len(group) >= 3 and denominator else None
        periods.append({"as_of": as_of, "sample_count": len(group), "rank_ic": ic})
    usable = [period["rank_ic"] for period in periods if period["rank_ic"] is not None]
    return {"periods": periods, "mean_rank_ic": mean(usable) if usable else None,
            "valid_periods": len(usable), "total_periods": len(periods),
            "interpretation": "descriptive correlation; not proof of profitability"}


def _portfolio_metrics(period_returns, benchmark_returns, *, missing):
    equity = benchmark = peak = 1.0
    max_drawdown = 0.0
    for ret, benchmark_ret in zip(period_returns, benchmark_returns):
        equity *= 1 + ret
        benchmark *= 1 + benchmark_ret
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return {"net_return": equity - 1, "benchmark_return": benchmark - 1,
            "excess_return": equity - benchmark, "max_drawdown": max_drawdown,
            "period_returns": period_returns, "period_count": len(period_returns),
            "unexecutable_count": missing}


def compare_net_baselines(observations: list[dict], *, top_k: int = 5) -> dict:
    """Top-factor and equal-weight portfolios on exactly the same supplied pool.

    Ranking is done before fill eligibility. A non-executable allocation stays
    in cash, so it is not silently replaced by a hindsight-selected winner.
    """
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    groups = defaultdict(list)
    for row in _observations(observations):
        groups[row["as_of"]].append(row)
    factor_returns, equal_returns, benchmarks, periods = [], [], [], []
    factor_missing = equal_missing = 0
    previous_exit = None
    for as_of, group in sorted(groups.items()):
        entries = {row["entry_at"] for row in group}
        exits = {row["exit_at"] for row in group}
        benchmark_values = {row["benchmark_return"] for row in group}
        if len(entries) != 1 or len(exits) != 1 or len(benchmark_values) != 1:
            raise ValueError("each period must share entry, exit and benchmark")
        entry, exit_at = next(iter(entries)), next(iter(exits))
        if previous_exit and entry < previous_exit:
            raise ValueError("overlapping holding periods cannot be compounded")
        previous_exit = exit_at
        selected = sorted(group, key=lambda row: (-row["factor_value"], row["stock_code"]))[:top_k]
        def net(row):
            return row["forward_return"] - row["cost_bps"] / 10000 if row["can_execute"] else 0.0
        if any(net(row) < -1 for row in group):
            raise ValueError("net return below -1 is outside unlevered simulation")
        factor_returns.append(mean(net(row) for row in selected))
        equal_returns.append(mean(net(row) for row in group))
        factor_missing += sum(not row["can_execute"] for row in selected)
        equal_missing += sum(not row["can_execute"] for row in group)
        benchmarks.append(next(iter(benchmark_values)))
        periods.append({"as_of": as_of, "entry_at": entry, "exit_at": exit_at,
                        "universe_count": len(group), "selected_codes": [row["stock_code"] for row in selected]})
    return {"factor": _portfolio_metrics(factor_returns, benchmarks, missing=factor_missing),
            "equal_weight": _portfolio_metrics(equal_returns, benchmarks, missing=equal_missing),
            "periods": periods, "auto_promoted": False, "return_unit": "decimal_ratio",
            "cost_basis": "caller_supplied_complete_round_trip_bps",
            "cash_return_assumption": 0.0}


def evaluate_experiment(payload: dict) -> dict:
    """Fail closed on missing data, without presenting synthetic backtests."""
    base = {"auto_promoted": False, "validation_status": "descriptive_only",
            "warnings": ["仅对输入样本计算诊断，不代表完成训练或独立样本外验证。",
                         "调用方须验证历史股票池、退市样本、实际可成交条件和完整双边成本。",
                         "未模拟整数手、资金容量和市场冲击，不能替代账户级回测。"]}
    try:
        if not payload.get("protocol_version"):
            raise ValueError("protocol_version is required")
        observations = _observations(payload.get("observations"))
        if payload.get("as_of"):
            cutoff = timestamp(payload["as_of"], end_of_day=True)
            if any(row["label_known_at"] > cutoff for row in observations):
                raise ValueError("future return label exceeds experiment as_of")
        baseline = compare_net_baselines(observations, top_k=payload.get("top_k", 5))
        return {**base, "status": "completed", "errors": [], "rank_ic": factor_rank_ic(observations),
                "comparison": baseline, "observation_count": len(observations),
                "data_versions": sorted({row["data_version"] for row in observations}),
                "strategy_versions": sorted({row["strategy_version"] for row in observations})}
    except (ValueError, TypeError, KeyError) as exc:
        return {**base, "status": "blocked", "errors": [str(exc)]}
