from typing import Dict, List
import structlog

logger = structlog.get_logger()


class OptionsRVScanner:
    def __init__(self, config: dict) -> None:
        self._config = config
        self._rich_threshold = config.get("rich_threshold", 0.7)
        self._cheap_threshold = config.get("cheap_threshold", 1.3)

    async def scan(self, symbols: List[str], redis_manager) -> List[dict]:
        opportunities = []
        for symbol in symbols:
            iv = await redis_manager.get_feature(symbol, "implied_vol")
            rv = await redis_manager.get_feature(symbol, "realized_vol_30m")

            if iv is None or rv is None:
                continue

            iv_f = float(iv)
            rv_f = float(rv)
            if iv_f <= 0:
                continue

            ratio = rv_f / iv_f
            if ratio < self._rich_threshold:
                opportunities.append({
                    "symbol": symbol,
                    "iv": iv_f,
                    "rv": rv_f,
                    "rv_iv_ratio": ratio,
                    "signal": "SELL_PREMIUM",
                    "strength": abs(ratio - 1.0),
                })
                logger.info("rv_opportunity", symbol=symbol, signal="SELL_PREMIUM", ratio=round(ratio, 3))
            elif ratio > self._cheap_threshold:
                opportunities.append({
                    "symbol": symbol,
                    "iv": iv_f,
                    "rv": rv_f,
                    "rv_iv_ratio": ratio,
                    "signal": "BUY_GAMMA",
                    "strength": abs(ratio - 1.0),
                })
                logger.info("rv_opportunity", symbol=symbol, signal="BUY_GAMMA", ratio=round(ratio, 3))

        opportunities.sort(key=lambda x: -x["strength"])
        return opportunities
