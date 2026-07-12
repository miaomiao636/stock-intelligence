# -*- coding: utf-8 -*-
"""Stock Intelligence 数据模型"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Horizon(str, Enum):
    """推荐周期"""
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class ActionType(str, Enum):
    """推荐操作类型"""
    WATCH = "watch"
    TRACK = "track"
    SETUP_READY = "setup_ready"
    HOLD = "hold"
    REDUCE = "reduce"
    AVOID = "avoid"


class RecommendationStatus(str, Enum):
    """推荐状态"""
    ACTIVE = "active"
    HIT = "hit"
    STOPPED = "stopped"
    EXPIRED = "expired"
    EXTENDED = "extended"
    CLOSED = "closed"
    DONE = "done"


class RecommendationType(str, Enum):
    """推荐时间类型"""
    MORNING = "morning"
    CLOSING = "closing"


class SourceTier(str, Enum):
    """新闻来源等级"""
    OFFICIAL = "official"
    MAJOR_MEDIA = "major_media"
    BROKERAGE = "brokerage"
    FINANCIAL = "financial"
    SOCIAL = "social"
    RUMOR = "rumor"


class MarketRegime(str, Enum):
    """市场状态"""
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    HIGH_VOLATILITY = "high_volatility"


class NewsSource(BaseModel):
    """新闻来源"""
    dedup_key: str
    title: str
    source: str
    url: str
    published_at: datetime
    fetched_at: datetime
    summary: str
    impact_direction: str
    affected_sectors: List[str] = Field(default_factory=list)
    confidence: str
    category: str
    source_tier: SourceTier
    is_duplicate: bool = False
    canonical_source_url: Optional[str] = None
    related_source_urls: List[str] = Field(default_factory=list)


class EntryExitTiming(BaseModel):
    """进场/离场时机"""
    entry_condition: str
    entry_price_range: Optional[str] = None
    entry_rule: str = "next_open"
    entry_price: Optional[float] = None
    entry_triggered: bool = False
    entry_triggered_at: Optional[datetime] = None
    risk_exit_condition: str
    target_observation_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    stop_loss_price: Optional[float] = None
    trigger_event: Optional[str] = None


class SectorRecommendation(BaseModel):
    """板块推荐"""
    recommendation_id: str
    sector_name: str
    sector_index_code: Optional[str] = None
    rating: int
    reason: str
    horizon: Horizon
    horizon_days: int
    target_return_pct: float
    benchmark_code: str = "000300"
    entry_date: datetime
    expiry_date: datetime
    risk_level: str
    incremental_basis: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    status: RecommendationStatus = RecommendationStatus.ACTIVE
    hit_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_reason: Optional[str] = None
    status_updated_at: Optional[datetime] = None


class StockRecommendation(BaseModel):
    """个股推荐"""
    recommendation_id: str
    parent_recommendation_id: Optional[str] = None
    code: str
    name: str
    sector: str
    action: ActionType
    horizon: Horizon
    horizon_days: int
    target_return_pct: Optional[float] = None
    target_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    stop_loss_price: Optional[float] = None
    reason: str
    timing: EntryExitTiming
    entry_date: datetime
    expiry_date: datetime
    status: RecommendationStatus = RecommendationStatus.ACTIVE
    board_type: str = "main_board"
    is_alternative: bool = False
    original_code: Optional[str] = None
    original_name: Optional[str] = None
    incremental_basis: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    hit_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_reason: Optional[str] = None
    status_updated_at: Optional[datetime] = None


class OvernightChange(BaseModel):
    """增量变化"""
    us_market: dict = Field(default_factory=dict)
    hk_market_adr: dict = Field(default_factory=dict)
    polymarket: dict = Field(default_factory=dict)
    futures: dict = Field(default_factory=dict)
    incremental_news: List[NewsSource] = Field(default_factory=list)


class GlobalMapping(BaseModel):
    """外围关联"""
    original_code: str
    original_name: str
    original_market: str
    original_change: float
    a_share_alternatives: List[dict] = Field(default_factory=list)


class EvaluationMetrics(BaseModel):
    """评估指标"""
    absolute_return: float
    relative_return: float
    excess_return: float
    net_return: float
    max_drawdown: float
    win_rate: float
    profit_loss_ratio: float
    hit_persistence: float
    miss_drawdown: float


class Recommendation(BaseModel):
    """完整推荐"""
    date: datetime
    type: RecommendationType
    run_id: str
    created_at: datetime
    published_at: Optional[datetime] = None
    data_as_of: datetime
    strategy_version: str
    market_regime: MarketRegime = MarketRegime.NEUTRAL
    market_snapshot_id: Optional[str] = None
    scanner_run_id: Optional[str] = None
    source_snapshot_ids: List[str] = Field(default_factory=list)
    incremental_changes: Optional[OvernightChange] = None
    yesterday_review: Optional[dict] = None
    sector_recommendations: List[SectorRecommendation] = Field(default_factory=list)
    stock_recommendations: List[StockRecommendation] = Field(default_factory=list)
    global_mappings: List[GlobalMapping] = Field(default_factory=list)
    portfolio_alerts: List[dict] = Field(default_factory=list)
    expiry_alerts: List[dict] = Field(default_factory=list)
    risk_warnings: List[str] = Field(default_factory=list)
    news_sources: List[NewsSource] = Field(default_factory=list)


class Evaluation(BaseModel):
    """评估结果"""
    date: datetime
    recommendation_date: datetime
    recommendation_type: RecommendationType
    a_share_summary: dict = Field(default_factory=dict)
    hk_share_summary: dict = Field(default_factory=dict)
    sector_results: List[dict] = Field(default_factory=list)
    stock_results: List[dict] = Field(default_factory=list)
    metrics: Optional[EvaluationMetrics] = None
    failure_analysis: List[dict] = Field(default_factory=list)
    benchmark_comparison: dict = Field(default_factory=dict)


class StrategyAdvisory(BaseModel):
    """策略调整建议"""
    advisory_id: int
    created_at: datetime
    adjustment_type: str
    target_name: str
    current_value: float
    suggested_value: float
    reason: str
    evidence: List[str] = Field(default_factory=list)
    status: str = "pending"
    accepted_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    applied_version: Optional[str] = None
    operator: Optional[str] = None


class FactorWeights(BaseModel):
    """因子权重"""
    version: str
    updated_at: datetime
    factor_weights: dict


class SectorAllocations(BaseModel):
    """板块配置"""
    version: str
    updated_at: datetime
    sector_allocations: dict


class Holding(BaseModel):
    """持仓"""
    code: str
    name: str
    cost_price: float
    quantity: int
    cost_amount: Optional[float] = None
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_amount: Optional[float] = None
    added_at: datetime
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    notes: Optional[str] = None


class Transaction(BaseModel):
    """交易记录"""
    transaction_id: str
    code: str
    name: str
    side: str
    price: float
    quantity: int
    amount: float
    fees: float
    traded_at: datetime
    notes: Optional[str] = None


class ApiResponse:
    """统一API响应包装类"""

    @staticmethod
    def ok(data=None, message=""):
        """成功响应"""
        from datetime import datetime
        return {
            "success": True,
            "data": data,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }

    @staticmethod
    def fail(error, status_code=400):
        """失败响应"""
        from datetime import datetime
        return {
            "success": False,
            "data": None,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
