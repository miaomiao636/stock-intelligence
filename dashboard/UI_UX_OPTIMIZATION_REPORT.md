# Stock Intelligence Dashboard — UI/UX 优化报告

**版本**: v0.7 → v0.8 建议  
**文件**: `dashboard/index.html` (1199行, 73KB)  
**评估日期**: 2026-07-09

---

## 一、总体评估

### ✅ 现有优势
1. **单文件架构** — 零依赖，直接打开即用，适合内部工具
2. **卡片式模块布局** — 功能区清晰分离（推荐/持仓/板块/新闻/策略/跟踪等）
3. **事件委托** — 用 `document.addEventListener('click', ...)` 统一处理点击，避免内联事件
4. **价格阈值即时生效** — slider拖动即筛选，体验流畅
5. **5分钟自动刷新** — `setInterval(refreshData, 5*60*1000)` 保持数据新鲜

### ❌ 核心问题
1. **信息密度极高** — 1199行全在一个文件，CSS/HTML/JS 混杂
2. **无图表可视化** — 资产分析、收益曲线全是纯表格，缺乏直观图表
3. **大量内联样式** — ~300行内联 `style=""` 前后不一致
4. **表格样式普通** — pos-table/asset-table 无斑马纹、无固定表头
5. **响应式不完善** — 只有 1024px 和 600px 两个断点，768px平板体验差
6. **错误处理粗糙** — `alert()` 弹窗体验差，无重试机制
7. **空状态浪费空间** — 全部数据显示"暂无"时页面极空

---

## 二、优化建议清单（按优先级排序）

### P0 — 高优先级（立即修复）

#### 1. 引入 Chart.js 图表可视化
**问题**: 资产分析、收益曲线全部是纯表格，无法直观看到趋势。  
**方案**: 在 `<head>` 中引入 Chart.js CDN，替换/增强资产分析区域。

```html
<!-- 在 <head> 中添加 -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
```

**具体修改 — 资产分析增加折线图**:

```javascript
// 在 renderAssetTab() 函数末尾，表格之后添加画布
function renderAssetTab(period) {
    // ... 现有表格代码 ...
    
    // 添加图表容器
    el.innerHTML = tabHtml + tHtml + '<canvas id="asset-chart" height="160" style="margin-top:12px;"></canvas>';
    
    // 绘制图表
    const ctx = document.getElementById('asset-chart');
    if (ctx && data.length > 1) {
        const labels = data.slice(-10).reverse().map(r => r.period);
        const values = data.slice(-10).reverse().map(r => r.end_equity || 0);
        const returns = data.slice(-10).reverse().map(r => r.return_pct || 0);
        
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: '期末资产',
                    data: values,
                    borderColor: '#1a1a2e',
                    backgroundColor: 'rgba(26,26,46,0.08)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    yAxisID: 'y'
                }, {
                    label: '收益率%',
                    data: returns,
                    borderColor: returns[returns.length-1] >= 0 ? '#27ae60' : '#e74c3c',
                    borderDash: [5, 5],
                    pointRadius: 3,
                    yAxisID: 'y1'
                }]
            },
            options: {
                responsive: true,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } } },
                scales: {
                    y: { position: 'left', ticks: { callback: v => '¥' + v.toLocaleString() } },
                    y1: { position: 'right', grid: { drawOnChartArea: false }, ticks: { callback: v => v + '%' } }
                }
            }
        });
    }
}
```

**新增 — 持仓分布饼图** (在持仓卡片底部):

```javascript
// loadPositions() 函数末尾添加
if (data.length > 0) {
    h += '<canvas id="position-pie" height="140" style="margin-top:12px;"></canvas>';
    el.innerHTML = h;
    
    const ctx = document.getElementById('position-pie');
    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(p => p.name || p.code),
            datasets: [{
                data: data.map(p => (p.current_price || 0) * (p.quantity || 0)),
                backgroundColor: ['#1a1a2e','#16213e','#0f3460','#e74c3c','#f39c12','#2196f3','#4caf50','#9c27b0']
            }]
        },
        options: {
            plugins: {
                legend: { position: 'bottom', labels: { font: { size: 11 }, boxWidth: 12 } },
                tooltip: { callbacks: { label: ctx => ctx.label + ': ¥' + ctx.parsed.toLocaleString() } }
            }
        }
    });
}
```

---

#### 2. 改进表格样式 — 斑马纹 + 粘性表头

```css
/* 替换现有 pos-table 样式 */
.pos-table { 
    width: 100%; border-collapse: collapse; font-size: 13px; 
    display: block; max-height: 400px; overflow-y: auto;
}
.pos-table thead { 
    position: sticky; top: 0; z-index: 1; background: #fafafa; 
}
.pos-table th { 
    text-align: left; padding: 10px 8px; color: #999; font-weight: 600; 
    font-size: 12px; border-bottom: 2px solid #e8e8e8; text-transform: uppercase; letter-spacing: 0.5px;
}
.pos-table td { 
    padding: 10px 8px; border-bottom: 1px solid #f5f5f5; transition: background 0.15s;
}
.pos-table tbody tr:nth-child(even) { background: #fafbfc; }
.pos-table tbody tr:hover { background: #f0f4ff; }

/* 资产分析表格同理 */
.asset-table tbody tr:nth-child(even) { background: #fafbfc; }
.asset-table tbody tr:hover { background: #f0f4ff; }
.asset-table td, .asset-table th { padding: 8px; border-bottom: 1px solid #f0f0f0; }
```

---

#### 3. 修复内联样式 → 提取到CSS类

**问题**: 大量 `style="..."` 散落在 JS 字符串模板中，难以维护。

```css
/* 提取常见的内联样式为CSS类 */
.warning-box { background: #fff3e0; border: 1px solid #ffcc80; border-radius: 6px; padding: 8px 10px; margin-top: 8px; font-size: 12px; }
.info-box { background: #fff8e1; border: 1px solid #ffe082; border-radius: 8px; padding: 12px; margin-top: 12px; font-size: 12px; color: #e65100; }
.success-box { background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 6px; padding: 8px 10px; font-size: 12px; }
.trade-day { margin-bottom: 12px; padding: 10px; background: #f8f9fa; border-radius: 8px; }
.weekly-card { margin-bottom: 14px; padding: 12px; background: #f8f9fa; border-radius: 8px; }
.section-title { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.price-tag { font-size: 12px; padding: 2px 8px; border-radius: 3px; }
.filter-chip { padding: 4px 12px; font-size: 12px; border-radius: 4px; cursor: pointer; transition: all 0.15s; }
.filter-chip.active { background: #1a1a2e; color: white; }
.filter-chip:not(.active) { background: #f5f5f5; color: #666; }
.filter-chip:not(.active):hover { background: #e8e8e8; }
```

---

### P1 — 中优先级（近期优化）

#### 4. 响应式断点增强

```css
/* 增加 768px 平板断点 */
@media (max-width: 768px) {
    .grid { grid-template-columns: 1fr; }
    .index-bar { gap: 16px; }
    .account-banner { grid-template-columns: repeat(2, 1fr); }
    .stock-prices { grid-template-columns: repeat(2, 1fr); }
    .summary-grid { grid-template-columns: repeat(3, 1fr); }
    .stock-card .tags { flex-wrap: wrap; }
    .control-group .slider-row label { width: 56px; font-size: 12px; }
}

/* 优化现有 600px 断点 */
@media (max-width: 600px) {
    .container { padding: 8px; }
    header { flex-direction: column; text-align: center; gap: 8px; padding: 12px 16px; }
    header h1 { font-size: 18px; }
    .account-banner { grid-template-columns: repeat(2, 1fr); }
    .account-stat .value { font-size: 16px; }
    .summary-grid { grid-template-columns: repeat(2, 1fr); }
    .stock-prices { grid-template-columns: 1fr 1fr; gap: 6px; }
    .modal-content { width: 96%; max-height: 90vh; }
    /* 隐藏低优先级卡片在移动端 */
    .control-group h3 { font-size: 12px; }
    .slider-row label { width: 48px; font-size: 11px; }
}
```

---

#### 5. 用 Toast 替代 alert()

```css
/* 添加 toast 样式 */
.toast { 
    position: fixed; top: 20px; right: 20px; z-index: 9999;
    padding: 12px 20px; border-radius: 8px; font-size: 13px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    animation: slideIn 0.3s ease, fadeOut 0.3s ease 2.7s;
    max-width: 320px;
}
.toast-success { background: #e8f5e9; color: #2e7d32; border-left: 4px solid #4caf50; }
.toast-error { background: #ffebee; color: #c62828; border-left: 4px solid #e74c3c; }
.toast-info { background: #e3f2fd; color: #1565c0; border-left: 4px solid #2196f3; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }
```

```javascript
// 替换所有 alert() 调用
function showToast(msg, type = 'info') {
    const t = document.createElement('div');
    t.className = 'toast toast-' + type;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
}

// 用法示例（替换 updateAccount 中的 alert）:
// showToast('已更新', 'success');
// showToast('更新失败', 'error');
```

---

#### 6. 添加加载骨架屏

```css
/* 骨架屏动画 */
.skeleton { 
    background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
    background-size: 200% 100%;
    animation: shimmer 1.5s infinite;
    border-radius: 4px;
}
.skeleton-line { height: 14px; margin-bottom: 8px; }
.skeleton-line.short { width: 60%; }
.skeleton-line.medium { width: 80%; }
.skeleton-block { height: 80px; margin-bottom: 12px; }
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
```

```javascript
// 替换现有的 loading div
function skeletonLines(n = 3) {
    return Array(n).fill(0).map((_, i) => 
        `<div class="skeleton skeleton-line ${i === n-1 ? 'short' : 'medium'}"></div>`
    ).join('');
}

// 使用: `<div class="loading">${skeletonLines(4)}</div>` 替代 `<div class="loading">加载中...</div>`
```

---

#### 7. Modal 改进 — 更大、更实用

```css
/* 增强 modal */
.modal-content { 
    background: white; border-radius: 16px; max-width: 720px; width: 94%; 
    max-height: 88vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); 
}
.modal-body { padding: 24px; }
.modal-body h2 { font-size: 20px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
.modal-body h3 { font-size: 15px; color: #1a1a2e; margin: 12px 0 8px; }
.modal-close { 
    position: absolute; top: 12px; right: 16px; background: none; border: none; 
    font-size: 20px; cursor: pointer; color: #999; padding: 4px 8px; border-radius: 4px;
}
.modal-close:hover { background: #f5f5f5; color: #333; }
```

---

### P2 — 低优先级（锦上添花）

#### 8. 卡片折叠/展开

```javascript
// 给每个 card h2 添加折叠功能
document.querySelectorAll('.card h2').forEach(h2 => {
    h2.style.cursor = 'pointer';
    h2.style.userSelect = 'none';
    h2.innerHTML += ' <span class="collapse-icon" style="font-size:11px;color:#ccc;margin-left:auto;">▼</span>';
    h2.addEventListener('click', () => {
        const card = h2.closest('.card');
        const body = h2.nextElementSibling;
        if (body) {
            const hidden = body.style.display === 'none';
            body.style.display = hidden ? '' : 'none';
            h2.querySelector('.collapse-icon').textContent = hidden ? '▼' : '▶';
        }
    });
});
```

---

#### 9. 暗色模式支持

```css
/* 添加暗色模式变量 */
:root {
    --bg-primary: #f0f2f5;
    --bg-card: white;
    --text-primary: #333;
    --text-secondary: #999;
    --border-light: #f0f0f0;
    --shadow: 0 1px 4px rgba(0,0,0,0.06);
}

[data-theme="dark"] {
    --bg-primary: #0d1117;
    --bg-card: #161b22;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --border-light: #21262d;
    --shadow: 0 1px 4px rgba(0,0,0,0.3);
}

body { background: var(--bg-primary); color: var(--text-primary); }
.card { background: var(--bg-card); box-shadow: var(--shadow); }
.pos-table th { border-bottom-color: var(--border-light); }
.pos-table td { border-bottom-color: var(--border-light); }
```

---

#### 10. 股票卡片增加迷你趋势线

```javascript
// 在 stock-card 的价格区域添加 5日迷你K线
function miniSparkline(prices, width = 80, height = 24) {
    if (!prices || prices.length < 2) return '';
    const min = Math.min(...prices), max = Math.max(...prices);
    const range = max - min || 1;
    const points = prices.map((p, i) => {
        const x = (i / (prices.length - 1)) * width;
        const y = height - ((p - min) / range) * height;
        return `${x},${y}`;
    }).join(' ');
    const color = prices[prices.length-1] >= prices[0] ? '#e74c3c' : '#27ae60';
    return `<svg width="${width}" height="${height}" style="vertical-align:middle;"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}
```

---

#### 11. 键盘快捷键

```javascript
// 添加键盘快捷键
document.addEventListener('keydown', (e) => {
    // R = 刷新数据
    if (e.key === 'r' && !e.ctrlKey && !e.metaKey && document.activeElement.tagName !== 'INPUT') {
        refreshData();
        showToast('数据已刷新', 'info');
    }
    // 数字键切换时间周期 tab
    if (e.key >= '1' && e.key <= '9' && document.activeElement.tagName !== 'INPUT') {
        const tabs = document.querySelectorAll('.time-tab');
        const idx = parseInt(e.key) - 1;
        if (tabs[idx]) tabs[idx].click();
    }
    // Escape 关闭 modal
    // (已有实现)
});
```

---

## 三、代码架构建议

### 将文件拆分为三个文件（可选）

```
dashboard/
├── index.html      (~300行，纯HTML结构)
├── style.css       (~200行，所有CSS)
└── app.js          (~700行，所有JavaScript)
```

**拆分后的 index.html 头部**:
```html
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Intelligence - 智能选股分析系统</title>
    <link rel="stylesheet" href="style.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <script src="app.js" defer></script>
</head>
```

> **注意**: 如果保持单文件分发（便于分享），可以不拆分，但建议用注释块明确分区。

---

## 四、优先级实施路线图

| 阶段 | 任务 | 预计工时 |
|------|------|---------|
| **Week 1** | P0-1: Chart.js 图表 | 2h |
| | P0-2: 表格样式优化 | 1h |
| | P0-3: 内联样式提取 | 1.5h |
| **Week 2** | P1-4: 响应式断点 | 1h |
| | P1-5: Toast 替代 alert | 1h |
| | P1-6: 骨架屏 | 1h |
| | P1-7: Modal 改进 | 0.5h |
| **Week 3** | P2-8: 卡片折叠 | 0.5h |
| | P2-9: 暗色模式 | 2h |
| | P2-10: 迷你趋势线 | 1h |
| | P2-11: 键盘快捷键 | 0.5h |

---

## 五、总结

当前 Dashboard 功能完整、布局合理，主要短板在于：
1. **纯表格 → 无图表** — 这是最大痛点，Chart.js 引入后视觉效果会质的飞跃
2. **内联样式泛滥** — 影响可维护性，提取为CSS类即可
3. **alert() 弹窗** — 粗暴打断用户流程，用 Toast 替代
4. **响应式断点不足** — 768px 平板用户会遇到布局问题

以上优化不改变任何后端逻辑，纯前端改动，风险极低。
