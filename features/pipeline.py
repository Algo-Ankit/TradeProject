import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional
import polars as pl
import structlog

logger = structlog.get_logger()

LATENCY_ALERT_MS = 100.0


class FeaturePipeline:
    def __init__(
        self,
        redis_manager,
        clickhouse_writer,
        feature_registry,
        config: dict,
        event_bus,
    ) -> None:
        self._redis = redis_manager
        self._clickhouse = clickhouse_writer
        self._registry = feature_registry
        self._config = config
        self._event_bus = event_bus
        self._tick_buffers: Dict[str, pl.DataFrame] = {}
        self._features_batch: List[dict] = []
        self._buffer_size = config.get("tick_buffer_size", 10000)
        self._flush_interval = 60
        self._flush_task: Optional[asyncio.Task] = None
        self._min_history = 10

        self._tick_schema = {
            "timestamp": pl.Datetime("us", "Asia/Kolkata"),
            "symbol": pl.Utf8,
            "last_price": pl.Float64,
            "bid": pl.Float64,
            "ask": pl.Float64,
            "bid_qty": pl.Int64,
            "ask_qty": pl.Int64,
            "volume": pl.Int64,
            "oi": pl.Int64,
        }

    async def start(self) -> None:
        from core.events import TickEvent
        self._event_bus.subscribe(TickEvent, self.on_tick)
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("feature_pipeline_started")

    async def on_tick(self, tick_event) -> None:
        symbol = tick_event.symbol
        self._append_tick(symbol, tick_event)

        buf = self._tick_buffers.get(symbol)
        if buf is None or len(buf) < self._min_history:
            return

        t0 = time.perf_counter()
        features = self._registry.compute_all(symbol, buf)
        latency_ms = (time.perf_counter() - t0) * 1000

        if latency_ms > LATENCY_ALERT_MS:
            logger.warning("feature_compute_slow", symbol=symbol, latency_ms=round(latency_ms, 2))

        ts = tick_event.timestamp
        for feature_name, value in features.items():
            await self._redis.set_feature(symbol, feature_name, value)
            self._features_batch.append({
                "timestamp": ts,
                "symbol": symbol,
                "feature_name": feature_name,
                "value": float(value),
            })

    def _append_tick(self, symbol: str, tick_event) -> None:
        row = {
            "timestamp": tick_event.timestamp,
            "symbol": tick_event.symbol,
            "last_price": tick_event.last_price,
            "bid": tick_event.bid,
            "ask": tick_event.ask,
            "bid_qty": tick_event.bid_qty,
            "ask_qty": tick_event.ask_qty,
            "volume": tick_event.volume,
            "oi": tick_event.oi,
        }

        new_row = pl.DataFrame([row])

        if symbol not in self._tick_buffers:
            self._tick_buffers[symbol] = new_row
        else:
            combined = pl.concat([self._tick_buffers[symbol], new_row])
            if len(combined) > self._buffer_size:
                combined = combined.tail(self._buffer_size)
            self._tick_buffers[symbol] = combined

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            if self._features_batch:
                batch = self._features_batch[:]
                self._features_batch.clear()
                await self._clickhouse.write_feature_batch(batch)
                logger.debug("features_flushed_to_clickhouse", count=len(batch))

    def get_buffer(self, symbol: str) -> Optional[pl.DataFrame]:
        return self._tick_buffers.get(symbol)
