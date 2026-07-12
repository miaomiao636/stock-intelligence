# -*- coding: utf-8 -*-
"""测试置信度校验功能"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analysis.validators import validate_llm_output


def test_confidence_validation():
    """测试置信度校验"""
    print("=" * 60)
    print("🧪 测试置信度校验功能")
    print("=" * 60)

    # 测试1: 有效数据（有confidence）
    print("\n📝 测试1: 有效数据")
    valid_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [
            {
                "sector_name": "AI算力",
                "rating": 4,
                "reason": "测试推荐理由",
                "horizon": "short"
            }
        ],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "setup_ready",
                "confidence": 4,
                "horizon": "short",
                "horizon_days": 3,
                "current_price": 38.50,
                "entry_price": 37.50,
                "target_price": 40.00,
                "stop_loss_price": 36.50,
                "reason": "测试推荐理由"
            }
        ],
        "risk_warnings": ["测试风险提示"],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(valid_data)
    assert is_valid, f"有效数据应该通过校验，错误: {error}"
    assert data["stock_recommendations"][0]["confidence"] == 4
    print("✅ 有效数据校验通过")

    # 测试2: 缺少confidence字段
    print("\n📝 测试2: 缺少confidence字段")
    no_confidence_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "setup_ready",
                "horizon": "short",
                "current_price": 38.50,
                "entry_price": 37.50,
                "target_price": 40.00,
                "stop_loss_price": 36.50,
                "reason": "测试推荐理由"
            }
        ],
        "risk_warnings": [],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(no_confidence_data)
    assert not is_valid, "缺少confidence应该失败"
    assert "confidence" in error.lower(), f"错误信息应包含confidence，实际: {error}"
    print(f"✅ 缺少confidence正确拒绝: {error}")

    # 测试3: confidence超出范围
    print("\n📝 测试3: confidence超出范围")
    invalid_confidence_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "setup_ready",
                "confidence": 6,
                "horizon": "short",
                "current_price": 38.50,
                "entry_price": 37.50,
                "target_price": 40.00,
                "stop_loss_price": 36.50,
                "reason": "测试推荐理由"
            }
        ],
        "risk_warnings": [],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(invalid_confidence_data)
    assert not is_valid, "confidence=6应该失败"
    assert "1-5" in error or "6" in error, f"错误信息应提示范围，实际: {error}"
    print(f"✅ 超出范围正确拒绝: {error}")

    # 测试4: setup_ready缺少价格字段
    print("\n📝 测试4: setup_ready缺少价格字段")
    missing_price_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "setup_ready",
                "confidence": 4,
                "horizon": "short",
                "reason": "测试推荐理由"
            }
        ],
        "risk_warnings": [],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(missing_price_data)
    assert not is_valid, "setup_ready缺少价格应该失败"
    assert "price" in error.lower() or "价格" in error, f"错误信息应提示价格，实际: {error}"
    print(f"✅ 缺少价格正确拒绝: {error}")

    # 测试5: watch类型不需要价格
    print("\n📝 测试5: watch类型不需要价格")
    watch_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "watch",
                "confidence": 2,
                "horizon": "short",
                "reason": "观望"
            }
        ],
        "risk_warnings": [],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(watch_data)
    assert is_valid, f"watch类型应该通过校验，错误: {error}"
    print("✅ watch类型校验通过")

    # 测试6: 无效action
    print("\n📝 测试6: 无效action")
    invalid_action_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "buy_now",
                "confidence": 4,
                "horizon": "short",
                "reason": "测试"
            }
        ],
        "risk_warnings": [],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(invalid_action_data)
    assert not is_valid, "无效action应该失败"
    assert "action" in error.lower(), f"错误信息应提示action，实际: {error}"
    print(f"✅ 无效action正确拒绝: {error}")

    # 测试7: 无效horizon
    print("\n📝 测试7: 无效horizon")
    invalid_horizon_data = '''{
        "market_regime": "neutral",
        "market_summary": "测试",
        "sector_recommendations": [],
        "stock_recommendations": [
            {
                "code": "002475",
                "name": "立讯精密",
                "sector": "消费电子",
                "action": "watch",
                "confidence": 3,
                "horizon": "very_long",
                "reason": "测试"
            }
        ],
        "risk_warnings": [],
        "news_sources": []
    }'''

    is_valid, data, error = validate_llm_output(invalid_horizon_data)
    assert not is_valid, "无效horizon应该失败"
    assert "horizon" in error.lower(), f"错误信息应提示horizon，实际: {error}"
    print(f"✅ 无效horizon正确拒绝: {error}")

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！置信度校验功能正常")
    print("=" * 60)


if __name__ == "__main__":
    test_confidence_validation()
