import asyncio
from typing import List, Optional
import structlog

from strategies.base import Signal

logger = structlog.get_logger()


class SmartExecutor:
    def __init__(self, order_manager, redis_manager, config: dict) -> None:
        self._order_manager = order_manager
        self._redis = redis_manager
        self._passive_timeout = config.get("passive_timeout_seconds", 30)
        self._depth_slice_threshold = 0.10

    async def execute(self, signal: Signal) -> List[str]:
        bid = await self._redis.get_feature(signal.symbol, "bid") or signal.expected_price
        ask = await self._redis.get_feature(signal.symbol, "ask") or signal.expected_price
        bid_qty = await self._redis.get_feature(signal.symbol, "bid_qty") or 1000
        ask_qty = await self._redis.get_feature(signal.symbol, "ask_qty") or 1000

        if bid is None:
            bid = signal.expected_price
        if ask is None:
            ask = signal.expected_price

        depth = {
            "bid": float(bid), "ask": float(ask),
            "bid_qty": float(bid_qty), "ask_qty": float(ask_qty),
        }

        top_depth = depth["bid_qty"] if signal.side == "SELL" else depth["ask_qty"]
        if top_depth > 0 and signal.qty > top_depth * self._depth_slice_threshold:
            child_signals = self._slice_order(signal, n_slices=4)
        else:
            child_signals = [signal]

        order_ids = []
        for i, child in enumerate(child_signals):
            if i > 0:
                await asyncio.sleep(7.5)
            limit_price = self._get_limit_price(child, depth)
            child.expected_price = limit_price if limit_price else child.expected_price
            order_id = await self._order_manager.submit(child)
            order_ids.append(order_id)

            if child.urgency < 0.7 and limit_price:
                asyncio.create_task(
                    self._upgrade_to_market(order_id, child.symbol)
                )

        return order_ids

    def _get_limit_price(self, signal: Signal, depth: dict) -> Optional[float]:
        urgency = signal.urgency
        bid = depth.get("bid", signal.expected_price)
        ask = depth.get("ask", signal.expected_price)
        mid = (bid + ask) / 2.0

        if urgency > 0.7:
            return None
        if urgency < 0.3:
            return bid if signal.side == "BUY" else ask
        return mid

    def _slice_order(self, signal: Signal, n_slices: int = 4) -> List[Signal]:
        from dataclasses import replace
        base_qty = signal.qty // n_slices
        remainder = signal.qty % n_slices
        slices = []
        for i in range(n_slices):
            qty = base_qty + (1 if i < remainder else 0)
            if qty > 0:
                child = Signal(
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    side=signal.side,
                    qty=qty,
                    urgency=signal.urgency,
                    expected_price=signal.expected_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    metadata={**signal.metadata, "slice": i + 1, "total_slices": n_slices},
                )
                slices.append(child)
        return slices

    async def _upgrade_to_market(self, order_id: str, symbol: str) -> None:
        await asyncio.sleep(self._passive_timeout)
        orders = await self._order_manager.get_open_orders()
        for order in orders:
            if order.get("order_id") == order_id and order.get("status") == "OPEN":
                logger.info("upgrading_order_to_market", order_id=order_id, symbol=symbol)
                await self._order_manager.cancel(order_id)
