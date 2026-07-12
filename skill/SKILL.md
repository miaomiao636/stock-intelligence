---
name: stock-intelligence
description: "智能选股分析系统。每日盘前/盘后推荐、个股分析、策略进化、持仓追踪。"
version: 1.0.0
tags: [stock, analysis, trading, strategy]
---

# Stock Intelligence - 智能选股分析系统

## 项目路径
~/github-ai-tools/stock-intelligence/

## 触发条件
当用户提到以下内容时使用：
- "今天的推荐" / "盘前推荐" / "盘后复盘"
- "帮我分析一下 XXX" / "XXX 怎么样"
- "推荐准确率" / "胜率" / "收益"
- "策略调整" / "进化建议"
- "持仓" / "我的股票"
- "到期提醒" / "有什么到期了"
- "回测" / "最近表现"
- "增量变化" / "隔夜变化" / "外围市场"
- "平替" / "科创板" / "创业板"

## 只读命令（可直接执行）

```bash
cd ~/github-ai-tools/stock-intelligence && python cli.py recommend
cd ~/github-ai-tools/stock-intelligence && python cli.py recommend --mode morning
cd ~/github-ai-tools/stock-intelligence && python cli.py recommend --mode closing
cd ~/github-ai-tools/stock-intelligence && python cli.py evaluate
cd ~/github-ai-tools/stock-intelligence && python cli.py strategy show
cd ~/github-ai-tools/stock-intelligence && python cli.py portfolio show
cd ~/github-ai-tools/stock-intelligence && python cli.py analyze 600519
cd ~/github-ai-tools/stock-intelligence && python cli.py overnight
```

## 需要确认的命令（执行前必须询问用户）

```bash
cd ~/github-ai-tools/stock-intelligence && python cli.py strategy accept <id>
cd ~/github-ai-tools/stock-intelligence && python cli.py strategy accept all
cd ~/github-ai-tools/stock-intelligence && python cli.py portfolio add <code> --cost <price> --quantity <qty>
cd ~/github-ai-tools/stock-intelligence && python cli.py portfolio remove <code>
cd ~/github-ai-tools/stock-intelligence && python cli.py recommend close REC-xxx --reason "..."
```

## 注意事项

1. 本系统不提供交易建议，仅提供研究参考
2. 所有推荐都带有周期标签（short/medium/long）
3. 交易在飞书内确认、否决或暂停；五分钟无操作也必须重新取价并通过风控
4. 评估收益分毛收益和净收益（扣除交易成本）
5. 禁止调用旧AutoTrader或旧paper execute直接成交
