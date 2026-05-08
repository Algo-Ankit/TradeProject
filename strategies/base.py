from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional
import pytz
import structlog


class StrategyState(Enum):
    ACTIVE = "active"
    HALTED = "halted"
    PAUSED = "paused"
    CLOSING = "closing"


@dataclass
class Signal:
    strategy_id: str
    symbol: str
    side: str
    qty: int
    urgency: float
    expected_price: float
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    metadata: Dict = field(default_factory=dict)


class BaseStrategy(ABC):
    def __init__(self, strategy_id: str, config: dict, redis_manager, event_bus) -> None:
        self.strategy_id = strategy_id
        self.config = config
        self._state = StrategyState.ACTIVE
        self._redis = redis_manager
        self._event_bus = event_bus
        self.logger = structlog.get_logger(strategy_id=strategy_id)
        self._tz = pytz.timezone("Asia/Kolkata")

    @abstractmethod
    async def on_tick(self, tick_event) -> Optional[Signal]: ...

    @abstractmethod
    async def on_fill(self, fill_event) -> None: ...

    @abstractmethod
    async def generate_signal(self) -> Optional[Signal]: ...

    async def get_positions(self) -> Dict:
        return await self._redis.get_all_positions(self.strategy_id)

    async def get_pnl(self) -> Dict:
        return await self._redis.get_pnl(self.strategy_id) or {}

    def halt(self, reason: str) -> None:
        self._state = StrategyState.HALTED
        self.logger.warning("strategy_halted", reason=reason)

    def resume(self) -> None:
        self._state = StrategyState.ACTIVE
        self.logger.info("strategy_resumed")

    @property
    def is_active(self) -> bool:
        return self._state == StrategyState.ACTIVE

    @property
    def capital_allocated(self) -> float:
        return float(self.config.get("max_position_inr", 0))

    async def _emit_signal(self, signal: Signal) -> None:
        from core.events import SignalEvent
        event = SignalEvent(
            timestamp=datetime.now(self._tz),
            source_module=self.strategy_id,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            side=signal.side,
            qty=signal.qty,
            urgency=signal.urgency,
            metadata=signal.metadata,
        )
        await self._event_bus.publish(event)
