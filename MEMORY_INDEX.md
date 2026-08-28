# Stock Intelligence 项目记忆索引

## 项目目标

构建一个面向 A 股的智能分析与自动模拟交易系统：每日结合行情、新闻与政策生成推荐，按风控规则自动模拟持仓和买卖，通过飞书推送，并用历史结果持续评估策略。

## 权威位置

- 本地正式仓库：`/Users/Admin/github-ai-tools/stock-intelligence`
- 私有资料：`/Users/Admin/github-ai-tools/stock-intelligence-private`
- GitHub：`https://github.com/miaomiao636/stock-intelligence`
- 云服务器代码：`/opt/stock-intelligence`
- 云端运行数据：`/opt/stock-intelligence/data`

## 当前代码基线

- 记录日期：2026-08-11
- 本地分支：`main`
- 迁移前最新提交：`2a2c2fa Fix closing report account performance semantics`
- 当前策略配置：初始策略资金 20,000 元；最多 5 只持仓；每天最多新开 2 只；单只最大 30%；最高股价 50 元。
- 自动交易模式：`auto`，确认等待时间为 0；成交后通过飞书通知。
- 报告时点：08:45 盘前、09:35 自动交易、13:15 午后、16:45 盘后。

以上是本地代码基线，不等同于云端实时状态。涉及云端资金、持仓、部署版本或运行结果时必须实时核验。

## 关键结论

- 云端 SQLite 账本是模拟交易唯一权威数据源。
- 本地与云端不得同时自动交易。
- 同一飞书应用只保留一个权威长连接消费者。
- 网页、飞书和评估报告必须从同一账本与同一估值口径读取。
- 收益率必须剔除用户增减资金影响，不能把入金误计为投资收益。

## 记忆文件

- `PROJECT_CONTEXT.md`：架构、技术栈、目录、启动和部署方式。
- `PROGRESS.md`：已完成阶段、近期修复和当前风险。
- `DECISIONS.md`：长期有效的核心技术与业务决策。
- `NEXT_TASKS.md`：下一步待办及验收标准。

## 开始下一次任务

先读取本文件和 `NEXT_TASKS.md`；只有任务涉及相应主题时再读取其他记忆文件。不要默认读取私有归档、数据库或整段历史日志。
