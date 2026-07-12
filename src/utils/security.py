# -*- coding: utf-8 -*-
"""安全工具：输入校验，防止路径穿越等注入攻击"""

import re
from typing import Optional

# 严格日期格式：YYYY-MM-DD，拒绝任何路径分隔符或点号序列
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 报告类型白名单
_REPORT_TYPES = {"morning", "closing"}


def validate_date_str(date_str: Optional[str], field: str = "date_str") -> Optional[str]:
    """校验日期字符串，防止路径穿越。

    仅允许 YYYY-MM-DD 格式，拒绝 ../、绝对路径、空字节等。
    None 视为合法（调用方自行处理为今天）。

    Raises:
        ValueError: 当 date_str 非法时
    """
    if date_str is None:
        return None
    s = str(date_str)
    if not _DATE_RE.match(s):
        raise ValueError(
            f"非法 {field}: {s!r}，需 YYYY-MM-DD 格式（禁止路径分隔符）"
        )
    # 额外防御：解析为真实日期，拒绝 2026-13-45 这类
    from datetime import datetime
    datetime.strptime(s, "%Y-%m-%d")
    return s


def validate_report_type(report_type: str) -> str:
    """校验报告类型，仅允许白名单值。"""
    if report_type not in _REPORT_TYPES:
        raise ValueError(f"非法 report_type: {report_type!r}，仅允许 {_REPORT_TYPES}")
    return report_type
