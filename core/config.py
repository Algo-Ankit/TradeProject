import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).parent.parent


class BrokerConfig(BaseModel):
    primary: str = "zerodha"
    secondary: str = "shoonya"


class DataConfig(BaseModel):
    tick_buffer_size: int = 10000
    clickhouse_flush_interval_seconds: int = 5
    clickhouse_flush_batch_size: int = 1000
    stale_tick_threshold_seconds: int = 5


class FeaturesConfig(BaseModel):
    computation_interval_seconds: int = 1
    vpin_bucket_count: int = 50
    regime_retrain_time: str = "09:00"


class PortfolioLimitsConfig(BaseModel):
    max_daily_drawdown_pct: float = 5.0
    max_gross_exposure_inr: float = 2_000_000.0
    max_net_exposure_pct: float = 30.0


class StatArbConfig(BaseModel):
    max_daily_drawdown_pct: float = 2.0
    max_position_inr: float = 500_000.0
    max_open_pairs: int = 8
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    stop_zscore: float = 3.5


class MLDirectionalConfig(BaseModel):
    max_daily_drawdown_pct: float = 1.5
    max_position_inr: float = 300_000.0
    min_model_confidence: float = 0.62


class OptionsRVConfig(BaseModel):
    max_daily_drawdown_pct: float = 1.0
    max_net_delta: float = 50.0
    max_net_gamma: float = 20.0


class PerStrategyConfig(BaseModel):
    stat_arb: StatArbConfig = Field(default_factory=StatArbConfig)
    ml_directional: MLDirectionalConfig = Field(default_factory=MLDirectionalConfig)
    options_rv: OptionsRVConfig = Field(default_factory=OptionsRVConfig)


class PreTradeConfig(BaseModel):
    max_single_order_inr: float = 100_000.0
    max_order_to_adv_pct: float = 1.0
    min_buying_power_buffer_pct: float = 20.0


class ExecutionLimitsConfig(BaseModel):
    passive_timeout_seconds: int = 30
    max_slippage_bps: float = 10.0


class RiskConfig(BaseModel):
    portfolio: PortfolioLimitsConfig = Field(default_factory=PortfolioLimitsConfig)
    per_strategy: PerStrategyConfig = Field(default_factory=PerStrategyConfig)
    pre_trade: PreTradeConfig = Field(default_factory=PreTradeConfig)
    execution: ExecutionLimitsConfig = Field(default_factory=ExecutionLimitsConfig)


class MonitoringConfig(BaseModel):
    pnl_update_interval_seconds: int = 5
    signal_heatmap_update_seconds: int = 30
    latency_alert_p99_ms: float = 500.0
    risk_alert_threshold_pct: float = 90.0


class SystemConfig(BaseModel):
    paper_mode: bool = True
    log_level: str = "INFO"
    timezone: str = "Asia/Kolkata"


class AppConfig(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)


class InstrumentEntry(BaseModel):
    symbol: str
    exchange: str
    type: str
    lot_size: int = 1
    tick_size: float = 0.05
    zerodha_token: Optional[int] = None


class InstrumentsConfig(BaseModel):
    holidays: list[str] = Field(default_factory=list)
    instruments: list[InstrumentEntry] = Field(default_factory=list)
    kafka: Dict[str, Any] = Field(default_factory=dict)
    clickhouse: Dict[str, Any] = Field(default_factory=dict)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    raw = _load_yaml(BASE_DIR / "config" / "system.yaml")
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_instruments_config() -> InstrumentsConfig:
    raw = _load_yaml(BASE_DIR / "config" / "instruments.yaml")
    return InstrumentsConfig.model_validate(raw)


def get_secret(key: str) -> str:
    value = os.environ.get(key)
    if value is None:
        raise ValueError(f"Required secret '{key}' not set in environment variables")
    return value


def get_secret_optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
