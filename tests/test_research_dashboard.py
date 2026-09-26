"""Contracts for the read-only research workspace and safe evidence rendering."""

from html.parser import HTMLParser
from pathlib import Path


DASHBOARD = Path(__file__).parents[1] / "dashboard"


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_research_pages_have_matching_navigation_and_accessible_headings():
    html = (DASHBOARD / "index.html").read_text()
    elements = Elements(html).elements
    for view in ("research", "judgments", "experiments"):
        assert any(a.get("data-view-target") == view for _, a in elements)
        panels = [a for tag, a in elements if tag == "section" and a.get("data-view-panel") == view]
        assert len(panels) == 1
        assert "hidden" in panels[0]
        assert any(a.get("id") == panels[0]["aria-labelledby"] for _, a in elements)
    assert 'href="/static/research.css"' in html
    assert 'src="/static/research.js"' in html
    assert "dashboard:viewchange" in html


def test_research_form_defaults_to_facts_and_labels_read_only_boundaries():
    html = (DASHBOARD / "index.html").read_text()
    elements = Elements(html).elements
    assert any(a.get("id") == "research-question" and a.get("maxlength") == "1200" for _, a in elements)
    assert any(a.get("value") == "facts" and "checked" in a for _, a in elements)
    assert "非实时报价" in html
    assert "不下单" in html
    assert "不调用大模型" in html
    assert "专家按需" in html


def test_research_external_content_is_text_only_and_keys_are_not_persisted():
    js = (DASHBOARD / "research.js").read_text()
    assert ".textContent" in js
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "requestOperationKey(" in js
    assert "apiFetch(" in js
    assert "['https:', 'http:'].includes" in js
    assert "noopener noreferrer" in js


def test_chat_checks_secure_transport_before_asking_for_key_and_sets_request_id():
    js = (DASHBOARD / "research.js").read_text()
    handler = js.split("async function submitQuestion", 1)[1].split("function initialize", 1)[0]
    assert handler.index("window.isSecureContext !== true") < handler.index("requestOperationKey(")
    assert "HTTPS" in handler
    assert "crypto.randomUUID()" in handler
    assert "request_id:" in handler
    assert "mode:" in handler
    assert "stock_code" in handler


def test_research_loading_errors_empty_data_and_as_of_are_explicit():
    js = (DASHBOARD / "research.js").read_text()
    for endpoint in ("/api/research/overview", "/api/research/judgments", "/api/research/lessons", "/api/research/experiments", "/api/research/chat"):
        assert endpoint in js
    for label in ("加载中", "暂无", "加载失败", "资料时间较早", "时间未提供", "history_incomplete", "known_at"):
        assert label in js
    assert "AbortController" in js
    assert "aria-busy" in js


def test_missing_execution_coverage_is_not_presented_as_zero():
    html = (DASHBOARD / "index.html").read_text()
    assert "function formatCoverage" in html
    assert "待核验" in html
    assert "summary.execution_coverage_pct||0" not in html
    assert "week.execution_coverage_pct||0" not in html
    assert "formatCoverage(summary.execution_coverage_pct)" in html
    assert "formatCoverage(week.execution_coverage_pct)" in html


def test_recommendation_counts_explain_report_rows_versus_unique_stocks():
    html = (DASHBOARD / "index.html").read_text()
    assert "报告条目" in html
    assert "去重股票" in html
    assert "uniqueStockCount" in html
    assert "new Set(allStocks" in html


def test_research_explains_unexecuted_entry_plans_without_inventing_opportunities():
    js = (DASHBOARD / "research.js").read_text()
    assert "entry_plans" in js
    assert "by_status" in js
    assert "reason_counts" in js
    assert "未归档" in js


def test_judgment_review_keeps_prediction_execution_and_profit_separate():
    js = (DASHBOARD / "research.js").read_text()
    for field in ("prediction_status", "execution_status", "net_pnl_status", "net_pnl_after_costs"):
        assert field in js
    assert "预测方向" in js
    assert "实际执行" in js
    assert "账户损益" in js


def test_overview_query_time_is_not_presented_as_evidence_freshness():
    js = (DASHBOARD / "research.js").read_text()
    assert "timestamp(data.data_as_of)" in js
    assert "快照查询时间" in js


def test_malformed_list_response_is_not_silently_treated_as_empty_records():
    js = (DASHBOARD / "research.js").read_text()
    assert "Array.isArray(data.items)" in js
    assert "资料结构不完整" in js
    assert js.count("itemsFrom(data)") >= 3


def test_judgment_evidence_keeps_supporting_and_counter_stances_visible():
    js = (DASHBOARD / "research.js").read_text()
    assert "item.stance" in js
    assert "支持证据" in js
    assert "反面证据" in js


def test_research_guidance_is_collapsed_and_facts_are_named_honestly():
    html = (DASHBOARD / "index.html").read_text()
    elements = Elements(html).elements
    guide = [a for tag, a in elements if tag == "details" and "research-guide" in a.get("class", "").split()]
    assert len(guide) == 1
    assert "open" not in guide[0]
    assert "固定摘要" in html
    assert "不按问题智能检索" in html


def test_research_replay_detail_precedes_list_for_mobile_access():
    html = (DASHBOARD / "index.html").read_text()
    assert html.index('id="research-judgment-detail"') < html.index('id="research-judgments"')
