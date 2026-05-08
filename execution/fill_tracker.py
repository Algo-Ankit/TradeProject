from collections import deque
from typing import Dict, List, Optional
import structlog

logger = structlog.get_logger()


class FillTracker:
    def __init__(self, redis_manager, clickhouse_writer, config: dict) -> None:
        self._redis = redis_manager
        self._clickhouse = clickhouse_writer
        self._max_slippage_bps = config.get("max_slippage_bps", 10.0)
        self._recent_fills: deque = deque(maxlen=1000)

    async def on_fill(self, fill_event) -> None:
        self._recent_fills.append({
            "symbol": fill_event.symbol,
            "strategy_id": fill_event.strategy_id,
            "slippage_bps": fill_event.slippage_bps,
            "commission_inr": fill_event.commission_inr,
            "qty": fill_event.qty,
            "fill_price": fill_event.fill_price,
            "side": fill_event.side,
        })

        commission_cost = fill_event.commission_inr
        slippage_cost = abs(fill_event.slippage_bps) * fill_event.qty * fill_event.fill_price / 10000

        await self._redis.increment("total_commission_paid", commission_cost)
        await self._redis.increment("total_slippage_cost_inr", slippage_cost)

        if abs(fill_event.slippage_bps) > self._max_slippage_bps:
            logger.warning(
                "high_slippage_detected",
                symbol=fill_event.symbol,
                strategy_id=fill_event.strategy_id,
                slippage_bps=round(fill_event.slippage_bps, 2),
                limit_bps=self._max_slippage_bps,
            )

    async def get_slippage_stats(self, strategy_id: Optional[str] = None, symbol: Optional[str] = None) -> dict:
        fills = list(self._recent_fills)
        if strategy_id:
            fills = [f for f in fills if f["strategy_id"] == strategy_id]
        if symbol:
            fills = [f for f in fills if f["symbol"] == symbol]

        if not fills:
            return {"avg_slippage_bps": 0.0, "total_commission": 0.0, "total_slippage_cost": 0.0, "n_fills": 0}

        avg_slippage = sum(abs(f["slippage_bps"]) for f in fills) / len(fills)
        total_commission = sum(f["commission_inr"] for f in fills)
        total_slippage = sum(abs(f["slippage_bps"]) * f["qty"] * f["fill_price"] / 10000 for f in fills)

        return {
            "avg_slippage_bps": round(avg_slippage, 3),
            "total_commission": round(total_commission, 2),
            "total_slippage_cost": round(total_slippage, 2),
            "n_fills": len(fills),
        }

    async def get_daily_summary(self) -> dict:
        fills = list(self._recent_fills)
        if not fills:
            return {}
        wins = [f for f in fills if f["slippage_bps"] < 0]
        limit_fills = [f for f in fills if f.get("order_type") == "LIMIT"]
        return {
            "total_fills": len(fills),
            "win_rate": len(wins) / len(fills),
            "avg_slippage_bps": sum(abs(f["slippage_bps"]) for f in fills) / len(fills),
            "total_commission": sum(f["commission_inr"] for f in fills),
            "fill_rate_limit": len(limit_fills) / len(fills) if fills else 0,
        }
