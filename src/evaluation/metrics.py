# -*- coding: utf-8 -*-
"""评估指标计算"""

from typing import Dict, Optional


def calculate_return(entry_price: float, exit_price: float) -> float:
    """计算收益率"""
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


def calculate_relative_return(stock_return: float, benchmark_return: float) -> float:
    """计算相对收益"""
    return stock_return - benchmark_return


def calculate_max_drawdown(prices: list) -> float:
    """计算最大回撤"""
    if not prices or len(prices) < 2:
        return 0.0
    
    peak = prices[0]
    max_dd = 0.0
    
    for price in prices:
        if price > peak:
            peak = price
        dd = (peak - price) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    return max_dd


def calculate_win_rate(results: list) -> float:
    """计算胜率（含中间状态：浮盈和接近目标也算部分成功）"""
    # 过滤掉status=error的记录
    valid_results = [r for r in results if r.get("status") != "error"]

    if not valid_results:
        return 0.0

    # 达标=完全成功，接近目标=较高成功，浮盈=部分成功
    wins = 0
    for r in valid_results:
        status = r.get("status", "")
        ret = r.get("return_pct", 0)

        if status == "hit":
            # 完全达标
            wins += 1.0
        elif status == "near_target":
            # 接近目标（>=70%），给予更高权重
            wins += 0.75
        elif ret > 0:
            # 浮盈但未达标
            wins += 0.5

    return wins / len(valid_results) * 100


def calculate_profit_loss_ratio(results: list) -> float:
    """计算盈亏比"""
    profits = [r["return_pct"] for r in results if r.get("return_pct", 0) > 0]
    losses = [abs(r["return_pct"]) for r in results if r.get("return_pct", 0) < 0]
    
    if not losses:
        return float("inf") if profits else 0.0
    
    avg_profit = sum(profits) / len(profits) if profits else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    
    if avg_loss == 0:
        return float("inf") if avg_profit else 0.0
    
    return avg_profit / avg_loss


def calculate_metrics(
    stock_results: list,
    benchmark_return: float = 0.0
) -> Dict:
    """计算综合指标"""
    
    # 过滤掉status=error的记录
    valid_results = [r for r in stock_results if r.get("status") != "error"]
    
    # 提取收益率
    returns = [r.get("return_pct", 0) for r in valid_results]
    
    # 计算各项指标
    avg_return = sum(returns) / len(returns) if returns else 0
    win_rate = calculate_win_rate(stock_results)
    profit_loss_ratio = calculate_profit_loss_ratio(stock_results)
    
    # 计算超额收益
    excess_returns = [r - benchmark_return for r in returns]
    avg_excess = sum(excess_returns) / len(excess_returns) if excess_returns else 0
    
    # 计算Sharpe Ratio（简化版，假设无风险利率=2%/年，日化）
    import math
    if len(returns) > 1:
        avg_ret = sum(returns) / len(returns)
        variance = sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0
        sharpe = (avg_ret - 0.008) / std_dev if std_dev > 0 else 0  # 0.008% ≈ 2%/252
    else:
        sharpe = 0

    return {
        "total_recommendations": len(stock_results),
        "avg_return_pct": round(avg_return, 2),
        "avg_excess_return_pct": round(avg_excess, 2),
        "benchmark_return_pct": round(benchmark_return, 2),
        "win_rate_pct": round(win_rate, 2),
        "profit_loss_ratio": round(profit_loss_ratio, 2),
        "max_return_pct": round(max(returns), 2) if returns else 0,
        "min_return_pct": round(min(returns), 2) if returns else 0,
        "sharpe_ratio": round(sharpe, 2),
    }
