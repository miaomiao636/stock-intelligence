# -*- coding: utf-8 -*-
"""测试盈亏计算功能"""

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.paper_trading.performance import Performance


def test_pnl_calculation(tmp_path):
    """测试盈亏计算"""
    print("=" * 60)
    print("🧪 测试盈亏计算功能")
    print("=" * 60)

    perf = Performance(tmp_path)

    # 测试数据（修正：总资产 = 现金 + 持仓市值）
    account = {
        "account_id": "test",
        "cash": 1303.0,
        "initial_cash": 4000.0,
        "total_equity": 5391.0,  # 1303 + 4088 = 5391
        "net_deposit": 0
    }

    positions = [
        {
            "code": "000725",
            "name": "京东方A",
            "avg_cost": 4.18,
            "quantity": 300,
            "market_value": 1248.0  # 4.16 * 300
        },
        {
            "code": "300015",
            "name": "爱尔眼科",
            "avg_cost": 28.50,
            "quantity": 100,
            "market_value": 2840.0  # 28.40 * 100
        }
    ]

    trades = [
        {
            "code": "000725",
            "action": "sell",
            "amount": 1260.0,  # 卖出收入
            "buy_price": 4.18,
            "quantity": 300,
            "realized_pnl": 6.0  # 1260 - 4.18*300 = 1260 - 1254 = 6
        },
        {
            "code": "300015",
            "action": "sell",
            "amount": 2800.0,  # 卖出收入
            "buy_price": 28.50,
            "quantity": 100,
            "realized_pnl": -50.0  # 2800 - 2850 = -50
        }
    ]

    # 测试1: 计算收益
    print("\n📝 测试1: 计算收益")
    result = perf.calculate_performance(account, positions, trades, "2026-07-09")

    print(f"\n计算结果:")
    print(f"  现金: ¥{result['cash']}")
    print(f"  持仓市值: ¥{result['market_value']}")
    print(f"  总资产: ¥{result['total_equity']}")
    print(f"  成本基础: ¥{result['cost_basis']}")
    print(f"  未实现盈亏: ¥{result['unrealized_pnl']}")
    print(f"  已实现盈亏: ¥{result['realized_pnl']}")
    print(f"  胜率: {result['win_rate']}%")

    # 验证计算
    expected_cost = 4.18 * 300 + 28.50 * 100  # 1254 + 2850 = 4104
    expected_market = 1248 + 2840  # 4088
    expected_unrealized = expected_market - expected_cost  # 4088 - 4104 = -16
    expected_realized = 6 + (-50)  # -44

    assert abs(result["cost_basis"] - expected_cost) < 0.01, \
        f"成本基础错误: 期望{expected_cost}, 实际{result['cost_basis']}"
    print(f"\n✅ 成本基础正确: ¥{result['cost_basis']}")

    assert abs(result["unrealized_pnl"] - expected_unrealized) < 0.01, \
        f"未实现盈亏错误: 期望{expected_unrealized}, 实际{result['unrealized_pnl']}"
    print(f"✅ 未实现盈亏正确: ¥{result['unrealized_pnl']}")

    assert abs(result["realized_pnl"] - expected_realized) < 0.01, \
        f"已实现盈亏错误: 期望{expected_realized}, 实际{result['realized_pnl']}"
    print(f"✅ 已实现盈亏正确: ¥{result['realized_pnl']}")

    # 胜率: 1赢1亏 = 50%
    assert result["win_rate"] == 50.0, f"胜率错误: 期望50%, 实际{result['win_rate']}%"
    print(f"✅ 胜率正确: {result['win_rate']}%")

    # 测试2: 对账验证
    print("\n📝 测试2: 对账验证")
    verification = perf.verify_accounting(account, positions)
    print(f"\n对账结果:")
    print(f"  现金: ¥{verification['cash']}")
    print(f"  持仓市值: ¥{verification['market_value']}")
    print(f"  计算总资产: ¥{verification['calculated_total']}")
    print(f"  账户总资产: ¥{verification['account_total']}")
    print(f"  状态: {verification['status']}")

    assert verification["matches"], "对账失败"
    print("✅ 对账验证通过")

    # 测试3: 空持仓
    print("\n📝 测试3: 空持仓计算")
    empty_result = perf.calculate_performance(
        {"cash": 4000, "initial_cash": 4000, "total_equity": 4000},
        [],
        [],
        "2026-07-08"
    )

    assert empty_result["unrealized_pnl"] == 0, "空持仓未实现盈亏应为0"
    assert empty_result["realized_pnl"] == 0, "空持仓已实现盈亏应为0"
    print("✅ 空持仓计算正确")

    # 测试4: 无交易
    print("\n📝 测试4: 无交易计算")
    no_trade_result = perf.calculate_performance(
        {"cash": 2000, "initial_cash": 4000, "total_equity": 4000},
        [{"code": "000001", "avg_cost": 10, "quantity": 200, "market_value": 2000}],
        [],
        "2026-07-08"
    )

    assert no_trade_result["win_rate"] == 0, "无交易胜率应为0"
    assert no_trade_result["realized_pnl"] == 0, "无交易已实现盈亏应为0"
    print("✅ 无交易计算正确")

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！盈亏计算功能正常")
    print("=" * 60)


if __name__ == "__main__":
    test_pnl_calculation()
