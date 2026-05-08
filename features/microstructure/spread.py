from typing import Dict
import polars as pl

from features.base import BaseFeature

ROLLING_WINDOW_SECONDS = 1800


class SpreadFeature(BaseFeature):
    @property
    def name(self) -> str:
        return "spread"

    @property
    def horizon_seconds(self) -> float:
        return float(ROLLING_WINDOW_SECONDS)

    @property
    def required_history_ticks(self) -> int:
        return 5

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if tick_buffer.is_empty():
            return self._zeros()

        last = tick_buffer[-1]
        bid = float(last["bid"][0])
        ask = float(last["ask"][0])
        bid_qty = float(last["bid_qty"][0])
        ask_qty = float(last["ask_qty"][0])

        if ask <= 0 or bid <= 0:
            return self._zeros()

        mid = (bid + ask) / 2.0
        absolute_spread = ask - bid
        relative_spread = (absolute_spread / mid * 10000) if mid > 0 else 0.0
        microprice = ((bid * bid_qty + ask * ask_qty) / (bid_qty + ask_qty)) if (bid_qty + ask_qty) > 0 else mid

        window = self._window_df(tick_buffer, ROLLING_WINDOW_SECONDS)
        if len(window) >= 5:
            window = window.with_columns([
                ((pl.col("ask") - pl.col("bid")) / ((pl.col("ask") + pl.col("bid")) / 2) * 10000).alias("rel_spread")
            ])
            mean = window["rel_spread"].mean() or 0.0
            std = window["rel_spread"].std() or 1.0
            spread_zscore = (relative_spread - mean) / std if std > 0 else 0.0
        else:
            spread_zscore = 0.0

        return {
            "absolute_spread": absolute_spread,
            "relative_spread": relative_spread,
            "mid_price": mid,
            "microprice": microprice,
            "spread_zscore": spread_zscore,
        }

    def _zeros(self) -> Dict[str, float]:
        return {
            "absolute_spread": 0.0,
            "relative_spread": 0.0,
            "mid_price": 0.0,
            "microprice": 0.0,
            "spread_zscore": 0.0,
        }
