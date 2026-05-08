from collections import deque
from datetime import datetime
from typing import Dict, Tuple
import pytz
import structlog

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class TickValidator:
    def __init__(self, stale_threshold_seconds: int = 5, max_price_move_pct: float = 5.0) -> None:
        self._stale_threshold = stale_threshold_seconds
        self._max_price_move = max_price_move_pct / 100.0
        self._last_price: Dict[str, float] = {}
        self._last_volume: Dict[str, int] = {}
        self._last_timestamp: Dict[str, datetime] = {}
        self._stats: Dict[str, Dict] = {}

    def validate(self, tick: dict) -> Tuple[bool, str]:
        symbol = tick.get("symbol", "")
        self._ensure_stats(symbol)

        self._stats[symbol]["total"] += 1

        ts = tick.get("timestamp")
        if ts is None:
            return self._reject(symbol, "missing_timestamp")

        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = IST.localize(ts)
            now = datetime.now(IST)
            age_seconds = (now - ts).total_seconds()
            if age_seconds > self._stale_threshold:
                return self._reject(symbol, f"stale_tick_age_{age_seconds:.1f}s")

        last_price = float(tick.get("last_price", 0))
        bid = float(tick.get("bid", 0))
        ask = float(tick.get("ask", 0))
        bid_qty = int(tick.get("bid_qty", 0))
        ask_qty = int(tick.get("ask_qty", 0))
        volume = int(tick.get("volume", 0))

        if last_price <= 0:
            return self._reject(symbol, "zero_price")

        if bid <= 0 or ask <= 0:
            return self._reject(symbol, "zero_bid_or_ask")

        if bid > ask:
            return self._reject(symbol, "crossed_spread")

        if bid_qty <= 0 or ask_qty <= 0:
            return self._reject(symbol, "zero_depth")

        prev_volume = self._last_volume.get(symbol)
        if prev_volume is not None and volume < prev_volume:
            return self._reject(symbol, "volume_decreased")

        prev_price = self._last_price.get(symbol)
        if prev_price is not None and prev_price > 0:
            move = abs(last_price - prev_price) / prev_price
            if move > self._max_price_move:
                return self._reject(symbol, f"extreme_move_{move*100:.1f}pct")

        self._last_price[symbol] = last_price
        self._last_volume[symbol] = volume
        if isinstance(ts, datetime):
            self._last_timestamp[symbol] = ts

        return True, ""

    def _reject(self, symbol: str, reason: str) -> Tuple[bool, str]:
        self._ensure_stats(symbol)
        self._stats[symbol]["dropped"] += 1
        reasons = self._stats[symbol]["drop_reasons"]
        reasons[reason] = reasons.get(reason, 0) + 1
        logger.warning("tick_dropped", symbol=symbol, reason=reason)
        return False, reason

    def _ensure_stats(self, symbol: str) -> None:
        if symbol not in self._stats:
            self._stats[symbol] = {"total": 0, "dropped": 0, "drop_reasons": {}}

    def get_stats(self) -> Dict[str, Dict]:
        return dict(self._stats)
