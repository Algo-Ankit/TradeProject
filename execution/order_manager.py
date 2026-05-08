import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional
import pytz
import structlog

from execution.brokers.zerodha import OrderStatus

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class OrderManager:
    def __init__(self, execution_client, redis_manager, clickhouse_writer, event_bus, config: dict) -> None:
        self._client = execution_client
        self._redis = redis_manager
        self._clickhouse = clickhouse_writer
        self._event_bus = event_bus
        self._passive_timeout = config.get("passive_timeout_seconds", 30)
        self._stuck_check_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._stuck_check_task = asyncio.create_task(self._stuck_order_monitor())

    async def submit(self, signal) -> str:
        order_id = str(uuid.uuid4())
        ts = datetime.now(IST)
        urgency = getattr(signal, "urgency", 0.5)
        order_type = "MARKET" if urgency > 0.7 else "LIMIT"

        order_doc = {
            "order_id": order_id,
            "strategy_id": signal.strategy_id,
            "timestamp_sent": ts,
            "timestamp_acked": None,
            "symbol": signal.symbol,
            "side": signal.side,
            "order_type": order_type,
            "qty": signal.qty,
            "limit_price": signal.expected_price if order_type == "LIMIT" else None,
            "status": OrderStatus.PENDING.value,
            "broker_order_id": None,
            "urgency": urgency,
        }

        await self._redis.set_value(f"orders:open:{order_id}", json.dumps(order_doc, default=str))
        await self._clickhouse.write_order(order_doc)

        try:
            broker_id = await self._client.place_order(
                symbol=signal.symbol,
                exchange="NSE",
                side=signal.side,
                order_type=order_type,
                qty=signal.qty,
                price=signal.expected_price if order_type == "LIMIT" else None,
                tag=signal.strategy_id[:20],
            )
            order_doc["broker_order_id"] = broker_id
            order_doc["status"] = OrderStatus.OPEN.value
            order_doc["timestamp_acked"] = datetime.now(IST)
            await self._redis.set_value(f"orders:open:{order_id}", json.dumps(order_doc, default=str))
            logger.info("order_submitted", order_id=order_id, broker_id=broker_id, symbol=signal.symbol)
        except Exception as e:
            order_doc["status"] = OrderStatus.REJECTED.value
            await self._redis.set_value(f"orders:open:{order_id}", json.dumps(order_doc, default=str))
            logger.error("order_submit_failed", order_id=order_id, error=str(e))

        return order_id

    async def cancel(self, order_id: str) -> bool:
        data = await self._redis.get_value(f"orders:open:{order_id}")
        if not data:
            return False
        order = json.loads(data)
        broker_id = order.get("broker_order_id")
        success = False
        if broker_id:
            success = await self._client.cancel_order(broker_id)
        order["status"] = OrderStatus.CANCELLED.value
        await self._redis.set_value(f"orders:open:{order_id}", json.dumps(order, default=str))
        await self._redis._redis.delete(f"orders:open:{order_id}")
        return success

    async def update_status(self, order_id: str, status: OrderStatus, fill_data: Optional[dict] = None) -> None:
        key = f"orders:open:{order_id}"
        data = await self._redis.get_value(key)
        if not data:
            return
        order = json.loads(data)
        order["status"] = status.value

        if status == OrderStatus.COMPLETE and fill_data:
            expected_price = order.get("limit_price") or fill_data.get("fill_price", 0)
            fill_price = fill_data.get("fill_price", 0)
            slippage_bps = ((fill_price - expected_price) / expected_price * 10000) if expected_price > 0 else 0.0
            commission = fill_data.get("qty", 0) * fill_price * 0.0003

            fill_doc = {
                "fill_id": str(uuid.uuid4()),
                "order_id": order_id,
                "strategy_id": order["strategy_id"],
                "timestamp": datetime.now(IST),
                "symbol": order["symbol"],
                "side": order["side"],
                "qty": fill_data.get("qty", order["qty"]),
                "fill_price": fill_price,
                "expected_price": expected_price,
                "slippage_bps": slippage_bps,
                "commission_inr": commission,
            }
            await self._clickhouse.write_fill(fill_doc)
            await self._redis._redis.delete(key)

            from core.events import FillEvent
            event = FillEvent(
                timestamp=datetime.now(IST),
                source_module="order_manager",
                fill_id=fill_doc["fill_id"],
                order_id=order_id,
                strategy_id=fill_doc["strategy_id"],
                symbol=fill_doc["symbol"],
                side=fill_doc["side"],
                qty=fill_doc["qty"],
                fill_price=fill_price,
                expected_price=expected_price,
                slippage_bps=slippage_bps,
                commission_inr=commission,
            )
            await self._event_bus.publish(event)
            logger.info("fill_recorded", order_id=order_id, fill_price=fill_price, slippage_bps=round(slippage_bps, 2))
        else:
            await self._redis.set_value(key, json.dumps(order, default=str))

    async def get_open_orders(self) -> List[dict]:
        orders = []
        async for key in self._redis._redis.scan_iter("orders:open:*"):
            data = await self._redis._redis.get(key)
            if data:
                orders.append(json.loads(data))
        return orders

    async def _stuck_order_monitor(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                orders = await self.get_open_orders()
                now = datetime.now(IST)
                for order in orders:
                    if order.get("status") != OrderStatus.OPEN.value:
                        continue
                    if order.get("order_type") != "LIMIT":
                        continue
                    ts_sent = order.get("timestamp_sent")
                    if ts_sent:
                        if isinstance(ts_sent, str):
                            ts_sent = datetime.fromisoformat(ts_sent)
                        if isinstance(ts_sent, datetime):
                            if ts_sent.tzinfo is None:
                                ts_sent = IST.localize(ts_sent)
                            age = (now - ts_sent).total_seconds()
                            if age > self._passive_timeout:
                                logger.warning("stuck_order_detected", order_id=order["order_id"], age_seconds=age)
            except Exception as e:
                logger.error("stuck_order_monitor_error", error=str(e))
