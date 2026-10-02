/*
 * Isolated headless browser regression for the research workspace.
 * Usage: node tests/research_compact_ui.cjs
 * Optional: PLAYWRIGHT_MODULE=/absolute/path/to/playwright
 *           PLAYWRIGHT_EXECUTABLE_PATH=/absolute/path/to/chromium
 *           RESEARCH_UI_ARTIFACT_DIR=/temporary/screenshot/directory
 * Uses only dashboard source and synthetic API fixtures. Every browser request
 * is intercepted: no running backend, .env, data/, model or cloud is accessed.
 * No Node dependency or browser installation is added to this repository.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

function loadPlaywright() {
    const candidates = [process.env.PLAYWRIGHT_MODULE, 'playwright', path.join(os.homedir(),
        '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')].filter(Boolean);
    for (const candidate of candidates) {
        try { return require(candidate); } catch (error) {
            if (error.code !== 'MODULE_NOT_FOUND') throw error;
        }
    }
    throw new Error('Playwright unavailable: set PLAYWRIGHT_MODULE to an existing installation. This test does not install dependencies.');
}
const {chromium} = loadPlaywright();
const dashboard = path.resolve(__dirname, '../dashboard');
const pageSource = fs.readFileSync(path.join(dashboard, 'index.html'), 'utf8');
const scriptSource = fs.readFileSync(path.join(dashboard, 'research.js'), 'utf8');
const styleSource = fs.readFileSync(path.join(dashboard, 'research.css'), 'utf8');
const BASE = 'http://127.0.0.1:4173';
const oldTime = '2026-01-01T08:00:00+08:00';
const unsafe = '<img src=x onerror="window.__researchInjected=1">';
const judgments = Array.from({length: 30}, (_, i) => ({
    id: `judgment-${i + 1}`, stock_code: String(i + 1).padStart(6, '0'),
    stock_name: i === 0 ? unsafe : `测试股票${i + 1}`,
    status: 'history_incomplete', history_incomplete: true, horizon: 'short', as_of: oldTime,
    thesis: `判断正文 ${i + 1}。` + '这是一段需要以两行摘要展示但详情必须保留的完整判断。'.repeat(20),
}));
const lessonGroups = [
    ['历史判断缺少完整证据', 'pending', 30],
    ['研究假设尚无可追溯支持快照', 'pending', 28],
    ['历史判断缺少完整证据', 'validated', 20],
    [unsafe + '无空格'.repeat(50), 'pending', 5],
];
const lessons = lessonGroups.flatMap(([title, status, count], g) => Array.from({length: count}, (_, i) => ({
    id: `lesson-${g}-${i}`, title, status, known_at: oldTime,
    summary: `经验记录 ${g}-${i}：` + '完整原始说明不可丢失。'.repeat(24),
    limitations: ['仍然必须进行独立验证，不自动修改策略。'],
})));
const experiments = Array.from({length: 12}, (_, i) => ({
    id: `experiment-${i}`, name: `验证实验 ${i + 1}`, status: 'proposed', created_at: oldTime,
    metrics: {sample_size: i + 1, sample_note: '仅为合成测试数据'}, limitations: ['结果不是收益承诺'],
}));
let browser;
let checks = 0;
const failures = [];

async function fixturePage({width = 1440, scenario = 'normal'} = {}) {
    const context = await browser.newContext({viewport: {width, height: 1000}, reducedMotion: 'reduce'});
    const page = await context.newPage();
    page.setDefaultTimeout(3500);
    const unexpected = [];
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== BASE) {
            unexpected.push(request.url());
            return route.abort('blockedbyclient');
        }
        const respond = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (request.method() !== 'GET') {
            unexpected.push(request.method() + ' ' + url.pathname);
            return respond({error: 'All writes are forbidden in this UI test'}, 405);
        }
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: pageSource});
        if (url.pathname === '/static/research.js') return route.fulfill({contentType: 'application/javascript', body: scriptSource});
        if (url.pathname === '/static/research.css') return route.fulfill({contentType: 'text/css', body: styleSource});
        if (url.pathname === '/favicon.ico') return route.fulfill({status: 204});
        if (url.pathname.startsWith('/api/research/')) {
            if (scenario === 'error') return respond({error: 'Synthetic unavailable service'}, 503);
            if (url.pathname === '/api/research/diagnostics') return respond({
                as_of: oldTime, tracking: {available: false},
                execution: {requested_date: '2026-01-01', trade_date: null,
                    is_latest_available: false, summary: {total: 0, by_status: {}, reason_counts: {}}},
                scorecard: null,
            });
            if (url.pathname === '/api/research/overview') return respond({
                available: true, judgments_count: 659, lessons_count: 83, experiments_count: 12,
                as_of: oldTime, data_as_of: oldTime, entry_plans: {total: 0},
                limitations: ['只读研究，不是收益承诺。'],
            });
            if (url.pathname === '/api/research/judgments') {
                assert.equal(url.searchParams.get('limit'), '30', 'Backend window must remain explicitly bounded');
                return respond(scenario === 'malformed' ? {} : {items: scenario === 'empty' ? [] : judgments});
            }
            if (url.pathname.startsWith('/api/research/judgments/')) {
                const item = judgments.find(item => item.id === url.pathname.split('/').pop());
                return respond({judgment: {...item, evidence: [{label: unsafe, url: 'javascript:window.__researchInjected=1'}]}, reviews: []});
            }
            if (url.pathname === '/api/research/lessons') return respond(scenario === 'malformed' ? {} : {items: scenario === 'empty' ? [] : lessons});
            if (url.pathname === '/api/research/experiments') return respond(scenario === 'malformed' ? {} : {items: scenario === 'empty' ? [] : experiments});
        }
        // The unmodified dashboard also loads these unrelated read-only panels.
        const payloads = {
            '/api/health': {success: true, data: {status: 'ok'}},
            '/api/recommendations/all': [], '/api/recommendation': {stock_recommendations: []},
            '/api/live/quotes': {}, '/api/strategy/params': {}, '/api/positions': [],
            '/api/performance': {initial_cash: 20000, total_equity: 20000, available_cash: 20000},
            '/api/recommendations': [], '/api/analysis/asset': {daily_returns: []},
            '/api/paper/auto-trades': {trades: []}, '/api/tracking': {summary: {}, records: []},
            '/api/tracking/weekly': [], '/api/tracking/suggestions': {},
        };
        if (Object.hasOwn(payloads, url.pathname)) return respond(payloads[url.pathname]);
        unexpected.push(request.url());
        return respond({error: 'Unexpected endpoint in isolated fixture'}, 404);
    });
    await page.goto(BASE, {waitUntil: 'networkidle'});
    return {page, context, errors, unexpected};
}

async function openView(page, view) {
    const nav = page.locator(`.sidebar-nav-item[data-view-target="${view}"]`);
    const toggle = page.locator('.menu-toggle');
    if (await toggle.isVisible()) await toggle.click();
    await nav.click();
    const selector = {judgments: '#research-judgments', experiments: '#research-lessons', research: '#research-overview'}[view];
    await page.locator(`${selector}[aria-busy="false"]`).waitFor();
    if (view === 'experiments') await page.locator('#research-experiments[aria-busy="false"]').waitFor();
}
async function visibleCount(locator) {
    // Closed native <details> descendants can retain layout rectangles. The
    // browser visibility API checks the skipped/hidden details subtree too.
    return locator.evaluateAll(nodes => nodes.filter(node => node.checkVisibility()).length);
}
async function test(name, action, options) {
    const fixture = await fixturePage(options);
    try {
        await action(fixture.page);
        assert.deepEqual(fixture.errors, [], 'No uncaught JavaScript errors');
        assert.deepEqual(fixture.unexpected, [], 'No external requests, writes, or unregistered API calls');
        checks++;
        console.log(`PASS ${name}`);
    } catch (error) {
        failures.push(`${name}: ${error.message}`);
        console.error(`FAIL ${name}: ${error.message}`);
    } finally { await fixture.context.close(); }
}
const pagination = target => target.locator('.research-pagination');
const nextPage = target => pagination(target).getByRole('button', {name: '下一页', exact: true}).first();
const previousPage = target => pagination(target).getByRole('button', {name: '上一页', exact: true}).first();

async function main() {
    browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? {executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH} : {})});
    try {
        await test('judgments: six compact records per page, all 30 reachable, not all 659', async page => {
            await openView(page, 'research');
            assert.match(await page.locator('#research-overview').innerText(), /659/);
            await openView(page, 'judgments');
            const target = page.locator('#research-judgments');
            assert.equal(await target.locator('.research-judgment-button').count(), 6);
            assert.match(await page.locator('[data-view-panel="judgments"]').innerText(), /最近\s*30\s*条/);
            assert.doesNotMatch(await target.innerText(), /全部\s*659|共\s*659/);
            const seen = [];
            for (let p = 0; p < 5; p++) {
                const cards = target.locator('.research-judgment-button');
                assert.equal(await cards.count(), 6);
                assert.match(await pagination(target).innerText(), new RegExp(`第\\s*${p + 1}\\s*[/／]\\s*5\\s*页`));
                seen.push(...await cards.locator('strong').allTextContents());
                for (const card of await cards.all()) {
                    assert.equal(await card.getByText('历史证据不足', {exact: true}).count(), 1, 'Do not duplicate incomplete-history badges');
                    const excerpt = await card.locator('.research-excerpt').evaluate(el => ({height: el.getBoundingClientRect().height, lineHeight: parseFloat(getComputedStyle(el).lineHeight)}));
                    assert.ok(excerpt.height <= excerpt.lineHeight * 2 + 1, 'Excerpt must occupy no more than two lines');
                }
                if (p < 4) await nextPage(target).click();
            }
            assert.equal(new Set(seen).size, 30);
            assert.ok(await nextPage(target).isDisabled());
            await previousPage(target).click();
            assert.match(await pagination(target).innerText(), /第\s*4\s*[/／]\s*5\s*页/);
        });
        await test('lessons: 83 originals in four closed topic/status groups; full records and paging preserved', async page => {
            await openView(page, 'experiments');
            const target = page.locator('#research-lessons');
            const groups = target.locator('details.research-lesson-group');
            assert.equal(await groups.count(), 4);
            assert.equal(await target.locator('details[open]').count(), 0);
            assert.equal(await visibleCount(target.locator('.research-record')), 0);
            assert.match(await target.innerText(), /83/);
            assert.equal(await groups.locator('summary').filter({hasText: '历史判断缺少完整证据'}).count(), 2, 'Different validation statuses remain separate groups');
            const group = groups.filter({has: page.locator('summary', {hasText: '历史判断缺少完整证据'})}).filter({has: page.locator('summary', {hasText: '待验证'})});
            assert.equal(await group.count(), 1);
            const summary = group.locator('summary');
            await summary.focus();
            await page.keyboard.press('Enter');
            assert.equal(await group.getAttribute('open'), '');
            assert.equal(await visibleCount(group.locator('.research-record')), 5);
            const records = [];
            for (let p = 0; p < 6; p++) {
                const texts = await group.locator('.research-record').allTextContents();
                assert.equal(texts.length, 5);
                for (let i = 0; i < 5; i++) {
                    assert.ok(texts[i].includes(lessons[p * 5 + i].summary), 'Full original summary is retained');
                    assert.match(texts[i], /2026[-/]01[-/]01/, 'Readable source date remains available');
                    assert.equal(await group.locator('.research-record').nth(i).locator('.research-time').getAttribute('title'), oldTime,
                        'Original timezone-qualified source timestamp is retained');
                }
                records.push(...texts);
                if (p < 5) await nextPage(group).click();
            }
            assert.equal(new Set(records).size, 30);
            assert.ok(await nextPage(group).isDisabled());
            await summary.focus();
            await page.keyboard.press('Enter');
            assert.equal(await visibleCount(group.locator('.research-record')), 0);
            // The other 53 original records must remain reachable too; grouping
            // is presentation only, not deduplication or evidence deletion.
            for (const [index, [title, status, count]] of lessonGroups.entries()) {
                if (index === 0) continue;
                const statusLabel = status === 'validated' ? '已验证' : '待验证';
                const other = groups.filter({has: page.locator('summary').filter({hasText: title})})
                    .filter({has: page.locator('summary').filter({hasText: statusLabel})});
                assert.equal(await other.count(), 1);
                await other.locator('summary').click();
                for (let p = 0; p < Math.ceil(count / 5); p++) {
                    const texts = await other.locator('.research-record').allTextContents();
                    assert.equal(texts.length, Math.min(5, count - p * 5));
                    for (let i = 0; i < texts.length; i++) {
                        const original = lessons.find(item => item.id === `lesson-${index}-${p * 5 + i}`);
                        assert.ok(texts[i].includes(original.summary), 'Every grouped original remains reachable in full');
                    }
                    records.push(...texts);
                    if (p < Math.ceil(count / 5) - 1) await nextPage(other).click();
                }
                await other.locator('summary').click();
            }
            assert.equal(new Set(records).size, 83);
            assert.equal(await page.evaluate(() => window.__researchInjected), undefined);
            assert.equal(await target.locator('img').count(), 0);
        });
        await test('experiments: collapsed pages keep all 12 recorded experiments reachable', async page => {
            await openView(page, 'experiments');
            const target = page.locator('#research-experiments');
            const records = target.locator('details.research-experiment');
            assert.equal(await records.count(), 5);
            assert.equal(await target.locator('details[open]').count(), 0);
            const seen = [];
            for (let p = 0; p < 3; p++) {
                seen.push(...await records.locator('summary').allTextContents());
                if (p < 2) await nextPage(target).click();
            }
            assert.equal(new Set(seen).size, 12);
            assert.ok(await nextPage(target).isDisabled());
        });
        await test('safe detail and keyboard: full thesis is accessible without executing stored HTML', async page => {
            await openView(page, 'judgments');
            const first = page.locator('.research-judgment-button').first();
            await first.focus();
            await page.keyboard.press('Enter');
            await page.locator('#research-judgment-detail[aria-busy="false"]').waitFor();
            assert.equal(await first.getAttribute('aria-pressed'), 'true');
            assert.ok((await page.locator('#research-judgment-detail').textContent()).includes(judgments[0].thesis));
            assert.equal(await page.locator('#research-judgment-detail img, #research-judgments img').count(), 0);
            assert.equal(await page.locator('#research-judgment-detail a[href^="javascript:"]').count(), 0);
            assert.equal(await page.evaluate(() => window.__researchInjected), undefined);
            assert.ok(await page.locator('#research-judgment-detail').evaluate(detail => Boolean(detail.compareDocumentPosition(document.querySelector('#research-judgments')) & Node.DOCUMENT_POSITION_FOLLOWING)), 'Detail precedes the list in DOM for narrow screens');
        });
        for (const width of [320, 768, 1024, 1440]) {
            await test(`responsive ${width}px: bounded initial lists, no horizontal overflow, narrow detail in view`, async page => {
                for (const view of ['research', 'judgments', 'experiments']) {
                    await openView(page, view);
                    const dimensions = await page.locator(`[data-view-panel="${view}"]`).evaluate(el => ({width: el.clientWidth, scrollWidth: el.scrollWidth}));
                    assert.ok(dimensions.scrollWidth <= dimensions.width + 1, `${view} panel overflows at ${width}px: ${JSON.stringify(dimensions)}`);
                    const workspace = await page.locator('#workspace-content').evaluate(el => ({width: el.clientWidth, scrollWidth: el.scrollWidth}));
                    assert.ok(workspace.scrollWidth <= workspace.width + 1, `Workspace overflows at ${width}px`);
                }
                assert.ok((await page.locator('#research-lessons').boundingBox()).height < 900, '83 folded lessons should not stretch the initial page');
                await openView(page, 'judgments');
                assert.ok((await page.locator('#research-judgments').boundingBox()).height < 1600, '30 judgments must not render as one long initial list');
                if (width <= 768) {
                    await page.locator('.research-judgment-button').last().click();
                    await page.locator('#research-judgment-detail[aria-busy="false"]').waitFor();
                    await page.waitForFunction(() => {
                        const r = document.querySelector('#research-judgment-detail').getBoundingClientRect();
                        return r.top >= 0 && r.top < window.innerHeight / 2;
                    });
                }
                if (process.env.RESEARCH_UI_ARTIFACT_DIR && [320, 1440].includes(width)) {
                    fs.mkdirSync(process.env.RESEARCH_UI_ARTIFACT_DIR, {recursive: true});
                    for (const view of ['research', 'judgments', 'experiments']) {
                        await openView(page, view);
                        await page.locator(`[data-view-panel="${view}"]`).scrollIntoViewIfNeeded();
                        await page.screenshot({path: path.join(process.env.RESEARCH_UI_ARTIFACT_DIR, `${view}-${width}.png`), animations: 'disabled'});
                    }
                }
            }, {width});
        }
        for (const scenario of ['empty', 'error', 'malformed']) {
            await test(`${scenario}: explicit recoverable state rather than misleading empty or stale lists`, async page => {
                await openView(page, 'judgments');
                const target = page.locator('#research-judgments');
                assert.equal(await target.locator('.research-judgment-button').count(), 0);
                assert.match(await target.innerText(), scenario === 'empty' ? /暂无判断/ : /加载失败/);
                await openView(page, 'experiments');
                for (const id of ['research-lessons', 'research-experiments']) {
                    assert.match(await page.locator(`#${id}`).innerText(), scenario === 'empty' ? /暂无/ : /加载失败/);
                }
            }, {scenario});
        }
    } finally { await browser.close(); }
    console.log(`\nResearch compact UI: ${checks} passed, ${failures.length} failed. All data is synthetic; all requests are intercepted.`);
    if (failures.length) process.exitCode = 1;
}
main().catch(error => { console.error(error); process.exitCode = 1; });
