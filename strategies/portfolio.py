import asyncio
from typing import Dict, List, Optional
import structlog

logger = structlog.get_logger()


class PortfolioManager:
    def __init__(
        self,
        strategies: list,
        redis_manager,
        clickhouse_writer,
        config: dict,
        starting_capital: float = 1_000_000.0,
    ) -> None:
        self._strategies = {s.strategy_id: s for s in strategies}
        self._redis = redis_manager
        self._clickhouse = clickhouse_writer
        self._config = config
        self._starting_capital = starting_capital
        self._snapshot_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())

    async def on_tick(self, tick_event) -> None:
        for strategy in self._strategies.values():
            if strategy.is_active:
                try:
                    signal = await strategy.on_tick(tick_event)
                    if signal:
                        await strategy._emit_signal(signal)
                except Exception as e:
                    logger.error("strategy_tick_error", strategy_id=strategy.strategy_id, error=str(e))

    async def on_fill(self, fill_event) -> None:
        strategy = self._strategies.get(fill_event.strategy_id)
        if strategy:
            await strategy.on_fill(fill_event)

    async def get_portfolio_summary(self) -> Dict:
        total_realized = 0.0
        total_unrealized = 0.0
        gross_exposure = 0.0
        net_exposure = 0.0
        per_strategy = {}

        for strategy_id in self._strategies:
            pnl = await self._redis.get_pnl(strategy_id) or {}
            realized = pnl.get("realized_pnl", 0.0)
            unrealized = pnl.get("unrealized_pnl", 0.0)
            total_realized += realized
            total_unrealized += unrealized

            positions = await self._redis.get_all_positions(strategy_id)
            for symbol, pos in positions.items():
                qty = pos.get("qty", 0)
                price = pos.get("avg_price", 0)
                exposure = abs(qty) * price
                gross_exposure += exposure
                net_exposure += qty * price

            per_strategy[strategy_id] = {
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "total_pnl": realized + unrealized,
            }

        return {
            "total_realized_pnl": total_realized,
            "total_unrealized_pnl": total_unrealized,
            "total_pnl": total_realized + total_unrealized,
            "gross_exposure": gross_exposure,
            "net_exposure": net_exposure,
            "per_strategy": per_strategy,
        }

    async def check_daily_drawdown(self) -> float:
        summary = await self.get_portfolio_summary()
        total_pnl = summary["total_pnl"]
        peak = await self._redis.get_float("portfolio:peak_capital", default=self._starting_capital)
        current_equity = self._starting_capital + total_pnl
        if current_equity > peak:
            await self._redis.set_value("portfolio:peak_capital", current_equity)
            peak = current_equity
        return (peak - current_equity) / peak * 100 if peak > 0 else 0.0

    async def _snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                summary = await self.get_portfolio_summary()
                import pytz
                from datetime import datetime
                IST = pytz.timezone("Asia/Kolkata")
                snapshot = {
                    "timestamp": datetime.now(IST),
                    "strategy_id": "portfolio",
                    "realized_pnl": summary["total_realized_pnl"],
                    "unrealized_pnl": summary["total_unrealized_pnl"],
                    "total_pnl": summary["total_pnl"],
                    "gross_exposure": summary["gross_exposure"],
                    "net_exposure": summary["net_exposure"],
                }
                await self._clickhouse.write_pnl_snapshot(snapshot)
            except Exception as e:
                logger.error("portfolio_snapshot_error", error=str(e))
