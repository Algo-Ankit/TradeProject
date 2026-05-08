from datetime import datetime
from typing import List, Optional
import pytz
import structlog

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")

CONFIRM_CODE = "CONFIRM_DEACTIVATE_KILL_SWITCH"


class KillSwitch:
    def __init__(self, redis_manager, order_manager=None, event_bus=None) -> None:
        self._redis = redis_manager
        self._order_manager = order_manager
        self._event_bus = event_bus

    async def activate(self, reason: str) -> None:
        await self._redis.set_kill_switch(active=True, reason=reason)
        logger.critical("kill_switch_activated", reason=reason, timestamp=datetime.now(IST).isoformat())

        if self._event_bus:
            from core.events import RiskEvent
            event = RiskEvent(
                timestamp=datetime.now(IST),
                source_module="kill_switch",
                event_type="kill_switch_activated",
                severity="CRITICAL",
                message=reason,
                details={"reason": reason},
            )
            await self._event_bus.publish(event)

        cancelled = await self.cancel_all_orders()
        signals = await self.flatten_all_positions()
        logger.critical("kill_switch_actions_taken", orders_cancelled=cancelled, flatten_signals=len(signals))

    async def cancel_all_orders(self) -> int:
        if self._order_manager is None:
            return 0
        open_orders = await self._order_manager.get_open_orders()
        count = 0
        for order in open_orders:
            try:
                await self._order_manager.cancel(order["order_id"])
                count += 1
            except Exception as e:
                logger.error("cancel_order_failed", order_id=order.get("order_id"), error=str(e))
        logger.warning("kill_switch_cancelled_orders", count=count)
        return count

    async def flatten_all_positions(self) -> List:
        signals = []
        import json
        async for key in self._redis._redis.scan_iter("position:*:*"):
            data = await self._redis._redis.get(key)
            if not data:
                continue
            pos = json.loads(data)
            parts = key.split(":")
            if len(parts) < 3:
                continue
            strategy_id = parts[1]
            symbol = parts[2]
            qty = pos.get("qty", 0)
            if qty == 0:
                continue

            from strategies.base import Signal
            side = "SELL" if qty > 0 else "BUY"
            signal = Signal(
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                qty=abs(qty),
                urgency=1.0,
                expected_price=0.0,
                metadata={"reason": "kill_switch_flatten"},
            )
            signals.append(signal)

            if self._event_bus:
                from core.events import SignalEvent
                event = SignalEvent(
                    timestamp=datetime.now(IST),
                    source_module="kill_switch",
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side=side,
                    qty=abs(qty),
                    urgency=1.0,
                    metadata={"reason": "kill_switch_flatten"},
                )
                await self._event_bus.publish(event)

        logger.warning("kill_switch_flatten_signals", count=len(signals))
        return signals

    async def deactivate(self, confirmation_code: str) -> bool:
        if confirmation_code != CONFIRM_CODE:
            logger.error("kill_switch_deactivate_wrong_code")
            return False
        await self._redis.set_kill_switch(active=False, reason="")
        logger.warning("kill_switch_deactivated", timestamp=datetime.now(IST).isoformat())
        return True

    async def is_active(self) -> bool:
        return await self._redis.is_kill_switch_active()
