# -*- coding: utf-8 -*-
"""综合研判模块"""

import json
from typing import Dict, List, Optional

from src.analysis.llm_client import LLMClient
from src.analysis.prompts import MORNING_ANALYSIS_PROMPT, FALLBACK_TEMPLATE
from src.analysis.validators import validate_llm_output, apply_action_rules


class Synthesizer:
    """综合研判器"""

    def __init__(self):
        self.llm_client = LLMClient()
        self.max_retries = 2  # 降为2次：每次最多120s，2次=240s+退避，避免总超时
    
    def analyze(
        self,
        market_data: Dict,
        news_list: List[Dict],
        factor_weights: Dict,
        sector_allocations: Dict,
        custom_params: Dict = None,
    ) -> Dict:
        """执行综合研判"""

        # 准备新闻摘要
        news_summary = self._format_news_summary(news_list)

        # 检查数据可用性
        data_quality = self._check_data_availability(market_data, news_list)

        # 根据数据可用性调整因子权重
        adjusted_weights = self._adjust_weights_for_data_quality(factor_weights, data_quality)

        # 构建策略配置描述
        params = custom_params or {}
        strategy_desc = self._format_strategy_params(params, adjusted_weights, sector_allocations)

        # 构建prompt
        prompt = MORNING_ANALYSIS_PROMPT.format(
            market_data=json.dumps(market_data, ensure_ascii=False, indent=2),
            news_summary=news_summary,
            factor_weights=json.dumps(adjusted_weights, ensure_ascii=False),
            sector_allocations=json.dumps(sector_allocations, ensure_ascii=False),
            strategy_params=strategy_desc,
        )

        # 尝试LLM
        if self.llm_client.is_available():
            result = self._try_llm_analysis(prompt, market_data)
        else:
            result = self._use_fallback_template(market_data)

        # 添加数据质量信息到结果
        result["data_quality"] = data_quality
        result["adjusted_weights"] = adjusted_weights

        return result
    
    def _try_llm_analysis(self, prompt: str, market_data: Dict = None) -> Dict:
        """尝试LLM分析（C5: 重试间加指数退避）"""
        import time

        for attempt in range(self.max_retries):
            try:
                # 调用LLM
                raw_output = self.llm_client.chat(
                    messages=[
                        {"role": "system", "content": "你是一个专业的股票分析师，只输出JSON格式的分析报告。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                )

                # 校验输出
                is_valid, data, error = validate_llm_output(raw_output)

                if is_valid:
                    # 应用action规则
                    data = apply_action_rules(data)
                    return {
                        "status": "success",
                        "data": data,
                        "source": "llm",
                        "attempt": attempt + 1,
                    }
                else:
                    print(f"  ⚠️  LLM输出校验失败 (尝试 {attempt + 1}/{self.max_retries}): {error}")
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)  # C5: 指数退避 1s, 2s...
                    continue

            except Exception as e:
                print(f"  ⚠️  LLM调用失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # C5: 指数退避
                continue

        # 所有重试都失败，使用fallback（传入market_data生成可交易推荐）
        print("  ⚠️  LLM所有重试都失败，使用fallback模板")
        return self._use_fallback_template(market_data)
    
    def _use_fallback_template(self, market_data: Dict = None) -> Dict:
        """使用fallback模板——优先用实时行情生成可交易推荐"""

        # 降级行情只能生成观察项，绝不生成可成交信号。
        if market_data:
            tradeable = self._generate_tradeable_fallback(market_data)
            if tradeable:
                return {
                    "status": "fallback",
                    "data": tradeable,
                    "source": "realtime_fallback",
                    "attempt": 0,
                }

        # 最终兜底：静态模板（不可交易，仅展示）
        data = json.loads(FALLBACK_TEMPLATE)
        data = apply_action_rules(data)
        for stock in data.get("stock_recommendations", []):
            stock["action"] = "watch"
            stock["trade_eligible"] = False
            stock["degraded_reason"] = "fallback数据禁止自动交易"

        return {
            "status": "fallback",
            "data": data,
            "source": "static_template",
            "attempt": 0,
        }

    def _generate_tradeable_fallback(self, market_data: Dict) -> Optional[Dict]:
        """从实时行情生成只读观察列表；fallback 永不具备交易资格。"""
        prices = market_data.get("realtime_stock_prices", [])
        if not prices:
            return None
        if isinstance(prices, dict):
            prices = list(prices.values())

        # 筛选适合小资金的股票（价格<20元，可买100股）
        account_equity = market_data.get("_account_equity", 4000)
        max_price = min(20.0, account_equity * 0.003)  # 不超过资金的0.3%/股

        candidates = []
        for p in prices:
            try:
                price = float(p.get("price", 0))
                if price <= 0 or price > max_price:
                    continue
                change = float(p.get("change_pct", 0))
                # 排除涨停(>9.5%)和跌停(<-9.5%)的股票
                if abs(change) > 9.5:
                    continue
                candidates.append(p)
            except (ValueError, TypeError):
                continue

        if not candidates:
            return None

        # 按涨跌幅排序，选跌幅较小的（相对稳健）
        candidates.sort(key=lambda x: float(x.get("change_pct", 0)), reverse=True)
        selected = candidates[:3]  # 选3只

        stocks = []
        for s in selected:
            code = s.get("code", "")
            name = s.get("name", code)
            price = float(s.get("price", 0))
            change = float(s.get("change_pct", 0))

            # 保守的止盈止损
            target_pct = 3.0
            stop_pct = -3.0
            entry_price = price
            target_price = round(price * (1 + target_pct / 100), 2)
            stop_price = round(price * (1 + stop_pct / 100), 2)

            stocks.append({
                "code": code,
                "name": name,
                "sector": s.get("sector", ""),
                "action": "watch",
                "trade_eligible": False,
                "confidence": 2,
                "horizon": "short",
                "horizon_days": 3,
                "current_price": price,
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_loss_price": stop_price,
                "target_return_pct": target_pct,
                "stop_loss_pct": stop_pct,
                "reason": f"LLM不可用，仅观察。当前{price}元，涨跌{change}%；fallback禁止自动交易。",
                "reason_news": "LLM不可用，无新闻分析",
                "reason_policy": "LLM不可用，无政策分析",
                "reason_technical": f"实时价格{price}元，涨跌幅{change}%",
                "reason_fund": "LLM不可用，无资金面分析",
                "timing": {
                    "entry_condition": "当前价格可买入",
                    "entry_price_range": f"{price * 0.995:.2f}-{price * 1.005:.2f}",
                    "entry_rule": "trigger_price",
                    "entry_price": entry_price,
                    "risk_exit_condition": f"止损{stop_pct}%",
                    "target_observation_price": target_price,
                    "stop_loss_price": stop_price,
                },
                "entry_date": None,
                "expiry_date": None,
                "incremental_basis": "实时行情fallback",
                "track_trigger": "",
            })

        if not stocks:
            return None

        # 构建板块推荐
        sectors = {}
        for s in stocks:
            sec = s.get("sector", "其他")
            if sec not in sectors:
                sectors[sec] = {"name": sec, "allocation": 0.3, "reason": "fallback推荐"}

        return {
            "date": market_data.get("date", ""),
            "type": "morning",
            "strategy_version": "1.0-fallback",
            "market_regime": market_data.get("market_regime", "neutral"),
            "market_data": market_data,
            "sector_recommendations": list(sectors.values()),
            "stock_recommendations": stocks,
            "risk_warnings": [
                "⚠️ LLM不可用，推荐基于实时行情自动生成",
                "⚠️ 采用保守止盈止损策略",
                "⚠️ 建议LLM恢复后重新评估",
            ],
            "news_sources": [],
        }
    
    def _format_news_summary(self, news_list: List[Dict]) -> str:
        """格式化新闻摘要"""
        
        if not news_list:
            return "暂无新闻"
        
        lines = []
        for i, news in enumerate(news_list[:5], 1):
            title = news.get("title", "")
            source = news.get("source", "")
            summary = news.get("summary", "")[:100]
            lines.append(f"{i}. {title} ({source})")
            if summary:
                lines.append(f"   摘要: {summary}")
        
        return "\n".join(lines)

    def _format_strategy_params(self, params: Dict, factor_weights: Dict, sector_allocations: Dict) -> str:
        """Format strategy parameters for LLM prompt"""
        lines = []

        # Account info (important for small capital strategy)
        cash = params.get("_account_cash", 0)
        equity = params.get("_account_equity", 0)
        if cash > 0:
            lines.append(f"- 账户资金：总资金¥{equity:,.0f}，可用现金¥{cash:,.0f}")
            if cash < 10000:
                lines.append(f"- ⚠️ 资金量较小！策略要求：")
                lines.append(f"  · 以短期操作为主（3-5天），追求快速获利积累资金")
                lines.append(f"  · 优先推荐低价股（¥3-20）和ETF（¥1-3），1手100股仅需100-300元")
                lines.append(f"  · 热门板块的龙头股如果价格过高，必须推荐同板块ETF或低价平替个股")
                lines.append(f"  · 例如：中际旭创¥1192太贵 → 推荐人工智能ETF(515070)¥1.38 或 中科曙光(603019)¥103")

        # Period preference
        short = params.get("short_ratio", 0.5)
        medium = params.get("medium_ratio", 0.35)
        long_r = params.get("long_ratio", 0.15)
        if short >= 0.7:
            lines.append(f"- 投资周期：以短期为主（短期{short*100:.0f}%），推荐短期操作的股票")
        elif medium >= 0.5:
            lines.append(f"- 投资周期：以中期为主（中期{medium*100:.0f}%）")
        else:
            lines.append(f"- 投资周期：短期{short*100:.0f}% 中期{medium*100:.0f}% 长期{long_r*100:.0f}%")

        # Market preference
        a_share = params.get("a_share_ratio", 0.8)
        if a_share >= 0.9:
            lines.append(f"- 市场偏好：专注A股（A股{a_share*100:.0f}%），不推荐港股/美股")
        else:
            lines.append(f"- 市场偏好：A股{a_share*100:.0f}%")

        # Price threshold
        max_price = params.get("max_stock_price", 500)
        if max_price and max_price > 0:
            lines.append(f"- 最高股价：不超过¥{max_price}，超过的只能标记为watch或track，并在reason中推荐ETF或低价平替")

        return "\n".join(lines) if lines else "无特殊策略配置"

    def _check_data_availability(self, market_data: Dict, news_list: List[Dict]) -> Dict:
        """检查数据可用性

        Returns:
            数据质量报告
        """
        available = []
        missing = []

        # 检查实时价格数据
        realtime_prices = market_data.get("realtime_stock_prices", {})
        if realtime_prices and len(realtime_prices) > 0:
            available.append("realtime_prices")
        else:
            missing.append("realtime_prices")

        # 检查新闻数据
        if news_list and len(news_list) > 0 and news_list[0].get("title") != "暂无新闻":
            available.append("news")
        else:
            missing.append("news")

        # 检查指数数据
        if "sse_index" in market_data or "szse_index" in market_data:
            available.append("market_indices")
        else:
            missing.append("market_indices")

        # 检查技术指标数据（我们目前没有）
        # 注意：当前系统不提供MACD、KDJ、RSI等技术指标
        missing.append("technical_indicators")

        # 检查资金流数据（我们目前没有）
        missing.append("capital_flow")

        # 确定整体数据质量级别
        if len(missing) == 0:
            quality_level = "full"
        elif "news" in missing:
            quality_level = "degraded"
        else:
            quality_level = "limited"

        return {
            "available": available,
            "missing": missing,
            "quality_level": quality_level,
            "realtime_prices_count": len(realtime_prices),
            "news_count": len(news_list) if news_list else 0,
            "warnings": self._generate_data_quality_warnings(missing),
        }

    def _generate_data_quality_warnings(self, missing: List[str]) -> List[str]:
        """生成数据质量警告"""
        warnings = []

        if "news" in missing:
            warnings.append("⚠️ 新闻数据不可用，news_sentiment因子权重已归零")
        if "technical_indicators" in missing:
            warnings.append("⚠️ 技术指标不可用，禁止在推荐理由中提及MACD、KDJ、RSI等指标")
        if "capital_flow" in missing:
            warnings.append("⚠️ 资金流数据不可用，禁止在推荐理由中提及北向资金、融资余额等")

        return warnings

    def _adjust_weights_for_data_quality(self, weights: Dict, data_quality: Dict) -> Dict:
        """根据数据可用性调整因子权重

        如果某个数据源不可用，对应的因子权重归零，其他因子权重重新归一化
        """
        adjusted = weights.copy()
        missing = data_quality.get("missing", [])

        # 数据源到因子的映射
        data_to_factor = {
            "news": "news_sentiment",
            "technical_indicators": "technical_pattern",
            "capital_flow": "capital_flow",
        }

        # 将缺失数据源的因子权重归零
        zeroed_weight = 0
        for data_source, factor in data_to_factor.items():
            if data_source in missing and factor in adjusted:
                zeroed_weight += adjusted[factor]
                adjusted[factor] = 0

        # 如果有因子被归零，重新归一化其他因子
        if zeroed_weight > 0:
            remaining_weight = sum(adjusted.values())
            if remaining_weight > 0:
                scale_factor = (remaining_weight + zeroed_weight) / remaining_weight
                for factor in adjusted:
                    if adjusted[factor] > 0:
                        adjusted[factor] = round(adjusted[factor] * scale_factor, 4)

        return adjusted
