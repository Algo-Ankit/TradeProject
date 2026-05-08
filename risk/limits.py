from typing import Optional
from pydantic import BaseModel, Field


class PortfolioLimits(BaseModel):
    max_daily_drawdown_pct: float = Field(default=5.0, gt=0, le=100)
    max_gross_exposure_inr: float = Field(default=2_000_000.0, gt=0)
    max_net_exposure_pct: float = Field(default=30.0, gt=0, le=100)


class StatArbLimits(BaseModel):
    max_daily_drawdown_pct: float = 2.0
    max_position_inr: float = 500_000.0
    max_open_pairs: int = 8
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    stop_zscore: float = 3.5


class MLDirectionalLimits(BaseModel):
    max_daily_drawdown_pct: float = 1.5
    max_position_inr: float = 300_000.0
    min_model_confidence: float = 0.62


class OptionsRVLimits(BaseModel):
    max_daily_drawdown_pct: float = 1.0
    max_net_delta: float = 50.0
    max_net_gamma: float = 20.0


class PerStrategyLimits(BaseModel):
    stat_arb: StatArbLimits = Field(default_factory=StatArbLimits)
    ml_directional: MLDirectionalLimits = Field(default_factory=MLDirectionalLimits)
    options_rv: OptionsRVLimits = Field(default_factory=OptionsRVLimits)


class PreTradeLimits(BaseModel):
    max_single_order_inr: float = 100_000.0
    max_order_to_adv_pct: float = 1.0
    min_buying_power_buffer_pct: float = 20.0


class GreeksLimits(BaseModel):
    max_net_delta: float = 100.0
    max_net_gamma: float = 50.0
    max_net_vega: float = 500_000.0


class ExecutionConfig(BaseModel):
    passive_timeout_seconds: int = 30
    max_slippage_bps: float = 10.0


class RiskLimits(BaseModel):
    portfolio: PortfolioLimits = Field(default_factory=PortfolioLimits)
    per_strategy: PerStrategyLimits = Field(default_factory=PerStrategyLimits)
    pre_trade: PreTradeLimits = Field(default_factory=PreTradeLimits)
    greeks: GreeksLimits = Field(default_factory=GreeksLimits)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


class RiskDecision(BaseModel):
    approved: bool
    reason: str = ""
    details: dict = Field(default_factory=dict)
