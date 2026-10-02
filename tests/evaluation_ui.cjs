// Isolated, synthetic browser acceptance. No backend, credentials or live requests.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = path.resolve(__dirname, '..');
const BASE = 'http://127.0.0.1:4176';
const diagnostics = () => ({
    as_of:'2026-10-03T12:00:00+08:00',
    tracking:{available:true,tracking_date:'2026-07-17',freshness:{status:'stale',age_calendar_days:78},data_quality:{status:'incomplete'}},
    execution:{requested_date:'2026-10-03',trade_date:'2026-09-30',is_latest_available:true,
        summary:{total:7,by_status:{rejected:7},reason_counts:{'最新研究已撤回或降级该候选':2,'推荐置信度不足':2,'推荐仅供观察，未通过交易资格':3}},
        note:'查询日期无计划，以下展示最近有记录的交易日，不代表今日交易。'},
    scorecard:null,
});

(async () => {
    const browser = await chromium.launch({headless:true});
    let passed = 0;
    try {
        for (const width of [320,768,1024,1440]) {
            const context = await browser.newContext({viewport:{width,height:1000},timezoneId:'Asia/Shanghai'});
            const page = await context.newPage();
            page.setDefaultTimeout(3500);
            await page.clock.setFixedTime(new Date('2026-10-03T04:00:00Z'));
            const errors = [], writes = [], external = [], leakedKeys = [], unexpected = [];
            let response = diagnostics(), responseStatus = 200;
            let releaseLoading;
            const loadingGate = width === 1440 ? new Promise(resolve => {releaseLoading = resolve;}) : null;
            page.on('pageerror', error => errors.push(error.message));
            await context.route('**/*', async route => {
                const req = route.request(), url = new URL(req.url());
                if (url.origin !== BASE) {external.push(req.url()); return route.abort();}
                if (req.method() !== 'GET') {writes.push(req.method()); return route.abort();}
                if ('x-api-key' in req.headers()) leakedKeys.push(url.pathname);
                const json = (body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
                if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'dashboard/index.html'),'utf8')});
                if (url.pathname.startsWith('/static/')) return route.fulfill({contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'dashboard',path.basename(url.pathname)),'utf8')});
                if (url.pathname === '/api/research/diagnostics') {
                    if (loadingGate) await loadingGate;
                    return json(response,responseStatus);
                }
                if (url.pathname === '/favicon.ico') return route.fulfill({status:204});
                const values = {'/api/health':{success:true,data:{status:'ok'}}, '/api/recommendations/all':[], '/api/recommendation':{stock_recommendations:[]},'/api/live/quotes':{},'/api/strategy/params':{},'/api/positions':[], '/api/performance':{initial_cash:20000,total_equity:20000,cash:20000}, '/api/recommendations':[], '/api/analysis/asset':{daily_returns:[]}, '/api/paper/auto-trades':[], '/api/tracking':{}, '/api/tracking/weekly':[], '/api/tracking/suggestions':{}, '/api/research/overview':{judgments_count:703,lessons_count:89,experiments_count:0,entry_plans:{total:0}}, '/api/research/judgments':{items:[]}};
                if (Object.hasOwn(values,url.pathname)) return json(values[url.pathname]);
                unexpected.push(url.pathname);
                return json({error:'Unexpected endpoint in synthetic fixture'},404);
            });
            const nav = async view => {
                if (await page.locator('.menu-toggle').isVisible()) await page.locator('.menu-toggle').click();
                await page.locator(`.sidebar-nav-item[data-view-target="${view}"]`).click();
            };
            await page.goto(BASE,{waitUntil:loadingGate ? 'domcontentloaded' : 'networkidle'});
            const home = page.locator('#overview-diagnostics');
            if (loadingGate) {
                assert.equal(await home.getAttribute('aria-busy'),'true');
                assert.match(await home.innerText(), /加载中/);
                releaseLoading();
            }
            await page.waitForFunction(()=>document.querySelector('#overview-diagnostics')?.textContent.includes('2026-09-30'),null,{timeout:3000});
            assert.match(await home.innerText(), /运行与评估/);
            assert.match(await home.innerText(), /跟踪.*过期/);
            assert.match(await home.innerText(), /78/);
            assert.match(await home.innerText(), /2026-09-30/);
            assert.match(await home.innerText(), /预测.*不等于.*账户收益/);
            assert.equal(await home.locator('details[open]').count(),0);
            assert.ok((await home.boundingBox()).height < (width === 320 ? 610 : 380),'summary must stay compact');
            const details = home.locator('details').first();
            await details.locator('summary').focus();
            await page.keyboard.press('Enter');
            assert.equal(await details.getAttribute('open'),'');
            assert.match(await home.innerText(), /推荐置信度不足.*2/);
            await details.locator('summary').click();
            await nav('research');
            const research = page.locator('#research-diagnostics');
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('2026-09-30'));
            assert.match(await research.innerText(), /2026-09-30/);
            assert.equal(await research.locator('details[open]').count(),0);
            const sampleCounts = {records_total:14,eligible:8,evaluable:4,correct:3,incorrect:1,pending:3,unavailable:1,excluded:4,duplicate:2,accuracy_pct:75,always_up_accuracy_pct:75,coverage_pct:50};
            response.scorecard = {as_of:'2026-10-03T12:00:00+08:00',protocol_version:'direction_maturity_v1',summary:sampleCounts,
                by_horizon:[{horizon_sessions:3,summary:sampleCounts},{horizon_sessions:5,summary:{...sampleCounts,evaluable:0,accuracy_pct:null,always_up_accuracy_pct:null,coverage_pct:0}}],
                exclusion_reasons:{history_incomplete:4},sample_dependence:'overlapping_windows_not_independent',affects_trading:false,limitations:['合成数据，窗口重叠，不能假定独立样本。']};
            await page.locator('#research-refresh').click();
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('75.0%'));
            assert.match(await research.innerText(), /可评估 4/);
            assert.match(await research.innerText(), /待到期\/待复核 3/);
            await research.locator('.research-scorecard-details > summary').click();
            assert.match(await research.innerText(), /始终预测上涨/);
            assert.match(await research.innerText(), /历史证据不足.*4/);
            assert.match(await research.locator('tr').filter({hasText:'5 个交易日'}).innerText(), /--/);
            assert.doesNotMatch(await research.locator('tr').filter({hasText:'5 个交易日'}).innerText(), /0\.0%/);
            await research.locator('.research-scorecard-details > summary').click();
            if (process.env.STOCK_UI_ARTIFACT_DIR) {
                fs.mkdirSync(process.env.STOCK_UI_ARTIFACT_DIR,{recursive:true});
                await research.screenshot({path:path.join(process.env.STOCK_UI_ARTIFACT_DIR,`diagnostics-${width}.png`),animations:'disabled'});
            }
            response.tracking = {available:true,tracking_date:'2026-10-03',freshness:{status:'fresh'},
                data_quality:{status:'ok'},tracking_quality:{status:'incomplete',data_error_samples:2,history_incomplete_samples:1,path_ambiguous_samples:0}};
            response.scorecard.exclusion_reasons = {forecast_input_snapshot_missing:2};
            await page.locator('#research-refresh').click();
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('样本不完整'));
            assert.doesNotMatch(await research.innerText(), /跟踪已更新/);
            await research.locator('details').first().locator('summary').click();
            assert.match(await research.innerText(), /行情错误 2/);
            await research.locator('.research-scorecard-details > summary').click();
            assert.match(await research.innerText(), /缺少模型输入行情快照/);
            response = {as_of:'2026-10-03T12:10:00+08:00',tracking:{available:false}, execution:{requested_date:'2026-10-03',trade_date:null,is_latest_available:false,summary:{total:0,by_status:{},reason_counts:{}}},scorecard:null};
            await page.locator('#research-refresh').click();
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('暂无计划记录'));
            assert.match(await research.innerText(), /未归档.*不等于.*失败/);
            assert.doesNotMatch(await research.innerText(), /失败率.*0%|正确率.*0%/);
            response = {tracking:{available:true},execution:{summary:{}}};
            await page.locator('#research-refresh').click();
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('加载失败'));
            assert.doesNotMatch(await research.innerText(), /暂无计划记录/);
            responseStatus = 503;
            await page.locator('#research-refresh').click();
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('加载失败'));
            assert.doesNotMatch(await research.innerText(), /2026-09-30/);
            responseStatus = 200;
            response = diagnostics();
            response.execution.summary.reason_counts = {'<img src=x onerror="window.pwned=true">':7};
            await research.getByRole('button',{name:'重试'}).click();
            await page.waitForFunction(()=>document.querySelector('#research-diagnostics')?.textContent.includes('2026-09-30'));
            await research.locator('details').first().locator('summary').click();
            assert.match(await research.innerText(), /<img src=x/);
            assert.equal(await research.locator('img').count(),0);
            assert.equal(await page.evaluate(()=>window.pwned),undefined);
            assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth + 1),`horizontal overflow at ${width}px`);
            assert.deepEqual(errors,[]); assert.deepEqual(writes,[]); assert.deepEqual(external,[]); assert.deepEqual(leakedKeys,[]); assert.deepEqual(unexpected,[]);
            passed++;
            console.log(`PASS diagnostics dates, freshness, folded reasons, missing/error/retry, escaped content and layout at ${width}px`);
            await context.close();
        }
    } finally {await browser.close();}
    console.log(`${passed} diagnostics browser scenarios passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});
