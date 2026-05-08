import asyncio
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Dict, List, Optional
import structlog

logger = structlog.get_logger()


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER PENDING"


ZERODHA_STATUS_MAP = {
    "OPEN": OrderStatus.OPEN,
    "COMPLETE": OrderStatus.COMPLETE,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "TRIGGER PENDING": OrderStatus.TRIGGER_PENDING,
}


class ZerodhaExecutionClient:
    def __init__(self, api_key: str, access_token: str, paper_mode: bool = True) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._paper_mode = paper_mode
        self._kite = None
        self._executor = ThreadPoolExecutor(max_workers=5)
        self._rate_limiter = asyncio.Semaphore(10)
        self._paper_fills: List[dict] = []
        self._paper_orders: Dict[str, dict] = {}
        logger.info("zerodha_client_initialized", paper_mode=paper_mode)

        if not paper_mode:
            self._init_kite()

    def _init_kite(self) -> None:
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(self._access_token)
        except ImportError:
            logger.error("kiteconnect_not_installed")
        except Exception as e:
            logger.error("kite_init_failed", error=str(e))

    async def place_order(
        self,
        symbol: str,
        exchange: str,
        side: str,
        order_type: str,
        qty: int,
        price: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> str:
        async with self._rate_limiter:
            asyncio.get_event_loop().create_task(self._release_semaphore())

        if self._paper_mode:
            return self._simulate_paper_order(symbol, exchange, side, order_type, qty, price, tag)

        loop = asyncio.get_event_loop()
        try:
            def _place():
                return self._kite.place_order(
                    tradingsymbol=symbol,
                    exchange=exchange,
                    transaction_type=side,
                    order_type=order_type,
                    quantity=qty,
                    product="MIS",
                    price=price or 0,
                    tag=tag or "",
                    variety=self._kite.VARIETY_REGULAR,
                )
            order_id = await loop.run_in_executor(self._executor, _place)
            logger.info("order_placed", symbol=symbol, side=side, qty=qty, order_id=order_id)
            return str(order_id)
        except Exception as e:
            logger.error("place_order_failed", symbol=symbol, side=side, qty=qty, error=str(e))
            raise

    async def _release_semaphore(self) -> None:
        await asyncio.sleep(0.1)
        self._rate_limiter.release()

    def _simulate_paper_order(self, symbol, exchange, side, order_type, qty, price, tag) -> str:
        order_id = str(uuid.uuid4())[:8]
        slippage = random.uniform(0, 3) / 10000 if order_type == "MARKET" else 0
        fill_price = (price or 0) * (1 + slippage if side == "BUY" else 1 - slippage)
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "exchange": exchange,
            "side": side,
            "order_type": order_type,
            "qty": qty,
            "price": price,
            "fill_price": fill_price,
            "status": OrderStatus.COMPLETE.value,
            "tag": tag,
        }
        self._paper_orders[order_id] = order
        self._paper_fills.append(order)
        logger.info("paper_order_placed", symbol=symbol, side=side, qty=qty, fill_price=fill_price, order_id=order_id)
        return order_id

    async def modify_order(self, order_id: str, price: Optional[float] = None, qty: Optional[int] = None) -> bool:
        if self._paper_mode:
            if order_id in self._paper_orders:
                if price:
                    self._paper_orders[order_id]["price"] = price
                logger.info("paper_order_modified", order_id=order_id)
                return True
            return False

        loop = asyncio.get_event_loop()
        try:
            def _modify():
                return self._kite.modify_order(
                    variety=self._kite.VARIETY_REGULAR,
                    order_id=order_id,
                    price=price,
                    quantity=qty,
                )
            await loop.run_in_executor(self._executor, _modify)
            return True
        except Exception as e:
            logger.error("modify_order_failed", order_id=order_id, error=str(e))
            return False

    async def cancel_order(self, order_id: str) -> bool:
        if self._paper_mode:
            if order_id in self._paper_orders:
                self._paper_orders[order_id]["status"] = OrderStatus.CANCELLED.value
                logger.info("paper_order_cancelled", order_id=order_id)
                return True
            return False

        loop = asyncio.get_event_loop()
        try:
            def _cancel():
                return self._kite.cancel_order(variety=self._kite.VARIETY_REGULAR, order_id=order_id)
            await loop.run_in_executor(self._executor, _cancel)
            return True
        except Exception as e:
            logger.error("cancel_order_failed", order_id=order_id, error=str(e))
            return False

    async def get_order_status(self, order_id: str) -> dict:
        if self._paper_mode:
            return self._paper_orders.get(order_id, {"status": OrderStatus.PENDING.value})

        loop = asyncio.get_event_loop()
        try:
            def _status():
                orders = self._kite.orders()
                for o in orders:
                    if str(o["order_id"]) == order_id:
                        return {**o, "status": ZERODHA_STATUS_MAP.get(o.get("status", ""), OrderStatus.PENDING).value}
                return {"status": OrderStatus.PENDING.value}
            return await loop.run_in_executor(self._executor, _status)
        except Exception as e:
            logger.error("get_order_status_failed", order_id=order_id, error=str(e))
            return {"status": OrderStatus.PENDING.value}

    async def get_all_orders(self) -> List[dict]:
        if self._paper_mode:
            return list(self._paper_orders.values())
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(self._executor, self._kite.orders)
        except Exception as e:
            logger.error("get_all_orders_failed", error=str(e))
            return []

    async def get_positions(self) -> dict:
        if self._paper_mode:
            return {"net": [], "day": []}
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(self._executor, self._kite.positions)
        except Exception as e:
            logger.error("get_positions_failed", error=str(e))
            return {"net": [], "day": []}
