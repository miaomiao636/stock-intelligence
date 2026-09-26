# Stock Intelligence v1.0

面向 A 股研究的智能分析与**自动模拟交易**系统。系统每天生成盘前、午后和盘后报告；09:35 使用当日行情重新计算候选，通过候选池、价格、市场状态和仓位风控后自动执行模拟交易，再由飞书发送执行结果。

> 仅供研究与模拟，不构成投资建议；当前版本不连接真实券商。

## 研究工作台更新（2026-09-26，本地待部署）

- 新增研究助手、判断回放、经验与实验三个侧栏页面。
- 研究判断与证据快照只追加不改写；复盘分开记录预测、执行和账户损益。历史材料不完整时明确标记。
- 盘后评估会生成待验证的经验卡；人工审核不会直接改变风控或上线策略。
- 事实查询不调用模型；专家模式按需调用量化/事件/反方三个视角，具有共享额度与幂等保护。
- 修复交易双边费用归因、交易日持有期、目标价质量门和同日待触发计划重查。
- 详细功能、验证限制、API 与服务器更新步骤见 [研究工作台更新说明](docs/RESEARCH_WORKBENCH.md)。

生产状态必须以服务器的提交、服务和同一 SQLite 账本核验，不把旧余额、旧收益或本地测试当作当前线上状态。本次开发尚未完成服务器部署；云端 `data/stock_intelligence.db` 仍是唯一权威账本。

## 已实现的安全边界

- 模拟账户支持经鉴权的现金调整；收益率按“原始本金 + 净入金”计算，资金调整不计入交易收益。
- 最多 2 只、单只不超过 30%、至少保留 30% 现金。
- 单笔经济订单不低于 ¥1,000，计入最低佣金、印花税、过户费和滑点。
- 普通 A 股按批次执行 T+1；100 股买入单位；非交易时段、过期行情和不可交易状态拒绝撮合。
- 08:45 生成盘前分析；09:35 再取价、重算并自动执行；13:15 生成午后报告；16:45 盘后复盘。
- SQLite 是账户、资金调整、订单、决定、成交、持仓批次和权益的唯一事实源。
- Fallback、静态模板、行情时间不可靠和候选池异常时禁止新开仓。
- 当日亏损达到 1.5% 或最大回撤达到 10% 时暂停新开仓。
- 已有 Champion/Challenger 注册门槛；研究工作台不会自动晋级策略。注册表不等于已完成前向影子验证。

## 快速开始

```bash
cd ~/github-ai-tools/stock-intelligence
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python cli.py init
python cli.py paper init --cash 4000
python cli.py doctor
python -m pytest -q
python -m pytest -q -m integration
```

新环境默认保持 `PAPER_TRADING_ENABLED=false`。只有在飞书、行情、候选池、定时任务和风控验收通过后，才可改为 `true`。当前腾讯云生产环境已经完成这些配置并处于启用状态。

## 飞书配置

普通群机器人 Webhook 用于发送报告；飞书自建应用和官方长连接用于接收事件。生产环境应只保留一个权威长连接消费者，避免同一飞书应用的事件被多个本地/云端进程随机分发。

```dotenv
HOST=127.0.0.1
PUBLIC_BASE_URL=
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=...
FEISHU_RECEIVE_ID=...
FEISHU_RECEIVE_ID_TYPE=chat_id
FEISHU_VERIFICATION_TOKEN=...
FEISHU_CALLBACK_MODE=auto
FEISHU_HTTP_FALLBACK_ENABLED=false
API_KEY=一段足够长的随机值
PAPER_TRADING_ENABLED=false
```

在飞书开发者后台把“事件与回调”的订阅方式设置为“使用长连接接收事件”，保存并发布版本。长连接健康状态写入：

```text
data/runtime/feishu_ws_status.json
```

只有使用 HTTP 备用模式时，`PUBLIC_BASE_URL` 才必须是飞书能够访问的公网 HTTPS 地址。

## 每日自动流程

| 时间 | 动作 |
|---|---|
| 08:45 | 收集行情、新闻和政策，生成盘前报告，不创建成交 |
| 08:55 | 检查盘前推送回执，缺失时补发或发送异常提示 |
| 09:35 | 获取当日行情，重算候选和仓位；通过全部门禁后自动模拟成交并推送结果 |
| 09:45 | 检查 09:35 阶段回执；中断、漏报或通知失败时安全补偿 |
| 09:00–15:00 每分钟 | 扫描待执行订单；无待执行订单时不请求行情、不发送消息 |
| 盘中多个时间点 | 盯市、权益快照、止损、目标价和持有周期检查；仅变化时推送 |
| 13:15 | 根据上午走势、最新行情、新闻和账户状态生成午后报告 |
| 13:25 | 检查午后推送回执并安全补偿 |
| 16:45 | 盘后评估、扣费后净收益、回撤和策略观察 |
| 16:55 | 检查盘后推送回执，缺失时补发 |

生产定时任务模板位于 `deploy/stock-intelligence.cron`，腾讯云安装结果位于 `/etc/cron.d/stock-intelligence`。

## 常用命令

```bash
# 报告
python cli.py daily --mode morning
python cli.py daily --mode afternoon
python cli.py daily --mode closing
python cli.py daily --mode morning --dry-run --no-notify --force
python cli.py notify --mode closing --if-missing

# 自动模拟交易
python cli.py paper open
python cli.py paper open --if-missing
python cli.py paper execute-due
python cli.py paper intraday
python cli.py paper account
python cli.py paper positions
python cli.py paper orders

# 环境与通知
python cli.py doctor
python cli.py notify-test
python cli.py notify-test --interactive
```

`notify-test --interactive` 只验证飞书回调链路，不创建订单，也不触发成交。

旧 AutoTrader 和旧的直接成交入口已经停用。所有自动成交必须进入统一交易账本，并经过行情新鲜度、候选池、价格、仓位、交易时段和风险门禁。

## 核心目录

```text
src/paper_trading/trading_service.py  唯一撮合入口和交易规则
src/paper_trading/workflow.py         09:35自动执行、到期执行和盘中监控
src/storage/trading_ledger.py         SQLite事务账本
src/notifier/feishu.py                报告与飞书通知
src/strategy/registry.py              Champion/Challenger晋级门禁
config/strategy.yaml                  仓位、股价上限和风险参数
config/cost_model.yaml                费用与滑点
deploy/stock-intelligence.cron        云端调度模板
tests/                                单元、API、账本、风控和集成测试
```

## 当前阶段与下一验收目标

当前项目属于“可持续运行的云端自动模拟交易 MVP”，不是已经证明有效的短线策略系统。下一阶段的主要任务不是继续增加页面功能，而是积累可审计样本：

1. 连续运行至少 20 个交易日。
2. 累积至少 30 笔已平仓交易后，首次评估短线胜率、盈亏比、费用后收益和最大回撤。
3. 每周核对云端备份、飞书长连接、定时任务和账本完整性。
4. 达到 100 笔已平仓和 20 个影子交易日后，再决定是否允许 Challenger 晋级。
5. 在此之前不连接真实券商，不把短期浮盈解释为策略已经有效。
