# -*- coding: utf-8 -*-
"""输出校验模块"""

import json
import re
from typing import Dict, Tuple

from pydantic import ValidationError

from src.models import Recommendation


def _extract_json_text(raw_output: str) -> str:
    """从代码块或说明文字中提取最外层 JSON 对象。"""
    text = str(raw_output or "").lstrip("\ufeff").strip()
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = next((item for item in fenced if "{" in item), fenced[0]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start:end + 1]
    return text.strip()


def _remove_trailing_commas(text: str) -> str:
    """安全移除对象/数组结尾逗号，不触碰字符串内部内容。"""
    result = []
    in_string = False
    escaped = False
    length = len(text)
    for index, char in enumerate(text):
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            result.append(char)
            continue
        if char == ",":
            cursor = index + 1
            while cursor < length and text[cursor].isspace():
                cursor += 1
            if cursor < length and text[cursor] in "]}":
                continue
        result.append(char)
    return "".join(result)


def validate_llm_output(raw_output: str) -> Tuple[bool, Dict, str]:
    """校验LLM输出
    
    Returns:
        (is_valid, parsed_data, error_message)
    """
    # 1. 尝试解析JSON
    try:
        json_str = _remove_trailing_commas(_extract_json_text(raw_output))
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return False, {}, f"JSON解析失败: {e}"
    
    # 2. 检查必要字段
    required_fields = ["sector_recommendations", "stock_recommendations"]
    for field in required_fields:
        if field not in data:
            return False, {}, f"缺少必要字段: {field}"
    
    # 3. 检查列表字段
    if not isinstance(data.get("sector_recommendations"), list):
        return False, {}, "sector_recommendations必须是数组"
    
    if not isinstance(data.get("stock_recommendations"), list):
        return False, {}, "stock_recommendations必须是数组"
    
    # 4. 校验板块推荐
    for i, sector in enumerate(data.get("sector_recommendations", [])):
        if "sector_name" not in sector:
            return False, {}, f"板块推荐[{i}]缺少sector_name"
        if "rating" not in sector:
            return False, {}, f"板块推荐[{i}]缺少rating"
    
    # 5. 校验个股推荐
    for i, stock in enumerate(data.get("stock_recommendations", [])):
        if "code" not in stock:
            return False, {}, f"个股推荐[{i}]缺少code"
        if "name" not in stock:
            return False, {}, f"个股推荐[{i}]缺少name"
        if "action" not in stock:
            return False, {}, f"个股推荐[{i}]缺少action"

        # 校验action类型
        valid_actions = ["watch", "track", "setup_ready", "hold", "reduce", "avoid"]
        if stock["action"] not in valid_actions:
            return False, {}, f"个股推荐[{i}]的action无效: {stock['action']}"

        # 校验confidence字段（必须存在且在1-5之间）
        if "confidence" not in stock:
            return False, {}, f"个股推荐[{i}]缺少confidence字段"
        confidence = stock["confidence"]
        if not isinstance(confidence, (int, float)) or confidence < 1 or confidence > 5:
            return False, {}, f"个股推荐[{i}]的confidence必须在1-5之间，当前: {confidence}"

        # 校验价格字段（setup_ready必须有完整价格）
        if stock["action"] == "setup_ready":
            required_price_fields = ["current_price", "entry_price", "target_price", "stop_loss_price"]
            for price_field in required_price_fields:
                if price_field not in stock or stock[price_field] is None:
                    return False, {}, f"个股推荐[{i}]的setup_ready缺少{price_field}"
                if not isinstance(stock[price_field], (int, float)) or stock[price_field] <= 0:
                    return False, {}, f"个股推荐[{i}]的{price_field}必须为正数，当前: {stock[price_field]}"

        # 校验horizon字段
        if "horizon" in stock:
            valid_horizons = ["short", "medium", "long"]
            if stock["horizon"] not in valid_horizons:
                return False, {}, f"个股推荐[{i}]的horizon无效: {stock['horizon']}"
    
    # 6. 确保所有列表字段存在
    list_fields = [
        "sector_recommendations",
        "stock_recommendations",
        "global_mappings",
        "portfolio_alerts",
        "expiry_alerts",
        "risk_warnings",
        "news_sources",
    ]
    for field in list_fields:
        if field not in data:
            data[field] = []
    
    return True, data, ""


def apply_action_rules(data: Dict) -> Dict:
    """应用action规则"""
    
    for stock in data.get("stock_recommendations", []):
        action = stock.get("action")
        
        # setup_ready/track必须有目标
        if action in ["setup_ready", "track"]:
            if "target_return_pct" not in stock:
                stock["target_return_pct"] = 5.0  # 默认目标
        
        # watch可以没有目标
        if action == "watch":
            stock.pop("target_return_pct", None)
        
        # avoid不允许正收益目标
        if action == "avoid":
            stock["target_return_pct"] = -5.0
    
    return data
