import asyncio
import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import structlog

logger = structlog.get_logger()

FLUSH_INTERVAL = 5
FLUSH_BATCH_SIZE = 1000
MAX_RETRIES = 3


class ClickHouseWriter:
    def __init__(self, host: str, port: int, database: str, user: str, password: str) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password
        self._client = None
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._ticks_buffer: List[dict] = []
        self._order_book_buffer: List[dict] = []
        self._features_buffer: List[dict] = []
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._fallback_dir = Path("data/failed_writes")
        self._fallback_dir.mkdir(parents=True, exist_ok=True)

    def _get_client(self):
        from clickhouse_driver import Client
        return Client(
            host=self._host,
            port=self._port,
            database=self._database,
            user=self._user,
            password=self._password,
            connect_timeout=10,
            send_receive_timeout=30,
        )

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._client = await loop.run_in_executor(self._executor, self._get_client)
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("clickhouse_connected", host=self._host, database=self._database)

    async def write_tick(self, tick: dict) -> None:
        async with self._lock:
            self._ticks_buffer.append(tick)
        if len(self._ticks_buffer) >= FLUSH_BATCH_SIZE:
            await self._flush_ticks()

    async def write_order_book(self, ob: dict) -> None:
        async with self._lock:
            self._order_book_buffer.append(ob)

    async def write_feature_batch(self, features: List[dict]) -> None:
        async with self._lock:
            self._features_buffer.extend(features)

    async def write_fill(self, fill: dict) -> None:
        await self._execute_with_retry(
            "INSERT INTO trading.fills VALUES",
            [fill],
        )

    async def write_order(self, order: dict) -> None:
        await self._execute_with_retry(
            "INSERT INTO trading.orders VALUES",
            [order],
        )

    async def write_pnl_snapshot(self, snapshot: dict) -> None:
        await self._execute_with_retry(
            "INSERT INTO trading.pnl_snapshots VALUES",
            [snapshot],
        )

    async def query(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
        loop = asyncio.get_event_loop()

        def _query():
            client = self._get_client()
            rows, columns = client.execute(sql, params or {}, with_column_types=True)
            col_names = [c[0] for c in columns]
            return [dict(zip(col_names, row)) for row in rows]

        return await loop.run_in_executor(self._executor, _query)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            await self._flush_all()

    async def _flush_all(self) -> None:
        await self._flush_ticks()
        await self._flush_order_book()
        await self._flush_features()

    async def _flush_ticks(self) -> None:
        async with self._lock:
            if not self._ticks_buffer:
                return
            batch = self._ticks_buffer[:]
            self._ticks_buffer.clear()

        t0 = time.perf_counter()
        await self._execute_with_retry("INSERT INTO trading.ticks VALUES", batch)
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.debug("ticks_flushed", count=len(batch), latency_ms=round(latency_ms, 2))

    async def _flush_order_book(self) -> None:
        async with self._lock:
            if not self._order_book_buffer:
                return
            batch = self._order_book_buffer[:]
            self._order_book_buffer.clear()
        await self._execute_with_retry("INSERT INTO trading.order_book VALUES", batch)

    async def _flush_features(self) -> None:
        async with self._lock:
            if not self._features_buffer:
                return
            batch = self._features_buffer[:]
            self._features_buffer.clear()
        await self._execute_with_retry("INSERT INTO trading.features VALUES", batch)

    async def _execute_with_retry(self, query: str, data: List[dict]) -> None:
        if not data:
            return
        loop = asyncio.get_event_loop()

        for attempt in range(MAX_RETRIES):
            try:
                def _insert():
                    client = self._get_client()
                    client.execute(query, data)

                await loop.run_in_executor(self._executor, _insert)
                return
            except Exception as e:
                logger.warning("clickhouse_write_failed", attempt=attempt, error=str(e), query=query[:50])
                if attempt == MAX_RETRIES - 1:
                    self._write_fallback(query, data)
                else:
                    await asyncio.sleep(2 ** attempt)

    def _write_fallback(self, query: str, data: List[dict]) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self._fallback_dir / f"failed_writes_{ts}.jsonl"
        try:
            with open(path, "w") as f:
                for row in data:
                    f.write(json.dumps({"query": query, "row": row}, default=str) + "\n")
            logger.error("clickhouse_fallback_written", path=str(path), rows=len(data))
        except Exception as e:
            logger.error("clickhouse_fallback_failed", error=str(e))
