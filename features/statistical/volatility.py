from typing import Dict
import numpy as np
import polars as pl

from features.base import BaseFeature

ANNUALIZATION_FACTOR = 252 * 375 * 60


class VolatilityFeature(BaseFeature):
    @property
    def name(self) -> str:
        return "volatility"

    @property
    def horizon_seconds(self) -> float:
        return 1800.0

    @property
    def required_history_ticks(self) -> int:
        return 30

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if len(tick_buffer) < self.required_history_ticks:
            return self._zeros()

        results = {}
        for key, seconds in [("realized_vol_1m", 60), ("realized_vol_5m", 300), ("realized_vol_30m", 1800)]:
            results[key] = self._realized_vol(tick_buffer, seconds)

        results["parkinson_vol"] = self._parkinson_vol(tick_buffer, 1800)
        rv_5m = results["realized_vol_5m"]
        rv_30m = results["realized_vol_30m"]
        results["vol_ratio"] = rv_5m / rv_30m if rv_30m > 1e-10 else 1.0

        return results

    def _realized_vol(self, df: pl.DataFrame, seconds: float) -> float:
        window = self._window_df(df, seconds)
        if len(window) < 2:
            return 0.0
        prices = window["last_price"].to_numpy()
        if len(prices) < 2:
            return 0.0
        log_returns = np.diff(np.log(prices + 1e-10))
        if len(log_returns) == 0:
            return 0.0
        per_tick_var = np.sum(log_returns ** 2)
        ticks_per_second = len(log_returns) / seconds if seconds > 0 else 1
        annualized_var = per_tick_var * (ANNUALIZATION_FACTOR * ticks_per_second)
        return float(np.sqrt(max(annualized_var, 0)) * 100)

    def _parkinson_vol(self, df: pl.DataFrame, seconds: float) -> float:
        window = self._window_df(df, seconds)
        if len(window) < 10:
            return 0.0

        window = window.with_columns([
            pl.col("timestamp").dt.truncate("1m").alias("minute")
        ])

        agg = window.group_by("minute").agg([
            pl.col("last_price").max().alias("high"),
            pl.col("last_price").min().alias("low"),
        ])

        if agg.is_empty():
            return 0.0

        highs = agg["high"].to_numpy()
        lows = agg["low"].to_numpy()
        valid = (highs > 0) & (lows > 0) & (highs >= lows)
        highs, lows = highs[valid], lows[valid]
        if len(highs) == 0:
            return 0.0

        log_hl = np.log(highs / lows)
        parkinson_var = np.mean(log_hl ** 2) / (4 * np.log(2))
        return float(np.sqrt(parkinson_var * 252 * 375) * 100)

    def _zeros(self) -> Dict[str, float]:
        return {
            "realized_vol_1m": 0.0,
            "realized_vol_5m": 0.0,
            "realized_vol_30m": 0.0,
            "parkinson_vol": 0.0,
            "vol_ratio": 1.0,
        }
