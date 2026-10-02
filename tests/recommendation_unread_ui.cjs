// Isolated UI regression: only synthetic API data, all network intercepted.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = path.resolve(__dirname, '..');
const BASE = 'http://127.0.0.1:4174';
const stock = {code:'600000', name:'合成股票', action:'setup_ready', entry_price:10, target_price:11, stop_loss_price:9};
const reports = () => ['morning','afternoon','closing'].map((type, i) => ({date:'2026-09-30', period:`2026-09-30_${type}`, type, time:['08:45','13:15','16:45'][i], revision:`revision-${i}`, data:{stock_recommendations:Array.from({length:7}, (_, n)=>({...stock,code:String(600000+n)}))}}));

(async () => {
    const browser = await chromium.launch({headless:true});
    let passed = 0;
    try {
        for (const width of [320,768,1024,1440]) {
            const context = await browser.newContext({viewport:{width,height:1000}, timezoneId:'Asia/Shanghai'});
            const page = await context.newPage();
            await page.clock.setFixedTime(new Date('2026-10-02T10:00:00Z'));
            const errors = [], writes = [], external = [];
            let data = reports();
            let tracking = {tracking_date:'2026-07-17',freshness:{status:'stale',as_of:'2026-07-17',age_calendar_days:77},summary:{win_rate_pct:50},tracks:[]};
            page.on('pageerror', e => errors.push(e.message));
            await context.route('**/*', route => {
                const req = route.request(), url = new URL(req.url());
                if(url.origin !== BASE) { external.push(req.url()); return route.abort(); }
                if(req.method() !== 'GET') { writes.push(req.method()); return route.abort(); }
                const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
                if(url.pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'dashboard/index.html'),'utf8')});
                if(url.pathname.startsWith('/static/')) return route.fulfill({contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'dashboard',path.basename(url.pathname)),'utf8')});
                if(url.pathname === '/api/recommendations/all') return json(data);
                if(url.pathname === '/api/tracking') return json(tracking);
                const values = {'/api/health':{success:true,data:{status:'ok'}},'/api/recommendation':{stock_recommendations:[]},'/api/live/quotes':{},'/api/strategy/params':{},'/api/positions':[], '/api/performance':{initial_cash:20000,total_equity:20000,cash:20000}, '/api/recommendations':[], '/api/analysis/asset':{daily_returns:[]}, '/api/paper/auto-trades':[], '/api/tracking/weekly':[], '/api/tracking/suggestions':{}};
                if(url.pathname === '/favicon.ico') return route.fulfill({status:204});
                return json(values[url.pathname] || {});
            });
            const nav = async view => {
                if(await page.locator('.menu-toggle').isVisible()) await page.locator('.menu-toggle').click();
                await page.locator(`.sidebar-nav-item[data-view-target="${view}"]`).click();
            };
            await page.goto(BASE, {waitUntil:'networkidle'});
            const badge = page.locator('#nav-rec-count');
            assert.equal(await badge.textContent(), '3');
            assert.match(await badge.getAttribute('aria-label'), /3.*未读.*报告/);
            await nav('recommendations');
            // Only morning's first three cards are shown: do not acknowledge
            // afternoon/closing reports that are still hidden by the preview.
            assert.equal(await badge.textContent(), '2');
            assert.equal(await badge.getAttribute('hidden'), null);
            await page.reload({waitUntil:'networkidle'});
            assert.equal(await badge.textContent(), '2');
            await page.locator('#recommendation .recommendation-actions button').click();
            assert.equal(await badge.getAttribute('hidden'), '');
            assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth + 1), `horizontal overflow at ${width}px`);
            assert.match(await page.locator('#page-title-recommendations').innerText(), /最近.*2026-09-30/);
            assert.match(await page.title(), /最近推荐/);
            assert.match(await page.locator('#recommendation-date-note').innerText(), /历史.*不代表.*当前/);
            assert.equal(await page.locator('#recommendation .tag-setup').count(), 0);
            if (process.env.STOCK_UI_ARTIFACT_DIR) {
                fs.mkdirSync(process.env.STOCK_UI_ARTIFACT_DIR, {recursive:true});
                await page.screenshot({path:path.join(process.env.STOCK_UI_ARTIFACT_DIR, `recommendations-${width}.png`),fullPage:true,animations:'disabled'});
            }
            await page.evaluate(()=>refreshData());
            assert.equal(await badge.getAttribute('hidden'), '');
            await page.reload({waitUntil:'networkidle'});
            assert.equal(await badge.getAttribute('hidden'), '');
            await nav('overview');
            data[1].data.stock_recommendations[0].latest_price = 10.5;
            await page.evaluate(()=>refreshData());
            assert.equal(await badge.getAttribute('hidden'), '');
            data[1].revision = 'updated-afternoon';
            await page.evaluate(()=>refreshData());
            assert.equal(await badge.getAttribute('hidden'), null);
            assert.equal(await badge.textContent(), '1');
            await page.evaluate(()=>switchTimeTab('2026-09-30_morning'));
            await nav('recommendations');
            assert.equal(await badge.textContent(), '1');
            await page.locator('[data-period="2026-09-30_afternoon"]').click();
            assert.equal(await badge.getAttribute('hidden'), '');
            // Old active filters must not produce a fake empty list on a new day.
            data = reports().map(r=>({...r,date:'2026-10-02',period:r.period.replace('09-30','10-02')}));
            await page.evaluate(()=>refreshData());
            assert.match(await page.locator('#page-title-recommendations').innerText(), /今日/);
            assert.ok(await page.locator('#recommendation .stock-card').count() > 0);
            await nav('tracking');
            assert.match(await page.locator('#tracking').innerText(), /过期/);
            assert.match(await page.locator('#panel-summary-tracking').textContent(), /过期/);
            if (process.env.STOCK_UI_ARTIFACT_DIR) await page.screenshot({path:path.join(process.env.STOCK_UI_ARTIFACT_DIR, `tracking-${width}.png`),fullPage:true,animations:'disabled'});
            tracking = {tracking_date:'2026-10-02',freshness:{status:'fresh',as_of:'2026-10-02'},data_quality:{status:'incomplete'},summary:{win_rate_pct:50,statistics_trustworthy:false},tracks:[]};
            await page.evaluate(()=>loadTracking());
            assert.match(await page.locator('#tracking .data-quality-warning').innerText(), /历史不完整/);
            assert.equal(await page.locator('#tracking .summary-stat').filter({hasText:'已结束胜率'}).locator('.value').innerText(), '待核验');
            assert.deepEqual(errors, []); assert.deepEqual(writes, []); assert.deepEqual(external, []);
            if (width === 1440) {
                await page.addInitScript(()=>Object.defineProperty(window, 'localStorage', {get(){throw new Error('Storage disabled for test');}}));
                await page.reload({waitUntil:'networkidle'});
                await nav('recommendations');
                await page.locator('#recommendation .recommendation-actions button').click();
                assert.equal(await badge.getAttribute('hidden'), '');
                await page.evaluate(()=>refreshData());
                assert.equal(await badge.getAttribute('hidden'), '');
                assert.deepEqual(errors, []);
            }
            passed++;
            console.log(`PASS unread persistence, new revisions, holiday labels, stale tracking at ${width}px`);
            await context.close();
        }
    } finally {await browser.close();}
    console.log(`${passed} browser scenarios passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});
