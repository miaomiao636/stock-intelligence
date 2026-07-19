# -*- coding: utf-8 -*-
"""Dashboard API authentication regression checks."""

from pathlib import Path


DASHBOARD_HTML = Path(__file__).parents[1] / "dashboard" / "index.html"


def test_dashboard_has_no_global_api_key_gate_and_keeps_cash_change_protected():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert 'id="auth-gate"' not in html
    assert "dashboardApiKey" not in html
    assert 'id="cash-update-key"' in html
    assert "async function updateAvailableCash" in html
    assert 'autocomplete="new-password"' in html
    assert "function clearCashUpdateKey" in html
    assert "window.addEventListener('pageshow', clearCashUpdateKey)" in html


def test_dashboard_api_fetch_only_adds_an_explicit_write_authentication_header():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "async function apiFetch" in html
    assert "headers.set('X-API-Key', apiKey)" in html
    assert "sessionStorage.getItem(DASHBOARD_API_KEY_STORAGE)" not in html
    assert "window.onload = bootstrap" in html


def test_dashboard_escapes_untrusted_history_and_tab_content():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")
    history = html.split("async function showHistoryDetail", 1)[1].split(
        "// ============ MODALS", 1
    )[0]

    assert "encodeURIComponent(dateStr)" in history
    assert "${esc(s.name||'-')}" in history
    assert "${esc(s.code||'-')}" in history
    assert "${esc(reason)}" in history
    assert "${esc(t.entry_condition)}" in history
    assert "${esc(s.sector_name||s.name||'-')}" in history
    assert "${esc(s.reason||'-')}" in history
    assert "onclick=\"switchTimeTab('${r.period}')\"" not in html


def test_dashboard_uses_sqlite_performance_and_trade_shapes():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "apiFetch('/api/performance')" in html
    assert "扣费后总收益" in html
    assert "最大回撤" in html
    assert "Number(t.fees||0)" in html
    assert "Number(t.slippage||0)" in html
    assert "trades_executed" not in html


def test_dashboard_labels_fallback_as_observation_and_can_retry_analysis():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "观察板块" in html
    assert "无AI目标" in html
    assert "重新生成AI分析" in html
    assert "apiFetch('/api/recommendation/regenerate'" in html


def test_dashboard_uses_a_compact_collapsible_cockpit_without_storing_credentials():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert 'class="primary-grid"' in html
    assert 'class="secondary-grid"' in html
    assert 'dashboardPanelStateV1' in html
    assert "function togglePanel" in html
    assert "function toggleAllPanels" in html
    assert "function toggleRecommendationDetail" in html
    assert "slice(0, 3)" in html
    assert 'aria-controls="panel-body-sectors"' in html
    assert "const DEFAULT_PANEL_STATE = { sectors:false, news:false, control:false, asset:false, trades:false, tracking:false, weekly:false, history:false }" in html
    assert ".primary-grid, .secondary-grid { grid-template-columns: 1fr; }" in html
    assert "sessionStorage.setItem(PANEL_STATE_STORAGE, JSON.stringify(panelState))" in html
    assert "apiKey" not in html.split("function persistPanelState", 1)[1].split(
        "function togglePanel", 1
    )[0]
