import asyncio
from datetime import datetime
from typing import Dict, List, Optional
import pytz
import structlog

from data.feeds.base_feed import BaseFeed

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class ShoonyaFeed(BaseFeed):
    def __init__(
        self,
        user: str,
        password: str,
        api_key: str,
        vc: str,
        imei: str,
        event_bus,
        validator,
        paper_mode: bool = False,
    ) -> None:
        super().__init__(event_bus=event_bus, validator=validator, paper_mode=paper_mode)
        self._user = user
        self._password = password
        self._api_key = api_key
        self._vc = vc
        self._imei = imei
        self._api = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._scrip_map: Dict[str, str] = {}

    @property
    def feed_name(self) -> str:
        return "shoonya_ws"

    async def _connect_impl(self) -> None:
        try:
            from NorenRestApiPy.NorenApi import NorenApi
        except ImportError:
            raise RuntimeError("NorenRestApiPy not installed: pip install NorenRestApiPy")

        self._loop = asyncio.get_event_loop()
        self._api = NorenApi(
            host="https://api.shoonya.com/NorenWClientTP/",
            websocket="wss://api.shoonya.com/NorenWSTP/",
        )
        ret = self._api.login(
            userid=self._user,
            password=self._password,
            twoFA="",
            vendor_code=self._vc,
            api_secret=self._api_key,
            imei=self._imei,
        )
        if ret and ret.get("stat") == "Ok":
            self._is_connected = True
            logger.info("shoonya_logged_in")
            self._api.start_websocket(
                subscribe_callback=self._on_tick_sync,
                order_update_callback=self._on_order_update_sync,
                socket_open_callback=self._on_open_sync,
                socket_close_callback=self._on_close_sync,
                socket_error_callback=self._on_error_sync,
            )
        else:
            raise RuntimeError(f"Shoonya login failed: {ret}")

    def _on_tick_sync(self, tick: dict) -> None:
        normalized = self._normalize_tick(tick)
        if normalized and self._loop:
            asyncio.run_coroutine_threadsafe(self._emit_tick(normalized), self._loop)

    def _on_order_update_sync(self, order: dict) -> None:
        logger.debug("shoonya_order_update", order=order)

    def _on_open_sync(self) -> None:
        logger.info("shoonya_ws_open")

    def _on_close_sync(self) -> None:
        self._is_connected = False
        logger.warning("shoonya_ws_closed")

    def _on_error_sync(self, error) -> None:
        logger.error("shoonya_ws_error", error=str(error))

    def _normalize_tick(self, raw: dict) -> Optional[dict]:
        symbol = raw.get("ts") or raw.get("tk")
        if not symbol:
            return None
        symbol = self._scrip_map.get(symbol, symbol)

        try:
            last_price = float(raw.get("lp", 0))
            bid = float(raw.get("bp1", last_price))
            ask = float(raw.get("sp1", last_price))
            bid_qty = int(float(raw.get("bq1", 0)))
            ask_qty = int(float(raw.get("sq1", 0)))
            volume = int(float(raw.get("v", 0)))
            oi = int(float(raw.get("oi", 0)))
        except (TypeError, ValueError):
            return None

        return {
            "timestamp": datetime.now(IST),
            "symbol": symbol,
            "exchange": raw.get("e", "NSE"),
            "last_price": last_price,
            "bid": bid,
            "ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "volume": volume,
            "oi": oi,
        }

    async def subscribe(self, symbols: List[str]) -> None:
        self._subscribed_symbols = symbols
        if self._paper_mode:
            logger.info("shoonya_paper_mode_subscribed", symbols=symbols)
            return
        if self._api and self._is_connected:
            for symbol in symbols:
                scrip = f"NSE|{symbol}-EQ"
                self._scrip_map[symbol] = symbol
                self._api.subscribe(scrip)

    async def on_tick(self, tick: dict) -> None:
        await self._emit_tick(tick)

    async def disconnect(self) -> None:
        self._is_connected = False
        if self._api:
            try:
                self._api.close_websocket()
            except Exception:
                pass
        logger.info("shoonya_disconnected")
