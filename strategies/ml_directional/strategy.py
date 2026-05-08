from datetime import datetime, time
from typing import Dict, Optional
import pytz
import structlog

from strategies.base import BaseStrategy, Signal
from strategies.ml_directional.predictor import MLPredictor

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")

TRADING_START = time(9, 30)
TRADING_END = time(14, 30)


class MLDirectionalStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str,
        config: dict,
        redis_manager,
        event_bus,
        predictors: Dict[str, MLPredictor],
    ) -> None:
        super().__init__(strategy_id=strategy_id, config=config, redis_manager=redis_manager, event_bus=event_bus)
        self._predictors = predictors
        self._min_confidence = config.get("min_model_confidence", 0.62)
        self._max_position_inr = config.get("max_position_inr", 300_000)
        self._stop_loss_pct = 0.005
        self._open_positions: Dict[str, dict] = {}

    async def on_tick(self, tick_event) -> Optional[Signal]:
        if not self.is_active:
            return None

        now = datetime.now(IST)
        if not (TRADING_START <= now.time() <= TRADING_END):
            return None

        symbol = tick_event.symbol
        predictor = self._predictors.get(symbol)
        if predictor is None:
            return None

        result = await predictor.predict_from_redis(symbol, self._redis)
        confidence = result.get("probability", 0.0)
        prediction = result.get("prediction", 0)

        if confidence < self._min_confidence:
            return None

        spread_z = await self._redis.get_feature(symbol, "spread_zscore")
        if spread_z is not None and abs(float(spread_z)) > 1.5:
            return None

        current_pos = self._open_positions.get(symbol)
        if current_pos is not None:
            price = tick_event.last_price
            entry = current_pos["entry_price"]
            side = current_pos["side"]
            if side == "BUY" and price < entry * (1 - self._stop_loss_pct):
                return await self._close_position(symbol, price, "stop_loss")
            if side == "SELL" and price > entry * (1 + self._stop_loss_pct):
                return await self._close_position(symbol, price, "stop_loss")
            return None

        side = "BUY" if prediction == 1 else "SELL"
        price = tick_event.last_price
        qty = max(1, int(self._max_position_inr * 0.01 / price))

        self._open_positions[symbol] = {"side": side, "entry_price": price, "qty": qty}
        logger.info("ml_directional_entry", symbol=symbol, side=side, confidence=round(confidence, 3))

        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol, side=side, qty=qty,
            urgency=0.5, expected_price=price,
            stop_price=price * (1 - self._stop_loss_pct) if side == "BUY" else price * (1 + self._stop_loss_pct),
            metadata={"confidence": confidence, "prediction": prediction},
        )

    async def _close_position(self, symbol: str, price: float, reason: str) -> Optional[Signal]:
        pos = self._open_positions.pop(symbol, None)
        if not pos:
            return None
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        logger.info("ml_directional_exit", symbol=symbol, reason=reason, side=close_side)
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol, side=close_side, qty=pos["qty"],
            urgency=0.8, expected_price=price,
            metadata={"exit_reason": reason},
        )

    async def on_fill(self, fill_event) -> None:
        symbol = fill_event.symbol
        qty = fill_event.qty if fill_event.side == "BUY" else -fill_event.qty
        pos = await self._redis.get_position(self.strategy_id, symbol) or {"qty": 0, "avg_price": 0}
        new_qty = pos["qty"] + qty
        await self._redis.update_position(self.strategy_id, symbol, new_qty, fill_event.fill_price)

    async def generate_signal(self) -> Optional[Signal]:
        return None
