import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
import pytz
import structlog

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")

MAX_RETRIES = 5
HEARTBEAT_TIMEOUT_SECONDS = 30
HEARTBEAT_CHECK_INTERVAL = 10


class BaseFeed(ABC):
    def __init__(self, event_bus, validator, paper_mode: bool = False) -> None:
        self._event_bus = event_bus
        self._validator = validator
        self._paper_mode = paper_mode
        self._is_connected = False
        self._last_tick_time: Optional[datetime] = None
        self._subscribed_symbols: List[str] = []
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.logger = structlog.get_logger(feed=self.feed_name)

    @property
    @abstractmethod
    def feed_name(self) -> str: ...

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def last_tick_time(self) -> Optional[datetime]:
        return self._last_tick_time

    @abstractmethod
    async def _connect_impl(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, symbols: List[str]) -> None: ...

    @abstractmethod
    async def on_tick(self, tick: dict) -> None: ...

    async def connect(self) -> None:
        if self._paper_mode:
            self.logger.info("paper_mode_active_skipping_connect")
            self._is_connected = True
            self._start_heartbeat()
            return

        for attempt in range(MAX_RETRIES):
            try:
                await self._connect_impl()
                self._is_connected = True
                self._start_heartbeat()
                self.logger.info("feed_connected", attempt=attempt)
                return
            except Exception as e:
                wait = 2 ** attempt
                self.logger.warning("connect_failed", attempt=attempt, error=str(e), retry_in=wait)
                await asyncio.sleep(wait)

        self.logger.error("connect_failed_max_retries", feed=self.feed_name)

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

    async def _heartbeat_monitor(self) -> None:
        while self._is_connected:
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL)
            if self._last_tick_time is None:
                continue
            now = datetime.now(IST)
            last = self._last_tick_time
            if (now - last).total_seconds() > HEARTBEAT_TIMEOUT_SECONDS:
                self.logger.warning("heartbeat_timeout_reconnecting", seconds_since_last_tick=(now - last).total_seconds())
                self._is_connected = False
                await self.connect()

    async def _emit_tick(self, raw_tick: dict) -> None:
        from core.events import TickEvent
        is_valid, reason = self._validator.validate(raw_tick)
        if not is_valid:
            self.logger.debug("tick_dropped", reason=reason, symbol=raw_tick.get("symbol"))
            return

        self._last_tick_time = datetime.now(IST)

        event = TickEvent(
            timestamp=raw_tick.get("timestamp", self._last_tick_time),
            source_module=self.feed_name,
            symbol=raw_tick["symbol"],
            exchange=raw_tick.get("exchange", "NSE"),
            last_price=float(raw_tick.get("last_price", 0)),
            bid=float(raw_tick.get("bid", 0)),
            ask=float(raw_tick.get("ask", 0)),
            bid_qty=int(raw_tick.get("bid_qty", 0)),
            ask_qty=int(raw_tick.get("ask_qty", 0)),
            volume=int(raw_tick.get("volume", 0)),
            oi=int(raw_tick.get("oi", 0)),
            order_book=raw_tick.get("order_book"),
        )
        await self._event_bus.publish(event)
