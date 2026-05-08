from typing import Dict
import numpy as np
import polars as pl

from features.base import BaseFeature

DEFAULT_BUCKET_COUNT = 50
DEFAULT_VPIN_THRESHOLD = 0.7


class VPINFeature(BaseFeature):
    def __init__(self, bucket_count: int = DEFAULT_BUCKET_COUNT, vpin_threshold: float = DEFAULT_VPIN_THRESHOLD) -> None:
        self._bucket_count = bucket_count
        self._vpin_threshold = vpin_threshold

    @property
    def name(self) -> str:
        return "vpin"

    @property
    def horizon_seconds(self) -> float:
        return 3600.0

    @property
    def required_history_ticks(self) -> int:
        return 200

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if len(tick_buffer) < self.required_history_ticks:
            return {"vpin": 0.5, "high_vpin_flag": 0.0}

        df = tick_buffer.with_columns([
            pl.col("last_price").diff().alias("price_diff"),
            pl.col("volume").diff().alias("vol_diff"),
        ]).drop_nulls()

        if df.is_empty():
            return {"vpin": 0.5, "high_vpin_flag": 0.0}

        vol_diff_series = df["vol_diff"]
        total_volume = float(vol_diff_series.filter(vol_diff_series > 0).sum())
        if total_volume <= 0:
            return {"vpin": 0.5, "high_vpin_flag": 0.0}

        bucket_size = total_volume / self._bucket_count

        prices = df["last_price"].to_numpy()
        vol_diffs = np.maximum(df["vol_diff"].to_numpy(), 0)

        buckets = []
        current_buy = 0.0
        current_sell = 0.0
        current_vol = 0.0

        for i in range(len(prices)):
            dv = vol_diffs[i]
            if dv <= 0:
                continue

            if i > 0 and (prices[i] - prices[i - 1]) > 0:
                current_buy += dv
            else:
                current_sell += dv
            current_vol += dv

            while current_vol >= bucket_size:
                fraction = bucket_size / current_vol
                b_buy = current_buy * fraction
                b_sell = current_sell * fraction
                buckets.append(abs(b_buy - b_sell) / bucket_size)
                current_buy -= b_buy
                current_sell -= b_sell
                current_vol -= bucket_size

        if not buckets:
            return {"vpin": 0.5, "high_vpin_flag": 0.0}

        recent_buckets = buckets[-self._bucket_count:]
        vpin = float(np.mean(recent_buckets))
        vpin = max(0.0, min(1.0, vpin))

        return {
            "vpin": vpin,
            "high_vpin_flag": 1.0 if vpin >= self._vpin_threshold else 0.0,
        }
