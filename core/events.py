import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type
import structlog

logger = structlog.get_logger()


@dataclass
class TickEvent:
    timestamp: datetime
    source_module: str
    symbol: str
    exchange: str
    last_price: float
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    volume: int
    oi: int
    order_book: Optional[Dict] = None


@dataclass
class OrderEvent:
    timestamp: datetime
    source_module: str
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    order_type: str
    qty: int
    limit_price: Optional[float]
    status: str


@dataclass
class FillEvent:
    timestamp: datetime
    source_module: str
    fill_id: str
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    qty: int
    fill_price: float
    expected_price: float
    slippage_bps: float
    commission_inr: float


@dataclass
class RiskEvent:
    timestamp: datetime
    source_module: str
    event_type: str
    severity: str
    message: str
    details: Dict = field(default_factory=dict)


@dataclass
class SignalEvent:
    timestamp: datetime
    source_module: str
    strategy_id: str
    symbol: str
    side: str
    qty: int
    urgency: float
    metadata: Dict = field(default_factory=dict)


class EventBus:
    _instance: Optional["EventBus"] = None

    def __init__(self) -> None:
        self._handlers: Dict[Type, List[Callable]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def subscribe(self, event_type: Type, handler: Callable) -> None:
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def publish(self, event: Any) -> None:
        await self._queue.put(event)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop())
        logger.info("event_bus_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("event_bus_stopped")

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                handlers = self._handlers.get(type(event), [])
                for handler in handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            await handler(event)
                        else:
                            handler(event)
                    except Exception as e:
                        logger.error("event_handler_error", handler=str(handler), error=str(e), event_type=type(event).__name__)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("event_dispatch_error", error=str(e))
