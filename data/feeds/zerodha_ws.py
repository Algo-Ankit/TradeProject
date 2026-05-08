import asyncio
from datetime import datetime
from typing import Dict, List, Optional
import pytz
import structlog

from data.feeds.base_feed import BaseFeed

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class ZerodhaFeed(BaseFeed):
    def __init__(
        self,
        api_key: str,
        access_token: str,
        instruments_config: dict,
        event_bus,
        validator,
        paper_mode: bool = False,
        clickhouse_writer=None,
    ) -> None:
        super().__init__(event_bus=event_bus, validator=validator, paper_mode=paper_mode)
        self._api_key = api_key
        self._access_token = access_token
        self._instruments_config = instruments_config
        self._clickhouse = clickhouse_writer
        self._ticker = None
        self._token_to_symbol: Dict[int, str] = {}
        self._symbol_to_token: Dict[str, int] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._build_token_map()

    @property
    def feed_name(self) -> str:
        return "zerodha_ws"

    def _build_token_map(self) -> None:
        for inst in self._instruments_config.get("instruments", []):
            token = inst.get("zerodha_token")
            symbol = inst.get("symbol")
            if token and symbol:
                self._token_to_symbol[token] = symbol
                self._symbol_to_token[symbol] = token

    async def _connect_impl(self) -> None:
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            raise RuntimeError("kiteconnect not installed: pip install kiteconnect")

        self._loop = asyncio.get_event_loop()
        self._ticker = KiteTicker(self._api_key, self._access_token)
        self._ticker.on_ticks = self._on_ticks_sync
        self._ticker.on_connect = self._on_connect_sync
        self._ticker.on_disconnect = self._on_disconnect_sync
        self._ticker.on_error = self._on_error_sync

        import threading
        thread = threading.Thread(target=self._ticker.connect, kwargs={"threaded": True}, daemon=True)
        thread.start()
        await asyncio.sleep(3)

    def _on_ticks_sync(self, ws, ticks: list) -> None:
        for raw in ticks:
            normalized = self._normalize_tick(raw)
            if normalized and self._loop:
                asyncio.run_coroutine_threadsafe(self._emit_tick(normalized), self._loop)

    def _on_connect_sync(self, ws, response) -> None:
        logger.info("zerodha_ws_connected")
        if self._subscribed_symbols and self._ticker:
            tokens = [self._symbol_to_token[s] for s in self._subscribed_symbols if s in self._symbol_to_token]
            if tokens:
                self._ticker.subscribe(tokens)
                self._ticker.set_mode(self._ticker.MODE_FULL, tokens)

    def _on_disconnect_sync(self, ws, code, reason) -> None:
        self._is_connected = False
        logger.warning("zerodha_ws_disconnected", code=code, reason=reason)

    def _on_error_sync(self, ws, code, reason) -> None:
        logger.error("zerodha_ws_error", code=code, reason=reason)

    def _normalize_tick(self, raw: dict) -> Optional[dict]:
        token = raw.get("instrument_token")
        symbol = self._token_to_symbol.get(token)
        if not symbol:
            return None

        depth = raw.get("depth", {})
        buy_depth = depth.get("buy", [{}])
        sell_depth = depth.get("sell", [{}])

        order_book = None
        if buy_depth and sell_depth:
            order_book = {"buy": buy_depth[:5], "sell": sell_depth[:5]}

        return {
            "timestamp": raw.get("timestamp") or datetime.now(IST),
            "symbol": symbol,
            "exchange": "NSE",
            "last_price": raw.get("last_price", 0),
            "bid": buy_depth[0].get("price", 0) if buy_depth else 0,
            "ask": sell_depth[0].get("price", 0) if sell_depth else 0,
            "bid_qty": buy_depth[0].get("quantity", 0) if buy_depth else 0,
            "ask_qty": sell_depth[0].get("quantity", 0) if sell_depth else 0,
            "volume": raw.get("volume_traded", 0),
            "oi": raw.get("oi", 0),
            "order_book": order_book,
        }

    async def subscribe(self, symbols: List[str]) -> None:
        self._subscribed_symbols = symbols
        if self._paper_mode:
            logger.info("paper_mode_subscribed", symbols=symbols)
            return
        if self._ticker and self._is_connected:
            tokens = [self._symbol_to_token[s] for s in symbols if s in self._symbol_to_token]
            self._ticker.subscribe(tokens)
            self._ticker.set_mode(self._ticker.MODE_FULL, tokens)

    async def on_tick(self, tick: dict) -> None:
        await self._emit_tick(tick)

    async def disconnect(self) -> None:
        self._is_connected = False
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
        logger.info("zerodha_ws_disconnected_gracefully")

    async def replay_from_clickhouse(self, symbols: List[str], start: datetime, end: datetime, speed: float = 1.0) -> None:
        if not self._clickhouse:
            raise ValueError("clickhouse_writer required for paper mode replay")

        logger.info("starting_replay", symbols=symbols, start=start, end=end)
        sql = """
            SELECT timestamp, symbol, exchange, last_price, bid, ask, bid_qty, ask_qty, volume, oi
            FROM trading.ticks
            WHERE symbol IN ({syms}) AND timestamp >= '{s}' AND timestamp <= '{e}'
            ORDER BY timestamp
        """.format(
            syms=",".join(f"'{s}'" for s in symbols),
            s=start.strftime("%Y-%m-%d %H:%M:%S"),
            e=end.strftime("%Y-%m-%d %H:%M:%S"),
        )
        rows = await self._clickhouse.query(sql)
        prev_ts = None
        for row in rows:
            if prev_ts and speed > 0:
                gap = (row["timestamp"] - prev_ts).total_seconds() / speed
                if gap > 0:
                    await asyncio.sleep(min(gap, 0.1))
            prev_ts = row["timestamp"]
            tick = dict(row)
            tick["timestamp"] = tick["timestamp"].replace(tzinfo=IST) if tick["timestamp"].tzinfo is None else tick["timestamp"]
            await self._emit_tick(tick)
