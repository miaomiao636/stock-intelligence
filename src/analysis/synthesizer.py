# -*- coding: utf-8 -*-
"""综合研判模块"""

import json
import hashlib
import os
import time
from typing import Dict, List, Optional

from src.analysis.llm_client import LLMClient
from src.analysis.prompts import AFTERNOON_ANALYSIS_PROMPT, MORNING_ANALYSIS_PROMPT, FALLBACK_TEMPLATE
from src.analysis.validators import validate_llm_output, apply_action_rules


class Synthesizer:
    """综合研判器"""

    def __init__(self):
        self.llm_client = LLMClient()
        self.max_retries = max(1, min(3, int(os.getenv("LLM_ANALYSIS_RETRIES", "2"))))
        self.format_retries = max(0, min(2, int(os.getenv("LLM_FORMAT_RETRIES", "1"))))
        self.repair_timeout = max(10, min(90, int(os.getenv("LLM_REPAIR_TIMEOUT", "45"))))
    
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
            result["diagnostics"] = [{
                "phase": "availability",
                "attempt": 0,
                "status": "failed",
                "error": "LLM客户端未初始化，请检查LLM_API_KEY配置",
            }]

        # 添加数据质量信息到结果
        result["data_quality"] = data_quality
        result["adjusted_weights"] = adjusted_weights

        return result

    def analyze_afternoon(
        self,
        market_data: Dict,
        morning_report: Dict,
        news_list: List[Dict],
        factor_weights: Dict,
        sector_allocations: Dict,
        custom_params: Dict = None,
    ) -> Dict:
        """复核上午观点；失败时由调用方生成不可交易的确定性降级报告。"""
        data_quality = self._check_data_availability(market_data, news_list)
        adjusted_weights = self._adjust_weights_for_data_quality(factor_weights, data_quality)
        strategy_desc = self._format_strategy_params(
            custom_params or {}, adjusted_weights, sector_allocations
        )
        prompt = AFTERNOON_ANALYSIS_PROMPT.format(
            market_data=json.dumps(market_data, ensure_ascii=False, indent=2),
            morning_report=json.dumps(morning_report, ensure_ascii=False, indent=2),
            news_summary=self._format_news_summary(news_list),
            strategy_params=strategy_desc,
        )
        if self.llm_client.is_available():
            result = self._try_llm_analysis(prompt, market_data)
        else:
            result = {
                "status": "fallback",
                "data": {},
                "source": "afternoon_deterministic_fallback",
                "diagnostics": [{
                    "phase": "availability",
                    "attempt": 0,
                    "status": "failed",
                    "error": "LLM客户端未初始化，请检查LLM_API_KEY配置",
                }],
            }
        result["data_quality"] = data_quality
        result["adjusted_weights"] = adjusted_weights
        return result
    
    def _try_llm_analysis(self, prompt: str, market_data: Dict = None) -> Dict:
        """分离网络重试与格式修复；失败时只返回不可交易观察数据。"""
        diagnostics = []

        for attempt in range(self.max_retries):
            try:
                raw_output = self.llm_client.chat(
                    messages=[
                        {"role": "system", "content": "你是专业A股分析师。只输出一个严格JSON对象，不要Markdown代码块或额外说明。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    json_mode=True,
                )
            except Exception as e:
                diagnostics.append(self._diagnostic(
                    "analysis_request", attempt + 1, str(e)
                ))
                print(f"  ⚠️  LLM调用失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            is_valid, data, error = validate_llm_output(raw_output)
            if is_valid:
                return {
                    "status": "success",
                    "data": apply_action_rules(data),
                    "source": "llm",
                    "attempt": attempt + 1,
                    "diagnostics": diagnostics,
                }

            diagnostics.append(self._diagnostic(
                "analysis_validation", attempt + 1, error, raw_output
            ))
            print(f"  ⚠️  LLM输出校验失败 (尝试 {attempt + 1}/{self.max_retries}): {error}")

            # 已有完整分析内容时，只让模型修复格式，不重复整段市场研判。
            repair_input = raw_output
            repair_error = error
            for repair_attempt in range(self.format_retries):
                try:
                    repaired_output = self.llm_client.chat(
                        messages=[
                            {
                                "role": "system",
                                "content": "你是JSON格式修复器。只修复语法和字段格式，保留原有分析含义；只输出严格JSON对象。",
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"校验错误：{repair_error}\n"
                                    "请修复下面内容，不要补充不存在的事实：\n"
                                    f"{repair_input}"
                                ),
                            },
                        ],
                        temperature=0,
                        timeout=self.repair_timeout,
                        json_mode=True,
                    )
                except Exception as e:
                    diagnostics.append(self._diagnostic(
                        "format_repair", attempt + 1, str(e),
                        repair_attempt=repair_attempt + 1,
                    ))
                    print(f"  ⚠️  LLM格式修复失败: {e}")
                    break

                repaired_valid, repaired_data, repair_error = validate_llm_output(repaired_output)
                diagnostics.append(self._diagnostic(
                    "format_repair",
                    attempt + 1,
                    "" if repaired_valid else repair_error,
                    repaired_output,
                    repair_attempt + 1,
                    status="success" if repaired_valid else "failed",
                ))
                if repaired_valid:
                    return {
                        "status": "success",
                        "data": apply_action_rules(repaired_data),
                        "source": "llm_repaired",
                        "attempt": attempt + 1,
                        "repair_attempt": repair_attempt + 1,
                        "diagnostics": diagnostics,
                    }
                repair_input = repaired_output

            if attempt < self.max_retries - 1:
                time.sleep(2 ** attempt)

        print("  ⚠️  LLM所有重试都失败，使用fallback模板")
        fallback = self._use_fallback_template(market_data)
        fallback["attempt"] = self.max_retries
        fallback["diagnostics"] = diagnostics
        return fallback

    @staticmethod
    def _diagnostic(
        phase: str,
        attempt: int,
        error: str,
        raw_output: str = None,
        repair_attempt: int = None,
        status: str = "failed",
    ) -> Dict:
        """记录可排障元数据，不持久化可能含敏感信息的原始响应。"""
        item = {
            "phase": phase,
            "attempt": attempt,
            "status": status,
            "error": str(error or "")[:500],
        }
        if repair_attempt is not None:
            item["repair_attempt"] = repair_attempt
        if raw_output is not None:
            encoded = str(raw_output).encode("utf-8", errors="replace")
            item["response_chars"] = len(str(raw_output))
            item["response_sha256"] = hashlib.sha256(encoded).hexdigest()
        return item
    
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
        data = apply_action_rules(json.loads(FALLBACK_TEMPLATE))
        for sector in data.get("sector_recommendations", []):
            sector.update({
                "rating": 0,
                "target_return_pct": 0.0,
                "risk_level": "unassessed",
                "reason": "LLM降级，当前仅展示观察板块，不构成板块推荐。",
                "degraded": True,
                "source": "static_fallback",
            })
        for stock in data.get("stock_recommendations", []):
            stock["action"] = "watch"
            stock["trade_eligible"] = False
            stock["degraded_reason"] = "fallback数据禁止自动交易"
            stock["entry_price"] = None
            stock["target_price"] = None
            stock["stop_loss_price"] = None
            stock["target_return_pct"] = None
            stock["stop_loss_pct"] = None

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
                "entry_price": None,
                "target_price": None,
                "stop_loss_price": None,
                "target_return_pct": None,
                "stop_loss_pct": None,
                "reason": f"LLM不可用，仅观察。当前{price}元，涨跌{change}%；fallback禁止自动交易。",
                "reason_news": "LLM不可用，无新闻分析",
                "reason_policy": "LLM不可用，无政策分析",
                "reason_technical": f"实时价格{price}元，涨跌幅{change}%",
                "reason_fund": "LLM不可用，无资金面分析",
                "timing": {
                    "entry_condition": "仅观察，不生成进场条件",
                    "entry_price_range": None,
                    "entry_rule": "observe_only",
                    "entry_price": None,
                    "risk_exit_condition": "LLM恢复后重新评估",
                    "target_observation_price": None,
                    "stop_loss_price": None,
                },
                "entry_date": None,
                "expiry_date": None,
                "incremental_basis": "实时行情fallback",
                "track_trigger": "",
                "degraded": True,
                "degraded_reason": "LLM降级，仅展示实时行情观察项",
                "source": "realtime_fallback",
            })

        if not stocks:
            return None

        # 构建板块推荐
        sectors = {}
        for s in stocks:
            sec = s.get("sector") or "其他"
            if sec not in sectors:
                sectors[sec] = {
                    "sector_name": sec,
                    "rating": 0,
                    "reason": "LLM降级，仅按观察股票所属行业聚合，不构成板块推荐。",
                    "horizon": "short",
                    "horizon_days": 0,
                    "target_return_pct": 0.0,
                    "risk_level": "unassessed",
                    "entry_date": None,
                    "expiry_date": None,
                    "incremental_basis": "实时行情观察名单聚合",
                    "degraded": True,
                    "source": "realtime_fallback",
                }

        return {
            "date": market_data.get("date", ""),
            "type": "morning",
            "strategy_version": "1.0-fallback",
            "market_regime": market_data.get("market_regime", "neutral"),
            "market_data": market_data,
            "sector_recommendations": list(sectors.values()),
            "stock_recommendations": stocks,
            "risk_warnings": [
                "⚠️ LLM不可用，以下仅为实时行情观察名单",
                "⚠️ 降级结果不提供进场价、目标价或止损价",
                "⚠️ 建议LLM恢复后重新评估",
            ],
            "news_sources": [],
        }
    
    def _format_news_summary(self, news_list: List[Dict]) -> str:
        """格式化新闻摘要"""
        news_list = [item for item in news_list if item.get("event_eligible") is True and "error" not in item]
        
        if not news_list:
            return "暂无新闻"
        
        lines = []
        for i, news in enumerate(news_list[:5], 1):
            title = news.get("title", "")
            source = news.get("source", "")
            summary = news.get("summary", "")[:100]
            lines.append(f"{i}. {title} ({source}; 发布: {news.get('published_at')}; 抓取: {news.get('fetched_at')})")
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
        if any(item.get("event_eligible") is True and "error" not in item for item in news_list):
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
