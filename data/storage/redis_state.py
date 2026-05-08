import json
from typing import Any, Dict, List, Optional
import structlog

logger = structlog.get_logger()

TICK_TTL = 60
FEATURE_TTL = 300
POSITION_TTL = 86400


class RedisStateManager:
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, password: Optional[str] = None) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._redis = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis
        self._redis = aioredis.Redis(
            host=self._host,
            port=self._port,
            db=self._db,
            password=self._password,
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info("redis_connected", host=self._host, port=self._port)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def update_tick(self, symbol: str, tick: dict) -> None:
        key = f"tick:{symbol}"
        value = json.dumps({k: str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v for k, v in tick.items()})
        await self._redis.setex(key, TICK_TTL, value)

    async def get_tick(self, symbol: str) -> Optional[Dict]:
        key = f"tick:{symbol}"
        data = await self._redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def get_all_ticks(self) -> Dict[str, Dict]:
        result = {}
        async for key in self._redis.scan_iter("tick:*"):
            symbol = key.split(":", 1)[1]
            data = await self._redis.get(key)
            if data:
                result[symbol] = json.loads(data)
        return result

    async def update_position(self, strategy_id: str, symbol: str, qty: int, avg_price: float) -> None:
        key = f"position:{strategy_id}:{symbol}"
        value = json.dumps({"qty": qty, "avg_price": avg_price})
        await self._redis.setex(key, POSITION_TTL, value)

    async def get_position(self, strategy_id: str, symbol: str) -> Optional[Dict]:
        key = f"position:{strategy_id}:{symbol}"
        data = await self._redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def get_all_positions(self, strategy_id: str) -> Dict[str, Dict]:
        result = {}
        prefix = f"position:{strategy_id}:"
        async for key in self._redis.scan_iter(f"{prefix}*"):
            symbol = key[len(prefix):]
            data = await self._redis.get(key)
            if data:
                result[symbol] = json.loads(data)
        return result

    async def update_pnl(self, strategy_id: str, realized: float, unrealized: float) -> None:
        key = f"pnl:{strategy_id}"
        value = json.dumps({"realized_pnl": realized, "unrealized_pnl": unrealized, "total_pnl": realized + unrealized})
        await self._redis.set(key, value)

    async def get_pnl(self, strategy_id: str) -> Optional[Dict]:
        key = f"pnl:{strategy_id}"
        data = await self._redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def set_feature(self, symbol: str, feature_name: str, value: float) -> None:
        key = f"features:{symbol}:{feature_name}"
        await self._redis.setex(key, FEATURE_TTL, str(value))

    async def get_feature(self, symbol: str, feature_name: str) -> Optional[float]:
        key = f"features:{symbol}:{feature_name}"
        data = await self._redis.get(key)
        if data:
            try:
                return float(data)
            except ValueError:
                return None
        return None

    async def get_all_features(self, symbol: str) -> Dict[str, float]:
        result = {}
        prefix = f"features:{symbol}:"
        async for key in self._redis.scan_iter(f"{prefix}*"):
            feature_name = key[len(prefix):]
            data = await self._redis.get(key)
            if data:
                try:
                    result[feature_name] = float(data)
                except ValueError:
                    pass
        return result

    async def set_kill_switch(self, active: bool, reason: str = "") -> None:
        await self._redis.set("kill_switch:active", "true" if active else "false")
        if reason:
            await self._redis.set("kill_switch:reason", reason)
        logger.warning("kill_switch_set", active=active, reason=reason)

    async def is_kill_switch_active(self) -> bool:
        val = await self._redis.get("kill_switch:active")
        return val == "true"

    async def set_buying_power(self, amount_inr: float) -> None:
        await self._redis.set("portfolio:buying_power", str(amount_inr))

    async def get_buying_power(self) -> float:
        val = await self._redis.get("portfolio:buying_power")
        if val:
            try:
                return float(val)
            except ValueError:
                pass
        return 0.0

    async def increment(self, key: str, amount: float) -> float:
        return float(await self._redis.incrbyfloat(key, amount))

    async def get_float(self, key: str, default: float = 0.0) -> float:
        val = await self._redis.get(key)
        if val:
            try:
                return float(val)
            except ValueError:
                pass
        return default

    async def set_value(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        if ttl:
            await self._redis.setex(key, ttl, str(value))
        else:
            await self._redis.set(key, str(value))

    async def get_value(self, key: str) -> Optional[str]:
        return await self._redis.get(key)
