import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
import numpy as np


@dataclass
class BacktestFill:
    order_id: str
    symbol: str
    side: str
    qty: int
    fill_price: float
    expected_price: float
    slippage_bps: float
    commission_inr: float
    timestamp: datetime
    is_partial: bool = False


class FillModel:
    def __init__(self, config: dict) -> None:
        self._commission_bps = float(config.get("commission_bps", 3))
        self._eta = float(config.get("market_impact_eta", 0.1))
        self._sigma = float(config.get("price_volatility", 0.01))
        self._adv_map: Dict[str, float] = {}

    def simulate_fill(self, signal, current_time: datetime, current_tick: dict) -> Optional[BacktestFill]:
        bid = float(current_tick.get("bid", current_tick.get("last_price", 0)))
        ask = float(current_tick.get("ask", current_tick.get("last_price", 0)))

        if bid <= 0 or ask <= 0:
            return None

        depth_at_best = float(current_tick.get("bid_qty" if signal.side == "SELL" else "ask_qty", 1000))
        if depth_at_best <= 0:
            depth_at_best = 1000

        urgency = getattr(signal, "urgency", 0.5)
        is_limit = urgency < 0.7

        if is_limit:
            queue_pos = depth_at_best * 0.3
            fill_prob = max(0, 1 - queue_pos / depth_at_best)
            if random.random() > fill_prob:
                return None

        actual_qty = min(signal.qty, int(depth_at_best))
        is_partial = actual_qty < signal.qty
        if actual_qty <= 0:
            actual_qty = signal.qty

        adv = self._adv_map.get(signal.symbol, 1_000_000)
        impact_bps = self._eta * self._sigma * float(np.sqrt(actual_qty / max(adv, 1))) * 10000

        if signal.side == "BUY":
            raw_price = ask
            fill_price = raw_price * (1 + impact_bps / 10000)
        else:
            raw_price = bid
            fill_price = raw_price * (1 - impact_bps / 10000)

        expected_price = getattr(signal, "expected_price", raw_price) or raw_price
        slippage_bps = ((fill_price - expected_price) / expected_price * 10000) if expected_price > 0 else 0.0
        commission = actual_qty * fill_price * self._commission_bps / 10000

        return BacktestFill(
            order_id=str(uuid.uuid4())[:8],
            symbol=signal.symbol,
            side=signal.side,
            qty=actual_qty,
            fill_price=fill_price,
            expected_price=expected_price,
            slippage_bps=slippage_bps,
            commission_inr=commission,
            timestamp=current_time,
            is_partial=is_partial,
        )

    def set_adv(self, symbol: str, adv: float) -> None:
        self._adv_map[symbol] = adv
