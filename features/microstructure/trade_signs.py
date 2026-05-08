from typing import Dict
import polars as pl
import numpy as np

from features.base import BaseFeature


class TradeSignsFeature(BaseFeature):
    @property
    def name(self) -> str:
        return "trade_signs"

    @property
    def horizon_seconds(self) -> float:
        return 60.0

    @property
    def required_history_ticks(self) -> int:
        return 10

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if len(tick_buffer) < 2:
            return {"trade_sign_run": 0.0, "buy_sell_ratio_1m": 0.5}

        df = tick_buffer.with_columns([
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        ])

        prices = df["last_price"].to_numpy()
        mids = df["mid"].to_numpy()

        signs = np.zeros(len(prices))
        for i in range(len(prices)):
            if prices[i] > mids[i]:
                signs[i] = 1.0
            elif prices[i] < mids[i]:
                signs[i] = -1.0
            elif i > 0:
                diff = prices[i] - prices[i - 1]
                if diff > 0:
                    signs[i] = 1.0
                elif diff < 0:
                    signs[i] = -1.0
                else:
                    signs[i] = signs[i - 1]

        run = 0
        if len(signs) > 0:
            last_sign = signs[-1]
            for s in reversed(signs):
                if s == last_sign and last_sign != 0:
                    run += 1
                else:
                    break

        window = self._window_df(tick_buffer, 60.0)
        if len(window) >= 2:
            w_df = window.with_columns([
                ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
            ])
            w_prices = w_df["last_price"].to_numpy()
            w_mids = w_df["mid"].to_numpy()
            w_signs = np.where(w_prices > w_mids, 1.0, np.where(w_prices < w_mids, -1.0, 0.0))
            buys = float(np.sum(w_signs > 0))
            total = float(np.sum(w_signs != 0))
            buy_sell_ratio = buys / total if total > 0 else 0.5
        else:
            buy_sell_ratio = 0.5

        return {
            "trade_sign_run": float(run),
            "buy_sell_ratio_1m": buy_sell_ratio,
        }
