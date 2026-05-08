import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pytz
import structlog

from strategies.base import BaseStrategy, Signal
from strategies.stat_arb.kalman_filter import KalmanHedgeRatio

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class SpreadTrader(BaseStrategy):
    def __init__(self, strategy_id: str, config: dict, redis_manager, event_bus, clickhouse_writer) -> None:
        super().__init__(strategy_id=strategy_id, config=config, redis_manager=redis_manager, event_bus=event_bus)
        self._clickhouse = clickhouse_writer
        self._pairs: Dict[Tuple[str, str], dict] = {}
        self._active_positions: Dict[Tuple[str, str], dict] = {}
        self._latest_prices: Dict[str, float] = {}
        self._latest_ts: Dict[str, datetime] = {}

        self._entry_zscore = config.get("entry_zscore", 2.0)
        self._exit_zscore = config.get("exit_zscore", 0.5)
        self._stop_zscore = config.get("stop_zscore", 3.5)
        self._max_open_pairs = config.get("max_open_pairs", 8)
        self._max_position_inr = config.get("max_position_inr", 500_000)
        self._vpin_threshold = 0.7
        self._stale_tick_seconds = 5
        self._minutes_to_close_exit = 30

    async def initialize(self) -> None:
        from strategies.stat_arb.pairs_selector import PairsSelector
        selector = PairsSelector(self._clickhouse, self._redis, self.config)
        pairs = await selector.load_pairs()
        for p in pairs:
            key = (p["symbol_a"], p["symbol_b"])
            self._pairs[key] = {
                "kalman": KalmanHedgeRatio(),
                "half_life": p.get("half_life", 5),
                "hedge_ratio": p.get("hedge_ratio", 1.0),
                "spread_variances": [],
            }
        logger.info("spread_trader_initialized", pairs=len(self._pairs))

    async def on_tick(self, tick_event) -> Optional[Signal]:
        if not self.is_active:
            return None

        symbol = tick_event.symbol
        self._latest_prices[symbol] = tick_event.last_price
        self._latest_ts[symbol] = tick_event.timestamp

        await self._check_force_exit()

        for pair_key, pair_data in self._pairs.items():
            sym_a, sym_b = pair_key
            if symbol not in (sym_a, sym_b):
                continue
            if sym_a not in self._latest_prices or sym_b not in self._latest_prices:
                continue

            price_a = self._latest_prices[sym_a]
            price_b = self._latest_prices[sym_b]
            kalman_result = pair_data["kalman"].update(price_a, price_b)
            z = kalman_result["spread_zscore"]

            if pair_key in self._active_positions:
                signal = await self._check_exit(pair_key, z, kalman_result)
                if signal:
                    return signal
            else:
                if len(self._active_positions) < self._max_open_pairs:
                    signal = await self._check_entry(pair_key, pair_data, z, kalman_result, price_a, price_b)
                    if signal:
                        return signal

        return None

    async def _check_entry(self, pair_key, pair_data, z: float, kalman_result: dict, price_a: float, price_b: float) -> Optional[Signal]:
        sym_a, sym_b = pair_key
        if abs(z) <= self._entry_zscore:
            return None

        variance = kalman_result["spread_variance"]
        variances = pair_data["spread_variances"]
        variances.append(variance)
        if len(variances) > 100:
            variances.pop(0)
        avg_var = sum(variances) / len(variances) if variances else variance
        if avg_var > 0 and variance / avg_var > 2.0:
            return None

        now = datetime.now(IST)
        for sym in (sym_a, sym_b):
            last_ts = self._latest_ts.get(sym)
            if last_ts is None:
                return None
            if (now - last_ts).total_seconds() > self._stale_tick_seconds:
                return None

        for sym in (sym_a, sym_b):
            vpin = await self._redis.get_feature(sym, "vpin")
            if vpin is not None and float(vpin) >= self._vpin_threshold:
                return None

        capital_per_pair = self._max_position_inr / max(self._max_open_pairs, 1)
        hedge_ratio = kalman_result["hedge_ratio"]
        half_life = pair_data.get("half_life", 5)
        kelly_f = min(0.5, 1.0 / max(half_life, 1))

        if z > 0:
            side_a, side_b = "SELL", "BUY"
        else:
            side_a, side_b = "BUY", "SELL"

        qty_a = max(1, int(capital_per_pair * kelly_f / price_a))
        qty_b = max(1, int(qty_a * abs(hedge_ratio)))

        self._active_positions[pair_key] = {
            "entry_z": z,
            "entry_time": now,
            "side_a": side_a,
            "qty_a": qty_a,
            "qty_b": qty_b,
            "kalman_state": kalman_result,
        }

        logger.info("stat_arb_entry", sym_a=sym_a, sym_b=sym_b, z=round(z, 3), side_a=side_a)

        signal_a = Signal(
            strategy_id=self.strategy_id,
            symbol=sym_a, side=side_a, qty=qty_a, urgency=0.5,
            expected_price=price_a, metadata={"pair": str(pair_key), "leg": "a", "z": z},
        )
        signal_b = Signal(
            strategy_id=self.strategy_id,
            symbol=sym_b, side=side_b, qty=qty_b, urgency=0.5,
            expected_price=price_b, metadata={"pair": str(pair_key), "leg": "b", "z": z},
        )
        await self._emit_signal(signal_b)
        return signal_a

    async def _check_exit(self, pair_key, z: float, kalman_result: dict) -> Optional[Signal]:
        position = self._active_positions.get(pair_key)
        if not position:
            return None

        sym_a, sym_b = pair_key
        price_a = self._latest_prices.get(sym_a, 0)
        price_b = self._latest_prices.get(sym_b, 0)
        side_a = position["side_a"]
        qty_a = position["qty_a"]
        qty_b = position["qty_b"]

        exit_reason = None
        if abs(z) < self._exit_zscore:
            exit_reason = "target_reached"
        elif abs(z) > self._stop_zscore:
            exit_reason = "stop_loss"

        if exit_reason:
            logger.info("stat_arb_exit", sym_a=sym_a, sym_b=sym_b, z=round(z, 3), reason=exit_reason)
            del self._active_positions[pair_key]

            close_a = "BUY" if side_a == "SELL" else "SELL"
            close_b = "BUY" if close_a == "SELL" else "SELL"

            signal_b = Signal(
                strategy_id=self.strategy_id, symbol=sym_b, side=close_b,
                qty=qty_b, urgency=0.7, expected_price=price_b,
                metadata={"pair": str(pair_key), "leg": "b", "exit_reason": exit_reason},
            )
            await self._emit_signal(signal_b)
            return Signal(
                strategy_id=self.strategy_id, symbol=sym_a, side=close_a,
                qty=qty_a, urgency=0.7, expected_price=price_a,
                metadata={"pair": str(pair_key), "leg": "a", "exit_reason": exit_reason},
            )
        return None

    async def _check_force_exit(self) -> None:
        from core.clock import MarketClock
        clock = MarketClock()
        if clock.minutes_to_close() <= self._minutes_to_close_exit:
            for pair_key in list(self._active_positions.keys()):
                sym_a, sym_b = pair_key
                pos = self._active_positions.pop(pair_key)
                price_a = self._latest_prices.get(sym_a, 0)
                price_b = self._latest_prices.get(sym_b, 0)
                close_a = "BUY" if pos["side_a"] == "SELL" else "SELL"
                close_b = "BUY" if close_a == "SELL" else "SELL"
                logger.info("stat_arb_force_exit", sym_a=sym_a, sym_b=sym_b)
                sig_b = Signal(
                    strategy_id=self.strategy_id, symbol=sym_b, side=close_b,
                    qty=pos["qty_b"], urgency=0.9, expected_price=price_b,
                    metadata={"exit_reason": "market_close"},
                )
                await self._emit_signal(sig_b)
                sig_a = Signal(
                    strategy_id=self.strategy_id, symbol=sym_a, side=close_a,
                    qty=pos["qty_a"], urgency=0.9, expected_price=price_a,
                    metadata={"exit_reason": "market_close"},
                )
                await self._emit_signal(sig_a)

    async def on_fill(self, fill_event) -> None:
        symbol = fill_event.symbol
        qty = fill_event.qty if fill_event.side == "BUY" else -fill_event.qty
        pos = await self._redis.get_position(self.strategy_id, symbol) or {"qty": 0, "avg_price": 0}
        new_qty = pos["qty"] + qty
        new_price = fill_event.fill_price
        await self._redis.update_position(self.strategy_id, symbol, new_qty, new_price)

    async def generate_signal(self) -> Optional[Signal]:
        return None
