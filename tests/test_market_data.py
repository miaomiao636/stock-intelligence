# -*- coding: utf-8 -*-
"""测试市场数据"""

import pytest
import os
from src.data_collectors.market_data import get_index_data, get_market_overview

pytestmark = pytest.mark.integration


def test_get_index_data():
    """测试获取指数数据"""
    result = get_index_data("000001")
    
    # 检查返回结构
    assert "code" in result
    assert "name" in result
    assert "close" in result
    assert "change_pct" in result
    assert "prev_close" in result
    assert "data_as_of" in result
    
    # 检查数据类型
    assert isinstance(result["close"], float)
    assert isinstance(result["change_pct"], float)


def test_get_market_overview():
    """测试获取市场概况"""
    result = get_market_overview()
    
    # 检查返回结构
    assert "date" in result
    assert "indices" in result
    
    # 检查指数数据
    indices = result["indices"]
    assert "000001" in indices  # 上证指数
    assert "399001" in indices  # 深证成指
    assert "399006" in indices  # 创业板指
    
    # 检查每个指数都有change_pct
    for code, data in indices.items():
        assert "change_pct" in data
        assert "prev_close" in data
        assert isinstance(data["change_pct"], float)


@pytest.mark.skipif(not os.getenv("TUSHARE_TOKEN"), reason="TUSHARE_TOKEN未配置")
def test_full_market_candidate_pool_live():
    from src.data_collectors.universe import get_ranked_candidates
    try:
        result = get_ranked_candidates("2026-07-10", limit=10)
    except Exception as exc:
        if "频率超限" in str(exc):
            pytest.skip("Tushare账户当前处于官方限频窗口；缓存行为由单元测试覆盖")
        raise
    assert result
    assert all(item["daily_amount"] >= 50_000_000 for item in result)


if __name__ == "__main__":
    pytest.main([__file__])
