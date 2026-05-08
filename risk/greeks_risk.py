import json
from typing import Dict, List
import structlog

from risk.limits import GreeksLimits

logger = structlog.get_logger()


class GreeksRiskManager:
    def __init__(self, redis_manager) -> None:
        self._redis = redis_manager

    async def update_greeks(
        self,
        strategy_id: str,
        symbol: str,
        delta: float,
        gamma: float,
        vega: float,
        theta: float,
        qty: int,
    ) -> None:
        key = f"greeks:{strategy_id}:{symbol}"
        value = json.dumps({"delta": delta * qty, "gamma": gamma * qty, "vega": vega * qty, "theta": theta * qty})
        await self._redis.set_value(key, value)
        await self._update_portfolio_greeks()

    async def _update_portfolio_greeks(self) -> None:
        net = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        async for key in self._redis._redis.scan_iter("greeks:*:*"):
            data = await self._redis._redis.get(key)
            if data:
                pos_greeks = json.loads(data)
                for k in net:
                    net[k] += pos_greeks.get(k, 0.0)
        for k, v in net.items():
            await self._redis.set_value(f"portfolio:net_{k}", v)

    async def get_portfolio_greeks(self) -> Dict[str, float]:
        result = {}
        for greek in ("delta", "gamma", "vega", "theta"):
            val = await self._redis.get_float(f"portfolio:net_{greek}", default=0.0)
            result[f"net_{greek}"] = val
        return result

    async def check_greeks_limits(self, limits: GreeksLimits) -> List[str]:
        greeks = await self.get_portfolio_greeks()
        breaches = []
        if abs(greeks.get("net_delta", 0)) > limits.max_net_delta:
            breaches.append(f"net_delta {greeks['net_delta']:.1f} > {limits.max_net_delta}")
        if abs(greeks.get("net_gamma", 0)) > limits.max_net_gamma:
            breaches.append(f"net_gamma {greeks['net_gamma']:.1f} > {limits.max_net_gamma}")
        if abs(greeks.get("net_vega", 0)) > limits.max_net_vega:
            breaches.append(f"net_vega {greeks['net_vega']:.1f} > {limits.max_net_vega}")
        return breaches
