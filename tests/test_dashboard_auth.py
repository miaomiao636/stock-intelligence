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
    assert 'id="a-initial"' not in html
    assert "getElementById('a-initial')" not in html
    assert "可用现金（可修改）" in html
    assert "d.effective_principal ?? d.adjusted_principal ?? d.initial_cash" in html
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


def test_dashboard_uses_sidebar_views_without_storing_credentials():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert 'class="app-shell"' in html
    assert 'class="sidebar"' in html
    assert 'aria-label="功能导航"' in html
    assert html.count('class="sidebar-nav-item') == 11
    assert html.count('<section class="dashboard-view"') == 11
    assert 'dashboardActiveViewV1' in html
    assert "function navigateDashboard" in html
    assert "function initializeDashboardNavigation" in html
    assert "function toggleRecommendationDetail" in html
    assert "slice(0, 3)" in html
    assert 'data-view-target="recommendations"' in html
    assert 'data-view-target="control"' in html
    assert '.app-shell.sidebar-open .sidebar' in html
    assert "sessionStorage.setItem(DASHBOARD_VIEW_STORAGE, viewId)" in html
    assert "apiKey" not in html.split("function navigateDashboard", 1)[1].split(
        "function initializeDashboardNavigation", 1
    )[0]


def test_dashboard_only_reports_server_confirmed_cash_and_strategy_saves():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "服务器已确认：可用现金" in html
    assert "服务器已确认：最高股价" in html
    assert "服务器未返回已保存的资金金额" in html
    assert "服务器未确认价格上限" in html
    assert "_savePriceTimer" not in html
    assert "sessionStorage.setItem('maxStockPrice'" not in html


def test_dashboard_distinguishes_unentered_recommendations_from_positions():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "执行覆盖" in html
    assert "未触发" in html
    assert "证据不足" in html
    assert "const hasReturn = Number.isFinite(Number(t.actual_return_pct))" in html
    assert "const entered = ['filled','theoretical_trigger'].includes(t.execution_status)" in html
    assert "const closed = hitCount + failCount" in html
