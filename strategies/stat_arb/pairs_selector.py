import asyncio
import json
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import polars as pl
import structlog

logger = structlog.get_logger()


class PairsSelector:
    def __init__(self, clickhouse_writer, redis_manager, config: dict) -> None:
        self._clickhouse = clickhouse_writer
        self._redis = redis_manager
        self._config = config
        self._max_pairs = config.get("max_open_pairs", 8)

    async def select_pairs(self, symbols: List[str], lookback_days: int = 180) -> List[dict]:
        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)

        sym_list = "','".join(symbols)
        sql = f"""
            SELECT symbol, toDate(timestamp) as dt, avgMerge(last_price) as close
            FROM trading.ticks
            WHERE symbol IN ('{sym_list}')
              AND toDate(timestamp) >= '{start_date}'
              AND toDate(timestamp) <= '{end_date}'
            GROUP BY symbol, dt
            ORDER BY symbol, dt
        """

        try:
            rows = await self._clickhouse.query(sql)
        except Exception as e:
            logger.error("pairs_selector_query_failed", error=str(e))
            rows = []

        if not rows:
            logger.warning("pairs_selector_no_data", symbols=symbols[:5])
            return []

        df = pl.DataFrame(rows)
        if df.is_empty():
            return []

        price_df = df.pivot(values="close", index="dt", on="symbol", aggregate_function="mean")
        price_df = price_df.drop_nulls()

        if len(price_df) < 30:
            logger.warning("pairs_selector_insufficient_data", rows=len(price_df))
            return []

        available_symbols = [c for c in price_df.columns if c != "dt"]
        pairs = []

        for i, sym_a in enumerate(available_symbols):
            for sym_b in available_symbols[i + 1:]:
                result = self._test_cointegration(
                    price_df[sym_a].to_numpy(),
                    price_df[sym_b].to_numpy(),
                    sym_a,
                    sym_b,
                )
                if result:
                    pairs.append(result)

        pairs.sort(key=lambda x: x["half_life"])
        top_pairs = pairs[:self._max_pairs * 2]

        logger.info("pairs_selected", total_tested=len(available_symbols) * (len(available_symbols) - 1) // 2, cointegrated=len(pairs), selected=len(top_pairs))
        return top_pairs

    def _test_cointegration(self, prices_a: np.ndarray, prices_b: np.ndarray, sym_a: str, sym_b: str) -> Optional[dict]:
        try:
            from statsmodels.tsa.stattools import coint
            from statsmodels.regression.linear_model import OLS
            from statsmodels.tools import add_constant

            if len(prices_a) < 30 or np.any(np.isnan(prices_a)) or np.any(np.isnan(prices_b)):
                return None

            _, pvalue, _ = coint(prices_a, prices_b)
            if pvalue >= 0.05:
                return None

            X = add_constant(prices_b)
            model = OLS(prices_a, X).fit()
            hedge_ratio = model.params[1]
            spread = prices_a - hedge_ratio * prices_b

            half_life = self._ou_half_life(spread)
            if half_life <= 0.5 or half_life > 60:
                return None

            return {
                "symbol_a": sym_a,
                "symbol_b": sym_b,
                "hedge_ratio": float(hedge_ratio),
                "half_life": float(half_life),
                "coint_pvalue": float(pvalue),
                "adf_pvalue": float(pvalue),
            }
        except Exception as e:
            logger.debug("coint_test_failed", sym_a=sym_a, sym_b=sym_b, error=str(e))
            return None

    def _ou_half_life(self, spread: np.ndarray) -> float:
        try:
            from statsmodels.regression.linear_model import OLS
            from statsmodels.tools import add_constant
            lag_spread = spread[:-1]
            delta_spread = np.diff(spread)
            X = add_constant(lag_spread)
            model = OLS(delta_spread, X).fit()
            kappa = -model.params[1]
            if kappa <= 0:
                return -1.0
            return float(np.log(2) / kappa)
        except Exception:
            return -1.0

    async def save_pairs(self, pairs: List[dict]) -> None:
        await self._redis.set_value("stat_arb:selected_pairs", json.dumps(pairs))
        logger.info("pairs_saved_to_redis", count=len(pairs))

    async def load_pairs(self) -> List[dict]:
        data = await self._redis.get_value("stat_arb:selected_pairs")
        if data:
            return json.loads(data)
        return []
