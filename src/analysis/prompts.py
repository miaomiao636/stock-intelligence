# -*- coding: utf-8 -*-
"""Prompt模板模块"""

MORNING_ANALYSIS_PROMPT = """你是一个专业的A股分析师，擅长结合新闻、政策、技术面和资金面进行综合研判。请根据以下信息，生成今日盘前推荐报告。

## 市场数据（含实时行情）
{market_data}

**极其重要：市场数据中包含"realtime_stock_prices"字段，里面有各只股票的【真实实时价格】。你推荐的每只股票的current_price必须使用realtime_stock_prices中的真实价格！绝对不要使用你训练数据中的旧价格！进场价/目标价/止损价必须基于真实当前价计算！**

## 今日新闻与政策
{news_summary}

## 策略配置
- 因子权重：{factor_weights}
- 板块配置：{sector_allocations}

## 用户策略偏好（必须严格遵守！）
{strategy_params}

## 分析要求

### 板块分析
1. **推荐3-5个板块**（覆盖不同行业，避免集中），每个板块必须给出多维度分析
2. 板块分析必须包含：政策面、消息面、资金面、技术面四个维度
3. 为每个板块推荐对应的**低价ETF基金**（场内ETF代码51xxxx/15xxxx，价格通常1-3元）
4. 每个板块推荐必须包含ETF推荐字段：etf_recommendation（code和name）

### 个股分析
1. **推荐5-8只个股**，覆盖3个以上不同板块，按进场确定性分为：setup_ready（可进场）、track（跟踪观察）、watch（观望）
2. 其中setup_ready应占大多数（3-5只），track和watch各1-2只
3. 每只股票必须包含以下信息：
   - **当前参考价格**：必须使用realtime_stock_prices中的真实价格！
   - **进场日期与价格**：基于真实当前价，回调2-5%的合理进场价
   - **离场日期与价格**：基于真实当前价，上涨3-5%的目标价
   - **止损价**：基于真实当前价，下跌2-3%的止损价
   - **推荐理由分层**：必须分别从新闻/消息面、政策面、技术面、资金面四个角度阐述
   - **增量依据**：相比昨日/前期有什么新变化

4. **低价替代推荐（重要！）**：
   - 如果推荐的股票价格超过用户策略配置中的max_stock_price，必须在reason中注明替代方案
   - 替代方案包括：同板块ETF、同板块更低价个股
   - 格式："替代建议：可关注XXX板块ETF(代码XXXXXX)现价¥XX，或XXX股(代码XXXXXX)现价¥XX"
   - 对于资金量较小的用户，优先推荐价格在¥3-20之间的股票和ETF

5. **小资金策略**：
   - 当账户资金<¥10,000时，以短期操作为主（3-5天），追求快速获利
   - 优先推荐价格低、流动性好的股票和ETF
   - ETF是小资金分散投资的最佳选择（1手仅需100-300元）

6. 不同action的说明：
   - **setup_ready**：已满足进场条件，可直接操作。必须给出具体的进场价格区间和目标价
   - **track**：有潜力但需等待触发条件。必须明确什么条件下升级为setup_ready
   - **watch**：暂时观望，等待更明确信号

7. **action分配规则（极其重要！）**：
   - **前一交易日涨幅超过7%的股票，action只能是track或watch，绝对不能是setup_ready！**（追高风险大，需等待回调）
   - **当前价已高于昨日收盘价3%以上的股票，action只能是track**，并建议设置限价单在当前价97%位置等待回调
   - 当market_regime为bearish（熊市/看空）时，最多只能推荐2只setup_ready，其余必须为track或watch
   - 当market_regime为high_volatility（高波动）时，最多只能推荐3只setup_ready

8. **confidence字段（必须填写）**：
   - 每只股票必须包含confidence字段，值为1-5的整数
   - 5=非常有把握（多重信号共振、近期催化剂明确）
   - 4=有把握（基本面+技术面+资金面三重确认）
   - 3=一般（有逻辑支撑但存在不确定性）
   - 2=较弱（逻辑不完整或信号冲突）
   - 1=仅为观察（缺乏明确信号）
   - 只有confidence>=4的股票才建议自动买入

9. 价格要求（必须填写，不可为空）：
   - current_price：必须从realtime_stock_prices中查找真实价格！
   - entry_price：基于真实当前价，回调2-5%的合理进场价
   - target_price：基于真实当前价，上涨3-5%的目标价
   - stop_loss_price：基于真实当前价，下跌2-3%的止损价
   - 三者必须有合理的比例关系（风险收益比至少1:2）

10. 股票选择原则：
   - 优先选择流动性好（日成交额>1亿）的股票
   - 不要推荐ST股、停牌股、新股（上市<30天）
   - 价格范围20-500元为佳，兼顾大盘蓝筹和中小盘成长
   - 板块和个股要有多样性，避免同质化

11. **数据可用性约束（极其重要！）**：
   - **严格禁止凭空编造数据！** 只能使用市场数据中实际提供的信息
   - 如果市场数据中没有技术指标（MACD、KDJ、RSI等），禁止在reason_technical中提及这些指标
   - 如果市场数据中没有资金流数据（北向资金、融资余额等），reason_fund应写"数据暂不可用，无法分析资金面"
   - 如果没有新闻数据（news_summary为空或"暂无新闻"），禁止在reason_news中编造新闻
   - **只能基于以下数据源进行分析**：
     * realtime_stock_prices中的真实价格和涨跌幅
     * 市场数据中的指数数据
     * news_summary中的实际新闻（如果有）
     * 板块配置和因子权重
   - **违反此规则的推荐将被直接拒绝！**

## 重要规则
1. 必须输出严格的JSON格式
2. 所有价格字段必须填写，不可为null
3. 日期格式：ISO 8601（如 2026-07-08T09:30:00+08:00）
4. 所有列表字段必须出现，可为空数组
5. reason字段必须至少50字，包含多维度分析
6. 不要推荐ST股、停牌股

## 输出JSON格式
```json
{{
  "market_regime": "bullish/neutral/bearish/high_volatility",
  "market_summary": "今日市场总体判断，50字以内",
  "sector_recommendations": [
    {{
      "sector_name": "板块名称",
      "rating": 1-5,
      "reason": "推荐理由（至少30字，包含政策+消息+资金+技术四维度）",
      "horizon": "short/medium/long",
      "horizon_days": 3,
      "target_return_pct": 5.0,
      "risk_level": "low/medium/high",
      "entry_date": "2026-07-08T09:30:00+08:00",
      "expiry_date": "2026-07-11T15:00:00+08:00",
      "incremental_basis": "相比前期有什么新变化",
      "etf_recommendation": {{
        "code": "ETF代码（如515070）",
        "name": "ETF名称（如人工智能ETF）"
      }}
    }}
  ],
  "stock_recommendations": [
    {{
      "code": "股票代码",
      "name": "股票名称",
      "sector": "所属板块",
      "action": "setup_ready/track/watch",
      "confidence": 4,
      "horizon": "short/medium/long",
      "horizon_days": 3,
      "current_price": 100.0,
      "entry_price": 98.5,
      "target_price": 108.0,
      "stop_loss_price": 95.0,
      "target_return_pct": 3.0,
      "stop_loss_pct": -3.0,
      "reason": "综合推荐理由（至少50字）",
      "reason_news": "新闻/消息面分析：具体相关新闻及其影响",
      "reason_policy": "政策面分析：相关政策及其对个股的影响",
      "reason_technical": "技术面分析：K线形态、均线、量价关系等",
      "reason_fund": "资金面分析：主力资金流向、北向资金、融资融券等",
      "timing": {{
        "entry_condition": "具体进场条件描述",
        "entry_price_range": "98-100元区间",
        "entry_rule": "trigger_price",
        "entry_price": 98.5,
        "risk_exit_condition": "具体风险退出条件",
        "target_observation_price": 108.0,
        "stop_loss_price": 95.0
      }},
      "entry_date": "2026-07-08T09:30:00+08:00",
      "expiry_date": "2026-07-11T15:00:00+08:00",
      "incremental_basis": "相比前期有什么新变化促使此次推荐",
      "track_trigger": "仅track/watch类型需要：什么条件下升级为setup_ready"
    }}
  ],
  "risk_warnings": ["风险提示1（至少3条）"],
  "news_sources": [
    {{
      "title": "新闻标题",
      "source": "来源",
      "url": "链接",
      "published_at": "2026-07-08T08:00:00+08:00",
      "summary": "新闻摘要",
      "impact_direction": "positive/negative/neutral",
      "affected_sectors": ["板块1", "板块2"]
    }}
  ]
}}
```

请直接输出JSON，不要输出其他内容。"""


FALLBACK_TEMPLATE = """{
  "market_regime": "neutral",
  "market_summary": "LLM不可用，使用模板报告，数据仅供参考",
  "sector_recommendations": [
    {
      "sector_name": "默认板块",
      "rating": 2,
      "reason": "LLM不可用，使用模板报告。建议等待系统恢复后重新分析。",
      "horizon": "short",
      "horizon_days": 3,
      "target_return_pct": 2.0,
      "risk_level": "medium",
      "entry_date": null,
      "expiry_date": null,
      "incremental_basis": "无"
    }
  ],
  "stock_recommendations": [
    {
      "code": "000001",
      "name": "平安银行",
      "sector": "金融",
      "action": "watch",
      "confidence": 1,
      "horizon": "short",
      "horizon_days": 3,
      "current_price": 0,
      "entry_price": 0,
      "target_price": 0,
      "stop_loss_price": 0,
      "target_return_pct": 2.0,
      "stop_loss_pct": -3.0,
      "reason": "LLM不可用，使用模板推荐。此为默认推荐，不建议实际操作。",
      "reason_news": "暂无新闻分析",
      "reason_policy": "暂无政策分析",
      "reason_technical": "暂无技术分析",
      "reason_fund": "暂无资金面分析",
      "timing": {
        "entry_condition": "等待系统恢复",
        "entry_price_range": "-",
        "entry_rule": "trigger_price",
        "entry_price": 0,
        "risk_exit_condition": "止损-3%",
        "target_observation_price": 0,
        "stop_loss_price": 0
      },
      "entry_date": null,
      "expiry_date": null,
      "incremental_basis": "无",
      "track_trigger": ""
    }
  ],
  "risk_warnings": ["⚠️ 本报告为系统自动生成，未经AI深度分析", "⚠️ LLM不可用，数据仅供参考", "⚠️ 不建议基于此报告进行任何操作"],
  "news_sources": []
}"""
