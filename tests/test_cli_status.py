# -*- coding: utf-8 -*-
"""CLI 数据源状态图标。"""

from cli import _source_status_icon


def test_source_status_icons_distinguish_success_degraded_and_deferred():
    assert _source_status_icon("ok_50") == "✅"
    assert _source_status_icon("success") == "✅"
    assert _source_status_icon("fallback") == "⚠️"
    assert _source_status_icon("deferred_to_0935") == "ℹ️"
    assert _source_status_icon("error") == "❌"
