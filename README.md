# Stock Intelligence v1.0

面向A股研究的智能分析与**模拟交易**系统。系统每天生成盘前候选，09:35使用当日行情重新计算，随后通过飞书交互卡片让用户确认、否决或暂停；五分钟无操作时，仅在再次取价和全部风控通过后模拟成交。

> 仅供研究与模拟，不构成投资建议；当前版本不连接真实券商。

## 已实现的安全边界

- 初始模拟资金固定为 ¥4,000；最多2只、单只不超过30%、至少保留30%现金。
- 单笔经济订单不低于 ¥1,000，计入最低佣金、印花税、过户费和滑点。
- 普通A股按批次执行T+1；100股买入单位；非交易时段、过期行情和不可交易状态拒绝撮合。
- 08:45只分析，08:50只发预告；09:35最终计划；确认后或五分钟到期时重新取价。
- SQLite是账户、订单、决定、成交、持仓批次和权益的唯一事实源。
- Fallback、静态模板、行情时间不可靠和飞书状态不确定时禁止开仓。
- 当日亏损达到1.5%或最大回撤达到10%时暂停新开仓。
- 策略采用Champion/Challenger；至少100笔已平仓和20个影子交易日后才允许晋级。

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

## 飞书双向配置

普通群机器人Webhook只能发送报告。交易卡片必须配置飞书自建应用，并在 `.env` 设置：

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=...
FEISHU_RECEIVE_ID=...
FEISHU_RECEIVE_ID_TYPE=chat_id
FEISHU_VERIFICATION_TOKEN=...
API_KEY=一段足够长的随机值
PAPER_TRADING_ENABLED=false
```

把飞书卡片回调指向：

```text
POST https://你的可访问地址/api/feishu/callback
```

先保持 `PAPER_TRADING_ENABLED=false`。完成卡片发送、回调、重复点击、迟到回调、服务重启和跳价拒绝测试后，才改为 `true`。

## 每日流程

| 时间 | 动作 |
|---|---|
| 08:45 | 收集行情、新闻和政策，生成盘前报告，不创建成交 |
| 08:50左右 | 飞书发送只读候选预告 |
| 09:35 | 重新获取当日行情，生成最终订单并发送交互卡片 |
| 确认后/5分钟到期 | 再次取价、原子抢单、风控和模拟撮合 |
| 盘中每30分钟 | 盯市、权益快照、止损/目标/持有周期检查；仅变化时推送 |
| 16:45 | 盘后评估、净收益与回撤记录、策略观察 |

定时任务模板位于 `deploy/stock-intelligence.cron`。其中每分钟任务只扫描已确认或已到期订单；无待执行订单时不请求行情、不发送消息。

## 常用命令

```bash
# 报告
python cli.py daily --mode morning
python cli.py daily --mode closing
python cli.py daily --mode morning --dry-run --no-notify --force

# 安全模拟交易
python cli.py paper open
python cli.py paper execute-due
python cli.py paper intraday
python cli.py paper account
python cli.py paper positions
python cli.py paper orders --status final_notified

# 环境与通知
python cli.py doctor
python cli.py notify-test
```

旧的 `paper execute`、旧AutoTrader和 `/api/paper/execute` 已停用，不能绕过飞书窗口和重新取价。

## 核心目录

```text
src/paper_trading/trading_service.py  唯一撮合入口和交易规则
src/paper_trading/workflow.py         09:35/到期执行/盘中监控
src/storage/trading_ledger.py         SQLite事务账本
src/notifier/feishu.py                报告与交互卡片
src/strategy/registry.py              Champion/Challenger晋级门禁
config/strategy.yaml                  ¥4,000仓位和风险参数
config/cost_model.yaml                费用与滑点
deploy/stock-intelligence.cron        调度模板
tests/                                单元、API和联网集成测试
```

## 正式启用前检查

1. `python -m pip check` 无错误。
2. 离线测试和 `-m integration` 全部通过。
3. `.env` 中的飞书双向参数和 `API_KEY` 已配置，密钥未提交。
4. 飞书三个按钮、五分钟超时、重复执行、T+1、跳价和回调失败测试通过。
5. 先运行至少20个交易日shadow mode，再开启长期模拟交易。

