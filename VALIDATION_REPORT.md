# Stock Intelligence v1.0 验证报告

> 验证日期：2026-07-12  
> 验证目录：`/Users/Admin/github-ai-tools/stock-intelligence`  
> 说明：已同步真实项目并完成真实目录回归验证。

## 结论

代码级改造、真实目录同步、SQLite初始化、Tushare全市场验证、定时任务替换和单LaunchAgent部署均已通过。当前仍不能打开 `PAPER_TRADING_ENABLED=true`，因为飞书双向应用参数和API Key尚未配置。

## 已完成改造

- SQLite唯一交易账本：账户、订单、用户决定、成交、持仓批次、权益和策略版本。
- 订单状态机、幂等键、SQLite事务和compare-and-set抢单。
- 08:45只分析；09:35最终计划；确认后或5分钟到期时重新取价。
- 飞书确认、否决、暂停今日交易三个按钮及独立Token鉴权。
- 普通A股T+1、100股、交易时段、行情时间戳、价格区间和状态校验。
- ¥4,000账户：最多2只、单笔至少¥1,000、单只最多30%、现金至少30%。
- 最低佣金、印花税、过户费、ETF差异和滑点进入净收益。
- 单笔风险1%、日亏损1.5%、最大回撤10%的开仓门禁。
- 盘中30分钟盯市、止损/目标/持有周期检测和飞书退出计划。
- Fallback全部降级为watch，不能产生订单。
- Tushare全市场point-in-time候选池；不可用时SAFE_MODE。
- 历史信号价格不可变，API仅附加`latest_price/latest_quote_time`。
- Champion/Challenger注册表和100笔/20日晋级门槛。
- 写API无`API_KEY`时关闭；旧Executor/AutoTrader直接成交入口停用。
- Dashboard关键外部文本转义及新闻URL协议限制。

## 验证结果

| 检查 | 结果 |
|---|---|
| Python compileall | 通过 |
| Dashboard内联JavaScript语法 | 通过 |
| API重复路由 | 0 |
| 离线单元/API/事务/并发测试 | 38 passed，3 deselected |
| 真实行情集成测试 | 2 passed |
| Tushare全市场联网测试 | 1 passed |
| API运行态 | health/account/positions/dashboard均通过 |
| 未授权写接口 | 正确返回401/503 |
| 旧直接成交接口 | 正确拒绝 |
| 定时任务 | 新crontab已安装，旧11:35 AutoTrader任务已移除 |
| 服务 | 唯一`com.stockintelligence.server`运行中，health返回v1.0.0 |
| `pip check` | 尚未通过：现有虚拟环境的pytest缺少`packaging`；安装请求需用户明确批准 |

## 启用模拟交易前必须执行

1. 明确批准安装缺失的`packaging`，再运行 `python -m pip check`。
2. 配置飞书自建应用、接收ID、Verification Token及回调地址，完成三个按钮实测。
3. 配置`API_KEY`；在此之前所有写API保持关闭。
4. 保持 `PAPER_TRADING_ENABLED=false`，先运行至少20个交易日shadow mode。

## 未放行事项

- 未连接真实券商，也不应连接。
- 未验证真实飞书按钮回调和外网回调可达性。
- 未获得100笔已平仓样本，策略自动晋级保持关闭。
