# -*- coding: utf-8 -*-
"""测试跨日持仓功能"""

import sys
from pathlib import Path
from datetime import date, timedelta

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.paper_trading.portfolio import PaperPortfolio


def test_cross_day_positions():
    """测试跨日持仓延续"""
    print("=" * 60)
    print("🧪 测试跨日持仓功能")
    print("=" * 60)

    # 使用测试目录
    test_dir = Path("/tmp/test_portfolio")
    if test_dir.exists():
        import shutil
        shutil.rmtree(test_dir)

    portfolio = PaperPortfolio(data_dir=test_dir)

    # 测试1: 空持仓
    print("\n📝 测试1: 初始状态")
    positions = portfolio.get_positions()
    assert positions == [], f"期望空列表，实际: {positions}"
    print("✅ 初始状态正确: 空持仓")

    # 测试2: 添加持仓
    print("\n📝 测试2: 添加持仓")
    test_positions = [
        {"code": "000725", "name": "京东方A", "avg_cost": 4.18, "quantity": 300},
        {"code": "300015", "name": "爱尔眼科", "avg_cost": 28.50, "quantity": 100},
        {"code": "000001", "name": "平安银行", "avg_cost": 11.20, "quantity": 200},
    ]

    for pos in test_positions:
        portfolio.add_position(pos)

    positions = portfolio.get_positions()
    assert len(positions) == 3, f"期望3个持仓，实际: {len(positions)}"
    print(f"✅ 添加持仓成功: {len(positions)} 个")

    # 测试3: 保存快照
    print("\n📝 测试3: 保存快照")
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    # 模拟昨天的持仓（少一个）
    yesterday_positions = test_positions[:2]
    portfolio.save_positions(yesterday_positions, yesterday_str)

    # 验证昨天快照
    yesterday_result = portfolio.get_positions(yesterday_str)
    assert len(yesterday_result) == 2, f"期望昨天2个持仓，实际: {len(yesterday_result)}"
    print(f"✅ 昨天快照正确: {len(yesterday_result)} 个")

    # 测试4: 今天持仓不变
    print("\n📝 测试4: 今天持仓不变")
    today_result = portfolio.get_positions()
    assert len(today_result) == 3, f"期望今天3个持仓，实际: {len(today_result)}"
    print(f"✅ 今天持仓正确: {len(today_result)} 个")

    # 测试5: 跨日查询
    print("\n📝 测试5: 跨日查询")
    # 查询今天（应该返回current.json）
    today_query = portfolio.get_positions(today_str)
    assert len(today_query) == 3, f"查询今天失败，期望3，实际: {len(today_query)}"

    # 查询昨天（应该返回快照）
    yesterday_query = portfolio.get_positions(yesterday_str)
    assert len(yesterday_query) == 2, f"查询昨天失败，期望2，实际: {len(yesterday_query)}"

    # 查询不存在的日期
    future_str = (date.today() + timedelta(days=10)).isoformat()
    future_query = portfolio.get_positions(future_str)
    assert future_query == [], f"查询未来日期失败，期望空，实际: {len(future_query)}"

    print("✅ 跨日查询正确")

    # 测试6: 更新持仓
    print("\n📝 测试6: 更新持仓")
    portfolio.update_position("000725", {"current_price": 4.25, "market_value": 1275})

    updated = portfolio.get_position("000725")
    assert updated["current_price"] == 4.25, f"更新失败，期望4.25，实际: {updated.get('current_price')}"
    print("✅ 更新持仓正确")

    # 测试7: 移除持仓
    print("\n📝 测试7: 移除持仓")
    portfolio.remove_position("000001")

    after_remove = portfolio.get_positions()
    assert len(after_remove) == 2, f"移除后期望2个，实际: {len(after_remove)}"

    codes = [p["code"] for p in after_remove]
    assert "000001" not in codes, "000001应该已被移除"
    assert "000725" in codes, "000725应该还在"
    assert "300015" in codes, "300015应该还在"
    print("✅ 移除持仓正确")

    # 测试8: 历史快照列表
    print("\n📝 测试8: 历史快照列表")
    snapshots = portfolio.get_historical_snapshots()
    assert len(snapshots) >= 2, f"期望至少2个快照，实际: {len(snapshots)}"
    print(f"✅ 历史快照列表正确: {len(snapshots)} 个")

    # 测试9: 验证文件结构
    print("\n📝 测试9: 验证文件结构")
    current_file = test_dir / "positions" / "current.json"
    snapshots_dir = test_dir / "positions" / "snapshots"

    assert current_file.exists(), "current.json 不存在"
    assert snapshots_dir.exists(), "snapshots目录 不存在"

    snapshot_files = list(snapshots_dir.glob("????-??-??.json"))
    assert len(snapshot_files) >= 2, f"期望至少2个快照文件，实际: {len(snapshot_files)}"
    print(f"✅ 文件结构正确: current.json + {len(snapshot_files)} 个快照")

    # 清理测试目录
    import shutil
    shutil.rmtree(test_dir)

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！跨日持仓功能正常")
    print("=" * 60)


if __name__ == "__main__":
    test_cross_day_positions()
