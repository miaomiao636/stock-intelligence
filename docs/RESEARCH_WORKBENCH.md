# 研究工作台：实现、边界与部署交接

更新日期：2026-09-26。这里记录本次代码能力，不承诺盈利，也不把本地开发等同于生产部署。

## 功能与使用

1. **研究助手**：默认事实模式，读取已存判断、经验、双边费用账本摘要与进场计划诊断，不调用模型、不联网补价。专家模式是按需的量化/事件/反方三个研究视角，同一快照，不是三个独立统计样本。每次最多3次模型调用、每次1000输出token/25秒、无SDK自动重试；共享SQLite控制并发1、每分钟4次、滚动24小时20次。同一request_id重试返回缓存或处理中，不能换问题重新消费。
2. **判断回放**：保留原始报告、来源、资料时间、当时假设、失效条件与模型/提示词/策略/数据版本。详情展示支持与反方证据。已保存判断不能被复盘覆盖。历史导入时间不会被伪造成过去的known_at。
3. **经验与实验**：盘后评估与历史评估只追加复核。未触发、来源不足、历史不完整、损益未闭合可生成候选经验；候选、已审核、否决、退役是人工管理状态，不联动交易参数。实验对显式PIT数据做RankIC、同池等权/因子净收益比较及时间切分；缺数据返回blocked。
4. **执行诊断**：09:35未到价的计划落入同一SQLite。现有盘中任务重新检查当天原计划、来源、行情、市场状态和全部风控；收盘到期，不跨日追单。下午报告只能撤销或复核上午授权，不新增买入授权。

聊天输入不进入交易工具；LLM不能选择任意SQL、URL或命令。模型意见不会修改资金、持仓或风控。研究POST仍须操作密钥，公网HTTP前端在询问密钥前阻止提交；HTTPS或本机安全访问是启用问答的前提。公开GET仍可阅读研究页面。不要关闭鉴权或TLS验证来解决此限制。

## 数据与评估口径

- 交易现金/旧成交行不重写。FIFO读全量成交，把买卖双边费用计入已实现盈亏；价内滑点不重复扣除。部分成交分摊成本，缺买入历史返回unknown/null并排除胜率。
- 持有期按交易日，不把周末计入。质量门不能调高研究目标价以人为满足盈亏比。
- 新闻published_at来自来源，不再使用抓取时间代替。未知、过旧、未来时间不进入“今日事件”模型摘要；仍可保留原始资料供审计。
- data_as_of为资料快照时间，known_at为系统真正记录时间；它们不是实时成交价。按历史as_of检索会排除后来导入的资料与后来形成的经验。
- 原收盘评估只有预计退出成本后的收益百分比时，不捏造已实现金额；缺少到期证据的多日预测仍为pending，不能用当日涨跌宣告成功。
- 经验是待验证假设，不是自动学习得到的可靠交易规律。对话模型回答保留在受控请求缓存；不能把它当作一条已经验证的交易预测。

## 研究命令（只作用于目标机器的当前项目数据）

```bash
.venv/bin/python cli.py research status
.venv/bin/python cli.py research import-history
.venv/bin/python cli.py research review-history
.venv/bin/python cli.py research experiment /absolute/path/to/audited-experiment.json
.venv/bin/python cli.py research add-lesson /absolute/path/to/lesson.json
.venv/bin/python cli.py research review-lesson LESSON_ID --status validated --reason '人工审核依据' --confirm
```

import-history读取已有盘前/盘中JSON；review-history读取已有收盘JSON。均不请求行情、不执行交易、不发送飞书、不重置账户；重复导入保持幂等。格式错误返回失败，不猜测修补历史。

经验JSON至少含title/body，可附judgment_ids、反例和适用范围。没有独立验证不得仅凭模型自述批准。

实验JSON包含name、protocol_version、as_of、top_k和observations；每条至少包含stock_code、as_of、available_at、entry_at、exit_at、label_known_at、can_execute、cost_bps、benchmark_return、factor_value、forward_return、data_version、strategy_version。收益使用小数比率（0.01=1%）；cost_bps是完整往返成本；未来特征/标签、重复记录、重叠持有区间和缺失字段均被拒绝。不提供虚构的演示盈利样本。

## API

| Method / Path | 用途 |
|---|---|
| GET /api/research/overview | 精确记录数量、资料截止、执行计划摘要、限制 |
| GET /api/research/judgments?code=600000&limit=30 | 已存判断，6位可选代码，limit 1–100 |
| GET /api/research/judgments/{id} | 原判断、解析的证据和追加复核 |
| GET /api/research/lessons | 经验卡与审核状态 |
| GET /api/research/experiments | 诊断结果或明确的数据阻塞 |
| GET /api/research/entry-plans?trade_date=YYYY-MM-DD | 每候选未成交原因，不修改订单 |
| POST /api/research/chat | question(1–1200字)、stock_code可选、mode facts/experts、request_id；需X-API-Key |

写接口缺配置503/缺密钥401；参数错误422/400；共享研究额度受限429。研究响应不使用旧ApiResponse外层包装，错误仍沿用既有error字段。

## 开源借鉴与未完成事项

本轮独立实现，未复制第三方代码，也未把大型框架装进生产自动交易进程。

- [Qlib](https://github.com/microsoft/qlib)：借鉴时间切分与版本化实验组织；没有宣称已训练Alpha158/LightGBM或已获样本外超额收益。
- [Alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded)：借鉴横截面RankIC、同样本因子诊断；不把相关性当盈利证明。
- [TradingAgents](https://github.com/TauricResearch/TradingAgents)：借鉴分析/反方分工；未复制其执行链、外部数据工具或角色人设。
- [QuantStats](https://github.com/ranaroussi/quantstats)：在有完整、现金流调整后的交易日净值后再评估接入。当前稀疏快照不能补0收益后冒充完整日收益序列。

仍缺：经审计的长期PIT财务披露与退市样本、分钟路径、完整历史股票池及可成交条件、真实容量与整手回测、独立样本外/前向影子策略结果、ETF专用基础数据。当前实验是确定性研究诊断，不是完整账户回测，不自动晋升。

## 本地验证（2026-09-26）

- 默认pytest：275 passed、3 deselected；6条既有依赖弃用警告。未运行真实外部源integration用例。
- compileall、research.js语法检查、git diff --check、wheel构建通过。
- 真实FastAPI＋独立临时SQLite＋Chromium联调：六个研究请求全200，entry-plans 200，未授权chat 401，事实查询model_calls=0、无pageerror。旧页面非研究请求被隔离mock，不是生产账户验收。
- 三轮隔离浏览器测试覆盖320/768/1024/1440布局、错误与空状态、HTML/危险URL安全渲染、HTTPS门禁、密钥不落存储。
- 真实公开历史报告导入临时库：6判断全部保留历史不完整标记、重复无新增、历史as-of无未来泄漏、SQLite完整性通过。
- 已读取的38笔公开成交快照经新FIFO核算：19笔卖出、无缺失买入、净已实现-344.26、胜率42.11%。此为指定快照复核，不是新增交易或收益预测。
- 未验证：真实付费模型效果、服务器部署、生产Feishu/定时任务、新交易时段闭环、样本外盈利能力。

## 部署与回滚边界

用户指定通过官方Computer Use操作已经打开的Edge服务器。本任务尚未加载该桌面控制工具，因此**目前尚未部署**。不得使用Computer History代替控制，或声称本地页面已经上线。

恢复可用的官方工具后，按以下顺序执行，不要求用户重新搭建环境：

1. 核验服务器工作目录 `/opt/stock-intelligence`、Git当前SHA/本地修改、两项systemd服务及定时任务；确认无运行中的分析/交易任务。在非交易时段更新。
2. 在 `/opt/stock-intelligence-backups/` 创建本次专属700权限目录，备份 `.env`、代码、报告JSON和cron。SQLite使用在线backup API获得一致副本（不能只复制主.db而遗漏WAL）；检查备份integrity_check=ok，记录账户现金与成交/订单数量。凭据不回显。
3. 若服务器可访问GitHub，使用明确SHA的fast-forward更新；如网络不通，上传仅含源码/测试/配置模板的更新包。绝不覆盖 `.env`、data或用本地账本替换线上账本，不执行git reset --hard。
4. 使用现有venv跑默认pytest与compileall。备份后执行research import-history、review-history；异常报告保留并记录，不初始化账户、不触发daily/paper命令验证。
5. 用现有部署脚本更新并重启已有Web/飞书服务（不可启动第二消费者）；核验active、研究API/主页、SQLite完整性、账本现金及成交数量未因部署改变、cron保持原调度。
6. 通过真实网站检查三个新页面；带密钥的问答需要HTTPS。核验事实模式零模型调用，再经明确操作核验一次有费用的专家请求。下一交易时段再核验待触发→重查→风控→成交的完整业务路径，不靠强制交易证明功能。

如失败，恢复备份代码并重启原服务。研究表为同库增量表，旧代码可忽略；不因代码回滚覆盖后来产生的交易账本。若确需恢复数据，先停止相关写入并核对自备份以来的新交易，单独确认恢复范围。
