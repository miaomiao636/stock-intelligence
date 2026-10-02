/* Research views only read evidence and request bounded research; never place orders. */
(() => {
    'use strict';
    const loaded = new Set();
    const sequences = {judgments: 0, detail: 0, experiments: 0, research: 0, diagnostics: 0};
    const byId = id => document.getElementById(id);
    const list = value => Array.isArray(value) ? value : [];
    function itemsFrom(data) {
        if (!Array.isArray(data.items)) throw new Error('资料结构不完整，无法确认是否有记录。');
        return data.items;
    }
    const text = value => value === null || value === undefined ? '' :
        (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value));
    const element = (tag, className = '', value = '') => {
        const el = document.createElement(tag);
        if (className) el.className = className;
        el.textContent = text(value);
        return el;
    };
    const labels = {pending:'待验证', proposed:'待验证', candidate:'待验证', active:'跟踪中',
        open:'跟踪中', draft:'草稿', completed:'已完成', reviewed:'已复核', rejected:'未通过',
        validated:'已验证', archived:'已归档', blocked:'受阻', history_incomplete:'历史证据不足',
        insufficient_data:'数据不足', short:'短期', medium:'中期', long:'长期',
        facts:'事实查询', experts:'专家研究', bull:'支持视角', bear:'反对视角', risk:'风险审查',
        waiting_trigger:'等待触发', waiting_price:'等待有效价格', waiting_quote:'等待有效行情', waiting_session:'等待交易时段',
        filled:'已模拟成交', expired:'已过期', watching:'观察中', pending_trigger:'等待触发', ready:'条件就绪',
        correct:'方向正确', incorrect:'方向错误', inconclusive:'无法判断', not_triggered:'未触发',
        closed:'已平仓', theoretical_trigger:'理论触发（非账户成交）', unknown:'未知',
        unavailable:'无可核验损益', realized:'已实现', unrealized:'未实现', retired:'已停用', recorded:'已归档',
        explicit_forecast_missing:'未明确记录预测方向或期限', forecast_input_snapshot_missing:'缺少模型输入行情快照',
        reference_quote_missing:'缺少可核验的预测基准价', reference_quote_from_future:'基准行情晚于判断时点',
        reference_price_basis_unknown:'基准价格口径不明', not_recorded_on_forecast_day:'并非判断当日留档',
        reference_evidence_mismatch:'基准价与引用证据不一致',
        not_recorded_at_forecast_time:'未在判断时点及时留档', reference_quote_stale:'判断基准行情已过期',
        same_stock_day_horizon_repeat:'同股票同日同期限重复', target_close_unavailable:'缺少到期收盘行情',
        target_date_mismatch:'行情不是指定到期日', invalid_close:'收盘价无效', observation_time_invalid:'到期行情时间无效',
        observation_source_missing:'到期行情来源缺失', price_adjustment_unverified:'尚未核验除权除息',
        invalid_return:'价格变化无法有效计算',
        corporate_action_requires_adjusted_reference:'期间发生除权除息，暂不按原始价评分'};
    const label = value => labels[value] || text(value) || '未标注';
    function state(target, message, error = false) {
        target.replaceChildren(element('p', `research-state${error ? ' is-error' : ''}`, message));
    }
    function empty(target, title, description) {
        const block = element('div', 'research-empty');
        block.append(element('h3', '', title), element('p', '', description));
        target.replaceChildren(block);
    }
    function busy(target, value) { target.setAttribute('aria-busy', String(value)); }
    function fold(title, className = 'research-fold') {
        const details = element('details', className);
        details.append(element('summary', '', title));
        return details;
    }
    // Page only the returned snapshot; never imply that 30/100 loaded rows are all history.
    function paged(target, items, size, renderItem, scope) {
        let page = 0;
        const pages = Math.max(1, Math.ceil(items.length / size));
        const rows = element('div', 'research-paged-items');
        const nav = element('nav', 'research-pagination');
        nav.setAttribute('aria-label', '记录分页');
        const previous = element('button', 'btn btn-sm', '上一页');
        const next = element('button', 'btn btn-sm', '下一页');
        const count = element('span');
        count.setAttribute('role', 'status');
        for (const button of [previous, next]) {
            button.type = 'button';
            button.setAttribute('aria-label', button.textContent);
        }
        nav.append(previous, count, next);
        target.replaceChildren(element('p', 'research-list-scope', scope), rows, nav);
        function render() {
            rows.replaceChildren(...items.slice(page * size, (page + 1) * size).map(renderItem));
            rows.scrollTop = 0;
            count.textContent = `第 ${page + 1} / ${pages} 页 · ${items.length} 条`;
            previous.disabled = page === 0;
            next.disabled = page === pages - 1;
        }
        previous.addEventListener('click', () => { if (page > 0) { page--; render(); } });
        next.addEventListener('click', () => { if (page + 1 < pages) { page++; render(); } });
        render();
    }
    function timestamp(value) {
        const raw = text(value);
        // Backend timestamps without an offset are China-market local times, not browser local times.
        const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
        const withZone = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(normalized)
            ? normalized + '+08:00' : normalized;
        const parsed = Date.parse(withZone);
        const stale = Number.isFinite(parsed) && Date.now() - parsed > 48 * 60 * 60 * 1000;
        const display = Number.isFinite(parsed) ? new Intl.DateTimeFormat('zh-CN', {
            timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'
        }).format(new Date(parsed)) + ' 北京时间' : raw || '时间未提供';
        const node = element('span', `research-time${stale ? ' is-stale' : ''}`,
            `资料截止：${display}${stale ? ' · 资料时间较早' : ''}`);
        node.title = raw;
        return node;
    }
    function limitations(parent, values) {
        const notes = list(values).filter(Boolean);
        if (!notes.length) return;
        const ul = element('ul', 'research-limitations');
        notes.forEach(note => ul.append(element('li', '', note)));
        parent.append(ul);
    }
    function sources(parent, values) {
        const items = list(values);
        if (!items.length) {
            parent.append(element('p', 'research-muted', '未提供可核验来源；不能仅凭回答作出交易判断。'));
            return;
        }
        const ul = element('ul', 'research-sources');
        items.forEach(source => {
            const item = typeof source === 'object' && source !== null ? source : {label: source};
            const li = element('li');
            const stance = {supporting:'支持证据', counter:'反面证据'}[item.stance];
            const title = `${stance ? stance + ' · ' : ''}${text(item.label || item.title || item.summary || item.content || item.source || '来源未命名')}`;
            let url;
            try { url = new URL(item.url); } catch (_) { url = null; }
            if (url && ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) {
                const a = element('a', '', title);
                a.href = url.href;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                li.append(a);
            } else li.append(element('span', '', title));
            if (item.known_at || item.as_of) li.append(element('span', '', `（已知时间：${text(item.known_at || item.as_of)}）`));
            ul.append(li);
        });
        parent.append(ul);
    }
    async function request(url, options = {}, apiKey = '', timeout = 15000) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), timeout);
        try {
            const response = await apiFetch(url, {...options, signal: controller.signal}, apiKey);
            if (!response.ok) {
                const messages = {401:'操作密钥未通过验证。', 403:'当前请求无权执行。',
                    404:'研究接口尚未部署，请确认服务器版本。', 409:'相同研究请求正在处理或请求状态冲突，请稍后查询。',
                    422:'输入未通过检查，请核对问题和股票代码。', 429:'研究额度或请求频率已达上限，请稍后再试。',
                    503:'研究服务暂不可用，或操作密钥尚未配置。'};
                throw new Error(messages[response.status] || `服务响应异常（HTTP ${response.status}）。`);
            }
            const data = await response.json();
            if (!data || typeof data !== 'object' || data.error) throw new Error('未取得完整的研究数据。');
            return data;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error('请求超时，未获得结果。请先核对网络和服务状态，避免连续重复提交。');
            throw error;
        } finally { window.clearTimeout(timer); }
    }
    const failure = (target, error) => state(target, `加载失败：${error.message || '请检查连接后重试。'}`, true);
    const diagnosticTargets = () => [byId('overview-diagnostics'), byId('research-diagnostics')].filter(Boolean);
    const countText = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? String(value) : '--';
    function diagnosticCell(name, value, note, warning = false) {
        const cell = element('div', 'research-diagnostic-cell');
        cell.append(element('h3', '', name), element('strong', warning ? 'is-warning' : '', value), element('p', '', note));
        return cell;
    }
    function scorecardPercent(value, evaluable) {
        return Number.isFinite(evaluable) && evaluable > 0 && typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
            ? `${value.toFixed(1)}%` : '--';
    }
    function predictionSummary(scorecard) {
        const summary = scorecard?.summary;
        if (!summary) return diagnosticCell('预测评估', '--', '暂无可核验的预测成绩；不能用报告数量代替准确率。');
        return diagnosticCell('方向正确率', scorecardPercent(summary.accuracy_pct, summary.evaluable),
            `可评估 ${countText(summary.evaluable)} · 待到期/待复核 ${countText(summary.pending)} · 未纳入 ${countText(summary.excluded)}`);
    }
    function predictionDetails(scorecard) {
        const details = fold('预测评估口径与各观察窗口', 'research-diagnostic-details research-scorecard-details');
        const body = element('div', 'research-diagnostic-detail-body');
        if (!scorecard?.summary) {
            body.append(element('p', 'research-muted', '尚无预测评估档案：需要保留当时判断、观察窗口及可核验的后续行情；不以账户收益或报告数量代替。'));
            details.append(body);
            return details;
        }
        const summary = scorecard.summary;
        body.append(element('p', 'research-muted', `评估截至 ${text(scorecard.as_of) || '未提供'} · 协议 ${text(scorecard.protocol_version) || '未提供'}`));
        body.append(element('p', 'research-muted', '方向统计不是收益率，也不是独立样本外验证；重叠观察窗口不能当成独立样本。按同股票、日期和窗口去重后评估。'));
        const counts = element('dl', 'research-metrics');
        [['已记录',summary.records_total],['可评估',summary.evaluable],['方向正确',summary.correct],['方向错误',summary.incorrect],
            ['待到期/待复核',summary.pending],['到期但结果不可核验',summary.unavailable],['未纳入评估',summary.excluded],['重复记录',summary.duplicate]]
            .forEach(([name, value]) => counts.append(element('dt', '', name),element('dd', '', countText(value))));
        counts.append(element('dt', '', '可评估覆盖率'), element('dd', '', scorecardPercent(summary.coverage_pct, summary.eligible)));
        body.append(counts, element('p', 'research-muted', `同批始终预测上涨的正确率：${scorecardPercent(summary.always_up_accuracy_pct, summary.evaluable)}。不高于此基线，不能证明选股有效。`));
        const rows = list(scorecard.by_horizon);
        if (rows.length) {
            const wrap = element('div', 'research-scorecard-table-wrap');
            const table = element('table', 'research-scorecard-table');
            const head = element('thead'), tr = element('tr');
            ['观察窗口', '可评估', '方向正确率', '始终预测上涨'].forEach(name => {const th = element('th', '', name); th.scope = 'col'; tr.append(th);});
            head.append(tr);
            table.append(element('caption', '', '各窗口分别统计；-- 表示没有可核验结果'), head);
            const tbody = element('tbody');
            rows.forEach(row => {
                const stats = row.summary || {}, line = element('tr');
                const name = Number.isInteger(row.horizon_sessions) && row.horizon_sessions > 0 ? `${row.horizon_sessions} 个交易日` : '窗口未明确';
                const th = element('th', '', name); th.scope = 'row';
                line.append(th, element('td', '', countText(stats.evaluable)), element('td', '', scorecardPercent(stats.accuracy_pct, stats.evaluable)),
                    element('td', '', scorecardPercent(stats.always_up_accuracy_pct, stats.evaluable)));
                tbody.append(line);
            });
            table.append(tbody); wrap.append(table); body.append(wrap);
        }
        const reasons = Object.entries(scorecard.exclusion_reasons || {});
        if (reasons.length) {
            const ul = element('ul', 'research-sources');
            reasons.forEach(([reason, count]) => ul.append(element('li', '', `${label(reason)} · ${countText(count)} 条`)));
            body.append(element('h3', 'research-subheading', '未纳入的原因'), ul);
        }
        limitations(body, scorecard.limitations);
        details.append(body);
        return details;
    }
    function renderDiagnostics(target, data) {
        const heading = element('div', 'research-section-heading');
        const refresh = element('button', 'btn btn-sm', '刷新');
        refresh.type = 'button';
        refresh.setAttribute('aria-label', '刷新运行与评估');
        refresh.addEventListener('click', loadDiagnostics);
        heading.append(element('h2', '', '运行与评估'), refresh);
        const grid = element('div', 'research-diagnostic-grid');
        const tracking = data.tracking || {}, freshness = tracking.freshness || {};
        const stale = freshness.status === 'stale';
        const samplesIncomplete = tracking.tracking_quality?.status === 'incomplete';
        const incomplete = tracking.data_quality?.status === 'incomplete' || samplesIncomplete;
        const trackingLabel = tracking.available === false ? '暂无跟踪记录' : stale ? '跟踪已过期' : samplesIncomplete ? '跟踪样本不完整' : incomplete ? '历史不完整' : freshness.status === 'fresh' ? '跟踪已更新' : freshness.status === 'recent_unverified' ? '近期记录 · 待核验' : '更新时间待核验';
        let trackingNote = tracking.tracking_date ? `记录日期 ${text(tracking.tracking_date)}` : '没有可核验的跟踪日期';
        if (stale && countText(freshness.age_calendar_days) !== '--') trackingNote += ` · 距今 ${freshness.age_calendar_days} 个自然日`;
        if (stale || incomplete) trackingNote += '；暂不据此评价当前胜率。';
        grid.append(diagnosticCell('跟踪质量', trackingLabel, trackingNote, stale || incomplete || tracking.available === false));
        const execution = data.execution || {}, summary = execution.summary || {};
        const hasPlans = typeof summary.total === 'number' && summary.total > 0 && Boolean(execution.trade_date);
        const historical = hasPlans && execution.trade_date !== execution.requested_date;
        const statuses = Object.entries(summary.by_status || {}).map(([key, value]) => `${label(key)} ${countText(value)}`).join(' · ');
        grid.append(diagnosticCell('计划与执行', hasPlans ? `${execution.trade_date} · ${countText(summary.total)} 条` : '暂无计划记录',
            hasPlans ? `${historical ? '最近有记录的日期，非今日；' : '指定日期的归档记录；'}${statuses || '状态待核验'}` : '未归档不等于没有机会或执行失败。'));
        grid.append(predictionSummary(data.scorecard));
        const detail = fold('查看未成交原因与数据说明', 'research-diagnostic-details');
        const body = element('div', 'research-diagnostic-detail-body');
        body.append(element('h3', 'research-subheading', '为什么没有成交？'));
        if (hasPlans) {
            body.append(element('p', 'research-muted', `计划日期 ${text(execution.trade_date)}；查询日期 ${text(execution.requested_date) || '未提供'}。只解释该批归档计划，不代表当前账户收益。`));
            const reasons = Object.entries(summary.reason_counts || {});
            if (reasons.length) {
                const ul = element('ul', 'research-sources');
                reasons.forEach(([reason, count]) => ul.append(element('li', '', `${reason} · ${countText(count)} 条`)));
                body.append(ul);
            } else body.append(element('p', 'research-muted', '没有已归档原因，不能自动归因于市场、风控或系统故障。'));
        } else body.append(element('p', 'research-muted', '暂无计划记录。未归档不等于零失败，也不能据此认定没有触价或没有成交。'));
        if (execution.note) body.append(element('p', 'research-muted', execution.note));
        if (samplesIncomplete) {
            const quality = tracking.tracking_quality;
            body.append(element('p', 'research-muted', `跟踪样本：行情错误 ${countText(quality.data_error_samples)} · 历史缺口 ${countText(quality.history_incomplete_samples)} · 路径不明 ${countText(quality.path_ambiguous_samples)}。文件可读取不等于样本可评估。`));
        }
        body.append(element('p', 'research-muted', '跟踪是否及时、计划是否成交与预测是否正确是三个不同的问题；这里不会补造历史交易或收益。'));
        detail.append(body);
        target.replaceChildren(heading, grid, element('p', 'research-diagnostic-boundary', '预测成绩不等于账户收益；没有有效样本时显示 --，不显示 0% 或假定正确。'), detail, predictionDetails(data.scorecard),
            element('p', 'research-diagnostic-asof', `本次查询：${text(data.as_of) || '未提供'}（不是行情更新时间）`));
    }
    async function loadDiagnostics() {
        const seq = ++sequences.diagnostics;
        const targets = diagnosticTargets();
        targets.forEach(target => {busy(target, true); state(target, '加载中：核对跟踪与执行记录…');});
        try {
            const data = await request('/api/research/diagnostics');
            if (seq !== sequences.diagnostics) return;
            if (!data.tracking || !data.execution || !Number.isInteger(data.execution.summary?.total) || data.execution.summary.total < 0) {
                throw new Error('运行诊断结构不完整，不能判定当前状态。');
            }
            targets.forEach(target => renderDiagnostics(target, data));
        } catch (error) {
            if (seq !== sequences.diagnostics) return;
            targets.forEach(target => {
                failure(target, error);
                const retry = element('button', 'btn btn-sm', '重试');
                retry.type = 'button';
                retry.addEventListener('click', loadDiagnostics);
                target.append(retry);
            });
        } finally {if (seq === sequences.diagnostics) targets.forEach(target => busy(target, false));}
    }
    async function loadOverview() {
        const target = byId('research-overview');
        const seq = ++sequences.research;
        busy(target, true);
        state(target, '加载中：核对研究资料…');
        try {
            const data = await request('/api/research/overview');
            if (seq !== sequences.research) return;
            target.replaceChildren();
            const grid = element('div', 'research-stat-grid');
            [['判断记录',data.judgments_count], ['研究经验',data.lessons_count], ['验证实验',data.experiments_count]].forEach(([name, value]) => {
                const card = element('div', 'research-stat');
                const count = typeof value === 'number' && Number.isFinite(value) ? String(value) : '待核验';
                card.append(element('span', '', name), element('strong', '', count));
                grid.append(card);
            });
            target.append(grid, timestamp(data.data_as_of));
            const context = fold('数据说明、入场计划与限制');
            context.append(element('span', 'research-time', `快照查询时间：${text(data.as_of) || '未提供'}（不代表行情或证据已更新）`));
            const plans = data.entry_plans;
            const planPanel = element('details', 'research-plan-summary');
            planPanel.append(element('summary', '', '入场计划 · 为什么还未成交？'));
            if (!plans || !Number.isFinite(plans.total) || plans.total === 0) {
                planPanel.append(element('p', 'research-muted', '当前日期没有已归档的入场计划：未归档不等于没有机会，也不能据此认定未触发或未成交。'));
            } else {
                planPanel.append(element('p', 'research-muted', `计划日期：${text(plans.trade_date) || '未提供'} · 已归档 ${plans.total} 条计划；状态来自服务端记录。`));
                const dl = element('dl', 'research-metrics');
                Object.entries(plans.by_status || {}).forEach(([key, count]) => dl.append(element('dt', '', label(key)), element('dd', '', count)));
                planPanel.append(dl, element('h3', 'research-subheading', '服务端记录的原因'));
                const reasons = Object.entries(plans.reason_counts || {});
                if (!reasons.length) planPanel.append(element('p', 'research-muted', '暂无已归档原因，不能自动归因于策略或市场。'));
                else {
                    const ul = element('ul', 'research-sources');
                    reasons.forEach(([reason, count]) => ul.append(element('li', '', `${reason} · ${count} 条`)));
                    planPanel.append(ul);
                }
            }
            context.append(planPanel);
            if (data.available === false) target.append(element('p', 'research-state', '暂无可用研究档案，当前不能据此判断策略是否有效。'));
            limitations(context, data.limitations);
            target.append(context);
        } catch (error) { if (seq === sequences.research) failure(target, error); }
        finally { if (seq === sequences.research) busy(target, false); }
    }
    async function loadJudgments() {
        const target = byId('research-judgments');
        const detail = byId('research-judgment-detail');
        const seq = ++sequences.judgments;
        ++sequences.detail;
        busy(detail, false);
        empty(detail, '选择一条判断', '查看原始观点、证据和后续复核。没有复核记录时，不自动判定对错。');
        busy(target, true);
        state(target, '加载中：查询判断记录…');
        try {
            const code = byId('judgment-code').value.trim();
            const data = await request(`/api/research/judgments?limit=30${code ? '&code=' + encodeURIComponent(code) : ''}`);
            if (seq !== sequences.judgments) return;
            const items = itemsFrom(data);
            if (!items.length) { empty(target, '暂无判断记录', '对应股票尚未留下可回放的研究判断；不会用今天的信息补写历史。'); return; }
            let selectedId = null;
            paged(target, items, 6, item => {
                const button = element('button', 'research-judgment-button');
                button.type = 'button';
                button.setAttribute('aria-pressed', String(item.id === selectedId));
                const heading = element('div', 'research-summary-line');
                heading.append(element('strong', '', `${text(item.stock_name) || '未命名股票'} ${text(item.stock_code)}`),
                    element('span', `research-badge${item.history_incomplete ? ' is-warning' : ''}`, label(item.history_incomplete ? 'history_incomplete' : item.status)));
                button.append(heading, timestamp(item.as_of), element('p', 'research-excerpt', text(item.thesis).slice(0, 140) || '未记录判断摘要'));
                button.addEventListener('click', () => {
                    selectedId = item.id;
                    target.querySelectorAll('.research-judgment-button').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
                    loadJudgmentDetail(item.id, true);
                });
                return button;
            }, `本次加载最近 ${items.length} 条判断（最多 30 条，非全部历史）· 点击摘要看全文`);
        } catch (error) { if (seq === sequences.judgments) failure(target, error); }
        finally { if (seq === sequences.judgments) busy(target, false); }
    }
    async function loadJudgmentDetail(id, reveal = false) {
        const target = byId('research-judgment-detail');
        const seq = ++sequences.detail;
        busy(target, true);
        state(target, '加载中：读取原始判断及复核…');
        try {
            const data = await request(`/api/research/judgments/${encodeURIComponent(text(id))}`);
            if (seq !== sequences.detail) return;
            const item = data.judgment;
            if (!item || typeof item !== 'object') throw new Error('未找到该判断的原始记录。');
            target.replaceChildren(element('div', 'research-eyebrow', '原始判断'),
                element('h2', '', `${text(item.stock_name) || '未命名股票'} ${text(item.stock_code)}`),
                element('span', 'research-badge', `${label(item.status)} · ${label(item.horizon)}`), timestamp(item.as_of));
            if (item.history_incomplete) limitations(target, ['历史证据不完整：不能认定这些资料在原判断时间均已可获得。']);
            target.append(element('p', 'research-answer-text', item.thesis || '未记录判断正文'));
            const evidence = fold(`依据与来源 · ${list(item.evidence).length} 条`);
            sources(evidence, item.evidence);
            target.append(evidence);
            const reviews = list(data.reviews);
            const reviewPanel = fold(`后续复核 · ${reviews.length} 条（不回写原判断）`);
            if (!reviews.length) reviewPanel.append(element('p', 'research-muted', '暂无复核记录，尚不能评判判断是否正确。'));
            reviews.forEach(review => {
                const record = element('article', 'research-record');
                record.append(element('span', 'research-badge', label(review.status || review.verdict)),
                    timestamp(review.known_at || review.reviewed_at || review.as_of),
                    element('p', '', review.summary || review.content || review.outcome || '未提供复核说明'));
                const dimensions = element('dl', 'research-metrics');
                [['预测方向', review.prediction_status], ['实际执行', review.execution_status],
                    ['账户损益', review.net_pnl_status]].forEach(([name, value]) => {
                    dimensions.append(element('dt', '', name), element('dd', '', label(value)));
                });
                if (typeof review.net_pnl_after_costs === 'number' && Number.isFinite(review.net_pnl_after_costs)) {
                    dimensions.append(element('dt', '', '已记录扣费后损益'), element('dd', '', `¥${review.net_pnl_after_costs.toFixed(2)}`));
                }
                record.append(dimensions);
                limitations(record, review.limitations);
                if (list(review.evidence).length) sources(record, review.evidence);
                reviewPanel.append(record);
            });
            target.append(reviewPanel);
        } catch (error) { if (seq === sequences.detail) failure(target, error); }
        finally {
            if (seq === sequences.detail) {
                busy(target, false);
                target.scrollTop = 0;
                if (reveal && window.matchMedia('(max-width: 768px)').matches) {
                    target.scrollIntoView({block:'start', behavior:'instant'});
                    target.focus({preventScroll:true});
                }
            }
        }
    }
    async function loadExperiments() {
        const seq = ++sequences.experiments;
        const panels = [['research-lessons','/api/research/lessons','lesson'], ['research-experiments','/api/research/experiments','experiment']];
        await Promise.all(panels.map(async ([id, url, kind]) => {
            const target = byId(id);
            busy(target, true);
            state(target, '加载中：读取研究记录…');
            try {
                const data = await request(url);
                if (seq !== sequences.experiments) return;
                const items = itemsFrom(data);
                if (!items.length) {
                    empty(target, kind === 'lesson' ? '暂无研究经验' : '暂无验证实验',
                        kind === 'lesson' ? '先积累可追溯的判断与复核，再形成可检验的经验。' : '未登记实验不等于策略无效，也不代表已经通过验证。');
                    return;
                }
                if (kind === 'lesson') {
                    const groups = new Map();
                    items.forEach(item => {
                        const key = JSON.stringify([item.title || '未命名记录', item.status || '']);
                        if (!groups.has(key)) groups.set(key, []);
                        groups.get(key).push(item);
                    });
                    target.replaceChildren(element('p', 'research-list-scope', `当前加载 ${items.length} 条经验，按主题与状态归为 ${groups.size} 组（最多加载最近 100 条）。展开看原始记录，不合并或删除数据。`));
                    groups.forEach(records => {
                        const first = records[0];
                        const group = fold(first.title || '未命名记录', 'research-lesson-group');
                        group.firstChild.append(element('span', 'research-badge', label(first.status)), element('span', 'research-group-count', `${records.length} 条`));
                        const body = element('div');
                        paged(body, records, 5, item => {
                            const record = element('article', 'research-record');
                            record.append(timestamp(item.known_at), element('p', '', item.summary || '未提供经验说明'));
                            limitations(record, item.limitations);
                            return record;
                        }, '原始记录 · 每页 5 条');
                        group.append(body);
                        target.append(group);
                    });
                } else {
                    paged(target, items, 5, item => {
                        const record = fold(item.title || item.name || '未命名记录', 'research-experiment');
                        record.firstChild.append(element('span', 'research-badge', label(item.status)));
                        if (item.as_of || item.created_at) record.append(timestamp(item.as_of || item.created_at));
                        const metrics = item.metrics && typeof item.metrics === 'object' ? Object.entries(item.metrics) : [];
                        if (!metrics.length) record.append(element('p', 'research-muted', '暂无已验证指标；不推算收益或成功率。'));
                        else {
                            const dl = element('dl', 'research-metrics');
                            metrics.forEach(([key, value]) => dl.append(element('dt', '', key), element('dd', '', value === null ? '未提供' : value)));
                            record.append(dl);
                        }
                        limitations(record, item.limitations);
                        return record;
                    }, `本次加载 ${items.length} 条实验（最多最近 100 条）· 展开查看指标`);
                }
            } catch (error) { if (seq === sequences.experiments) failure(target, error); }
            finally { if (seq === sequences.experiments) busy(target, false); }
        }));
    }
    function renderAnswer(data, question) {
        const target = byId('research-answer');
        const card = element('article', 'card research-answer-card');
        card.append(element('div', 'research-section-heading'));
        card.firstChild.append(element('h2', '', '研究回答'), element('span', 'research-badge', label(data.mode)));
        card.append(timestamp(data.as_of), element('p', 'research-question-echo', `问题：${question}`),
            element('div', 'research-answer-text', data.answer || '未获得可用回答，请核对研究资料。'));
        if (list(data.roles).length) {
            const roles = element('div', 'research-role-grid');
            list(data.roles).forEach(role => {
                const item = typeof role === 'object' && role !== null ? role : {role};
                const block = element('section', 'research-role');
                block.append(element('h3', '', label(item.name || item.role)), element('p', '',
                    item.analysis || item.summary || item.answer || item.content || item.conclusion || '未提供该视角的独立意见'));
                roles.append(block);
            });
            card.append(roles, element('p', 'research-muted', '视角分工不等于独立样本；多个模型或角色一致，也不能替代样本外验证。'));
        }
        card.append(element('h3', 'research-subheading', '可追溯来源'));
        sources(card, data.sources);
        limitations(card, data.limitations);
        if (data.usage && typeof data.usage === 'object') {
            const details = element('details');
            details.append(element('summary', 'research-muted', '本次研究用量（服务端记录）'), element('pre', 'research-answer-text', data.usage));
            card.append(details);
        }
        target.replaceChildren(card);
    }
    async function submitQuestion(event) {
        event.preventDefault();
        const status = byId('research-chat-status');
        const button = byId('research-submit');
        if (button.disabled) return;
        status.className = 'research-state';
        status.textContent = '';
        if (window.isSecureContext !== true) {
            status.className = 'research-state is-error';
            status.textContent = '当前页面不是安全连接。请先配置 HTTPS，再提交研究查询；为保护操作密钥，本页不会询问或发送密钥。公开研究资料仍可查看。';
            return;
        }
        const question = byId('research-question').value.trim();
        const code = byId('research-stock-code').value.trim();
        if (!question || question.length > 1200 || (code && !/^\d{6}$/.test(code))) {
            status.className = 'research-state is-error';
            status.textContent = '请输入 1–1200 字的问题；股票代码留空或填写 6 位数字。';
            return;
        }
        const mode = document.querySelector('input[name="research-mode"]:checked')?.value === 'experts' ? 'experts' : 'facts';
        // A new explicit submission gets a new ID; transport never automatically retries.
        const payload = {question, mode: mode, request_id: crypto.randomUUID()};
        if (code) payload.stock_code = code;
        let apiKey = '';
        try {
            apiKey = requestOperationKey(mode === 'experts' ? '按需专家研究（可能产生模型费用）' : '只读资料查询');
            button.disabled = true;
            busy(byId('research-form'), true);
            status.textContent = mode === 'experts' ? '研究中：在限定轮次内核对不同视角，请勿重复提交…' : '查询中：汇总已有事实，不调用大模型…';
            const data = await request('/api/research/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}, apiKey, 60000);
            renderAnswer(data, question);
            status.textContent = '已取得研究回答。请核对资料时间、来源与限制；未执行任何交易。';
        } catch (error) {
            status.className = 'research-state is-error';
            status.textContent = error.message || '查询失败，请稍后重试。';
        } finally {
            apiKey = '';
            button.disabled = false;
            busy(byId('research-form'), false);
        }
    }
    function initialize() {
        byId('research-form').addEventListener('submit', submitQuestion);
        byId('judgment-filter').addEventListener('submit', event => { event.preventDefault(); loadJudgments(); });
        byId('research-refresh').addEventListener('click', () => {loadOverview(); loadDiagnostics();});
        byId('experiments-refresh').addEventListener('click', loadExperiments);
        document.querySelectorAll('[data-research-question]').forEach(button => button.addEventListener('click', () => {
            byId('research-question').value = button.dataset.researchQuestion;
            byId('research-question').focus();
        }));
        document.querySelectorAll('input[name="research-mode"]').forEach(input => input.addEventListener('change', () => {
            byId('research-submit').textContent = input.value === 'experts' ? '开始专家研究' : '查询资料';
        }));
        if (window.isSecureContext !== true) byId('research-chat-status').textContent = '当前为 HTTP 连接：可以查看资料，提交查询前需配置 HTTPS。';
        loadDiagnostics();
    }
    document.addEventListener('dashboard:viewchange', event => {
        const view = event.detail?.viewId;
        if (loaded.has(view)) return;
        const loader = {research:loadOverview, judgments:loadJudgments, experiments:loadExperiments}[view];
        if (loader) { loaded.add(view); loader(); }
    });
    document.addEventListener('DOMContentLoaded', initialize);
})();
