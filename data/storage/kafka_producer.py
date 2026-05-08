import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional
import msgpack
import structlog

logger = structlog.get_logger()


class TickProducer:
    def __init__(self, bootstrap_servers: str, topic_config: Dict[str, str]) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic_config = topic_config
        self._producer = None
        self._executor = ThreadPoolExecutor(max_workers=2)

    def _build_producer(self):
        from kafka import KafkaProducer
        return KafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: msgpack.packb(v, default=str, use_bin_type=True),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks=1,
            retries=3,
            linger_ms=5,
            batch_size=65536,
            compression_type="lz4",
        )

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._producer = await loop.run_in_executor(self._executor, self._build_producer)
        logger.info("kafka_producer_connected", servers=self._bootstrap_servers)

    async def publish_tick(self, tick_event) -> None:
        payload = {
            "timestamp": tick_event.timestamp.isoformat(),
            "symbol": tick_event.symbol,
            "exchange": tick_event.exchange,
            "last_price": tick_event.last_price,
            "bid": tick_event.bid,
            "ask": tick_event.ask,
            "bid_qty": tick_event.bid_qty,
            "ask_qty": tick_event.ask_qty,
            "volume": tick_event.volume,
            "oi": tick_event.oi,
        }
        topic = self._topic_config.get("raw_ticks", "raw-ticks")
        await self._send(topic, key=tick_event.symbol, value=payload)

    async def publish_order_book(self, ob_event: dict) -> None:
        topic = self._topic_config.get("order_book", "order-book")
        await self._send(topic, key=ob_event.get("symbol"), value=ob_event)

    async def publish_signal(self, signal_event) -> None:
        payload = {
            "timestamp": signal_event.timestamp.isoformat(),
            "strategy_id": signal_event.strategy_id,
            "symbol": signal_event.symbol,
            "side": signal_event.side,
            "qty": signal_event.qty,
            "urgency": signal_event.urgency,
            "metadata": signal_event.metadata,
        }
        topic = self._topic_config.get("signals", "signals")
        await self._send(topic, key=signal_event.symbol, value=payload)

    async def _send(self, topic: str, key: Optional[str], value: dict) -> None:
        if self._producer is None:
            logger.warning("kafka_producer_not_connected")
            return
        loop = asyncio.get_event_loop()

        def _do_send():
            self._producer.send(topic, key=key, value=value)

        await loop.run_in_executor(self._executor, _do_send)

    async def flush(self) -> None:
        if self._producer:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._producer.flush)

    async def close(self) -> None:
        if self._producer:
            await self.flush()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._producer.close)
        logger.info("kafka_producer_closed")
