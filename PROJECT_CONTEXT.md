# 项目上下文

## 系统定位

Stock Intelligence 是一个 A 股研究与自动模拟交易系统。它负责行情与新闻采集、候选池构建、LLM 综合研判、推荐生成、模拟交易、持仓估值、盘后评估、策略观察和飞书通知。

## 技术栈

- Python 3.11+
- FastAPI / Uvicorn Web 服务
- SQLite 交易账本
- 原生 HTML、CSS、JavaScript 仪表盘
- 飞书机器人 Webhook 与官方长连接
- cron + systemd 云端自动化
- pytest 测试

## 关键目录

- `cli.py`：命令行入口。
- `server.py`：HTTP API 与仪表盘服务。
- `dashboard/`：前端页面。
- `src/analysis/`：盘前、午后和综合分析。
- `src/data_collectors/`：行情、新闻和候选池采集。
- `src/evaluation/`：盘后评估、收益与回撤计算。
- `src/paper_trading/`：模拟交易账户、订单、风控与执行流程。
- `src/storage/`：SQLite 读写与交易账本。
- `src/notifier/`：飞书报告、卡片与长连接事件。
- `config/`：策略、数据源、成本与合规配置。
- `deploy/`：Ubuntu 安装、更新、systemd 和 cron。
- `tests/`：自动化测试。

## 日常流程

| 时间 | 任务 |
| --- | --- |
| 08:45 | 收集行情、新闻与政策，生成盘前报告，不成交 |
| 09:35 | 重新获取行情，重算候选与仓位，通过风控后自动模拟成交 |
| 09:45 | 检查 09:35 阶段回执并安全补偿 |
| 13:15 | 根据上午走势、最新行情和账户状态生成午后报告 |
| 盘中 | 每 30 分钟进行持仓与风险检查，状态变化时通知 |
| 16:45 | 盘后估值、评估、扣费后收益、回撤与策略观察 |

## 主要命令

```bash
python cli.py doctor
python cli.py daily --mode morning
python cli.py daily --mode afternoon
python cli.py daily --mode closing
python cli.py paper account
python cli.py paper open
python cli.py paper intraday
python cli.py paper execute-due
python cli.py notify-test
```

开发测试优先使用项目 `.venv/bin/python`。

## 部署关系

- 本地仓库负责开发、测试和生成可部署代码。
- GitHub 保存不含密钥和运行数据的源码与文档。
- 腾讯云服务器负责 7x24 小时 Web 服务、定时任务、飞书长连接和模拟交易。
- 云端 `.env` 与 `data/` 不由 Git 管理，更新脚本必须保留它们。
- 域名与 Cloudflare 只负责访问入口和代理，不是交易数据源。

## 数据权威性

云端 `/opt/stock-intelligence/data/stock_intelligence.db` 是当前模拟账户、订单、成交、持仓与权益快照的权威账本。网页与飞书都应读取这一账本；本地历史数据库仅用于追溯和受控迁移。
