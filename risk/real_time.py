import asyncio
from datetime import datetime
from typing import List, Optional
import pytz
import structlog

from risk.limits import RiskLimits

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class RealTimeRiskMonitor:
    def __init__(
        self,
        limits: RiskLimits,
        redis_manager,
        event_bus,
        kill_switch,
        strategies: list,
        starting_capital: float = 1_000_000.0,
    ) -> None:
        self._limits = limits
        self._redis = redis_manager
        self._event_bus = event_bus
        self._kill_switch = kill_switch
        self._strategies = {s.strategy_id: s for s in strategies}
        self._starting_capital = starting_capital
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("risk_monitor_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self._check_portfolio_drawdown()
                await self._check_strategy_drawdowns()
                await self._check_exposure_limits()
            except Exception as e:
                logger.error("risk_monitor_error", error=str(e))
            await asyncio.sleep(1)

    async def _check_portfolio_drawdown(self) -> None:
        total_pnl = 0.0
        async for key in self._redis._redis.scan_iter("pnl:*"):
            import json
            data = await self._redis._redis.get(key)
            if data:
                pnl_data = json.loads(data)
                total_pnl += pnl_data.get("total_pnl", 0.0)

        peak = await self._redis.get_float("portfolio:peak_capital", default=self._starting_capital)
        current_equity = self._starting_capital + total_pnl
        if current_equity > peak:
            await self._redis.set_value("portfolio:peak_capital", current_equity)
            peak = current_equity

        if peak > 0:
            drawdown_pct = (peak - current_equity) / peak * 100
            if drawdown_pct > self._limits.portfolio.max_daily_drawdown_pct:
                logger.critical("portfolio_drawdown_breached", drawdown_pct=round(drawdown_pct, 2), limit=self._limits.portfolio.max_daily_drawdown_pct)
                await self._kill_switch.activate(f"Portfolio drawdown {drawdown_pct:.1f}% > limit {self._limits.portfolio.max_daily_drawdown_pct}%")

            await self._emit_risk_event("portfolio_drawdown", "INFO", f"Drawdown {drawdown_pct:.2f}%", {"drawdown_pct": drawdown_pct, "equity": current_equity})

    async def _check_strategy_drawdowns(self) -> None:
        per_strategy_limits = self._limits.per_strategy.model_dump()
        for strategy_id, strategy in self._strategies.items():
            pnl = await self._redis.get_pnl(strategy_id) or {}
            total_pnl = pnl.get("total_pnl", 0.0)
            allocated = strategy.capital_allocated

            for key, lims in per_strategy_limits.items():
                if key.replace("_", "") in strategy_id.replace("_", ""):
                    max_dd = lims.get("max_daily_drawdown_pct", 100)
                    if allocated > 0:
                        dd_pct = -total_pnl / allocated * 100
                        if dd_pct > max_dd:
                            logger.warning("strategy_drawdown_breached", strategy_id=strategy_id, drawdown_pct=round(dd_pct, 2))
                            strategy.halt(f"Drawdown {dd_pct:.1f}% > limit {max_dd}%")
                    break

    async def _check_exposure_limits(self) -> None:
        gross = 0.0
        net = 0.0
        import json
        async for key in self._redis._redis.scan_iter("position:*:*"):
            data = await self._redis._redis.get(key)
            if data:
                pos = json.loads(data)
                qty = pos.get("qty", 0)
                price = pos.get("avg_price", 0)
                exposure = qty * price
                gross += abs(exposure)
                net += exposure

        max_gross = self._limits.portfolio.max_gross_exposure_inr
        util_pct = gross / max_gross * 100 if max_gross > 0 else 0
        if util_pct > self._limits.execution.max_slippage_bps:
            pass
        if util_pct > 90:
            await self._emit_risk_event("gross_exposure", "WARNING", f"Gross exposure {util_pct:.1f}% of limit", {"gross": gross, "util_pct": util_pct})

    async def _emit_risk_event(self, event_type: str, severity: str, message: str, details: dict) -> None:
        from core.events import RiskEvent
        event = RiskEvent(
            timestamp=datetime.now(IST),
            source_module="risk_monitor",
            event_type=event_type,
            severity=severity,
            message=message,
            details=details,
        )
        await self._event_bus.publish(event)
