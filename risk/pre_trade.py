import structlog

from risk.limits import RiskDecision, RiskLimits
from strategies.base import Signal

logger = structlog.get_logger()

ADV_CACHE_KEY = "adv_20d:{symbol}"
ADV_CACHE_TTL = 3600


class PreTradeChecker:
    def __init__(self, limits: RiskLimits, redis_manager, clickhouse_writer, clock, kill_switch) -> None:
        self._limits = limits
        self._redis = redis_manager
        self._clickhouse = clickhouse_writer
        self._clock = clock
        self._kill_switch = kill_switch

    async def check(self, signal: Signal) -> RiskDecision:
        if await self._redis.is_kill_switch_active():
            return self._reject("kill_switch_active", signal)

        if not self._clock.is_market_open():
            return self._reject("market_closed", signal)

        order_value = signal.qty * signal.expected_price
        if order_value > self._limits.pre_trade.max_single_order_inr:
            return self._reject(
                f"order_too_large_{order_value:.0f}_vs_{self._limits.pre_trade.max_single_order_inr:.0f}",
                signal,
            )

        buying_power = await self._redis.get_buying_power()
        buffer_pct = self._limits.pre_trade.min_buying_power_buffer_pct / 100
        effective_bp = buying_power * (1 - buffer_pct)
        if order_value > effective_bp:
            return self._reject(f"insufficient_buying_power_{effective_bp:.0f}", signal)

        gross_exposure = await self._get_gross_exposure()
        if gross_exposure + order_value > self._limits.portfolio.max_gross_exposure_inr:
            return self._reject("gross_exposure_breach", signal)

        adv = await self._get_adv(signal.symbol)
        if adv > 0 and signal.qty / adv * 100 > self._limits.pre_trade.max_order_to_adv_pct:
            return self._reject(f"adv_breach_qty_{signal.qty}_adv_{adv:.0f}", signal)

        strategy_prefix = signal.strategy_id.split("_")[0]
        per_strategy = self._limits.per_strategy.model_dump()
        for key in per_strategy:
            if strategy_prefix in key:
                strat_limits = per_strategy[key]
                max_drawdown = strat_limits.get("max_daily_drawdown_pct", 100)
                pnl = await self._redis.get_pnl(signal.strategy_id) or {}
                realized = pnl.get("realized_pnl", 0.0)
                max_pos = strat_limits.get("max_position_inr", float("inf"))
                if order_value > max_pos:
                    return self._reject(f"strategy_position_limit_{max_pos:.0f}", signal)
                break

        logger.debug("pre_trade_approved", symbol=signal.symbol, qty=signal.qty, order_value=order_value)
        return RiskDecision(approved=True)

    def _reject(self, reason: str, signal: Signal) -> RiskDecision:
        logger.warning("pre_trade_rejected", reason=reason, symbol=signal.symbol, strategy=signal.strategy_id, qty=signal.qty)
        return RiskDecision(approved=False, reason=reason)

    async def _get_gross_exposure(self) -> float:
        total = 0.0
        async for key in self._redis._redis.scan_iter("position:*:*"):
            import json
            data = await self._redis._redis.get(key)
            if data:
                pos = json.loads(data)
                total += abs(pos.get("qty", 0)) * pos.get("avg_price", 0)
        return total

    async def _get_adv(self, symbol: str) -> float:
        cache_key = ADV_CACHE_KEY.format(symbol=symbol)
        cached = await self._redis.get_value(cache_key)
        if cached:
            try:
                return float(cached)
            except ValueError:
                pass

        try:
            rows = await self._clickhouse.query(
                f"SELECT avg(volume) as adv FROM trading.ticks WHERE symbol = '{symbol}' AND toDate(timestamp) >= today() - 20"
            )
            if rows and rows[0]["adv"]:
                adv = float(rows[0]["adv"])
                await self._redis.set_value(cache_key, adv, ttl=ADV_CACHE_TTL)
                return adv
        except Exception as e:
            logger.warning("adv_fetch_failed", symbol=symbol, error=str(e))
        return 0.0
