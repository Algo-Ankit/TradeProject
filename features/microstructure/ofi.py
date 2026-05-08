from typing import Dict
import polars as pl
import structlog

from features.base import BaseFeature

logger = structlog.get_logger()

HORIZONS = {"ofi_1s": 1, "ofi_5s": 5, "ofi_30s": 30, "ofi_5m": 300}


class OFIFeature(BaseFeature):
    @property
    def name(self) -> str:
        return "ofi"

    @property
    def horizon_seconds(self) -> float:
        return 300.0

    @property
    def required_history_ticks(self) -> int:
        return 10

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if tick_buffer.is_empty() or len(tick_buffer) < 2:
            return {k: 0.0 for k in HORIZONS}

        df = tick_buffer.with_columns([
            pl.col("bid").diff().alias("delta_bid"),
            pl.col("ask").diff().alias("delta_ask"),
            pl.col("bid_qty").diff().alias("delta_bid_qty"),
            pl.col("ask_qty").diff().alias("delta_ask_qty"),
        ]).drop_nulls()

        df = df.with_columns([
            pl.when(pl.col("delta_bid") > 0)
              .then(pl.col("bid_qty"))
              .when(pl.col("delta_bid") < 0)
              .then(pl.col("bid_qty") * -1)
              .otherwise(
                  pl.when(pl.col("delta_bid_qty") > 0).then(pl.col("bid_qty")).otherwise(pl.lit(0))
              ).alias("bid_flow"),

            pl.when(pl.col("delta_ask") > 0)
              .then(pl.col("ask_qty") * -1)
              .when(pl.col("delta_ask") < 0)
              .then(pl.col("ask_qty"))
              .otherwise(
                  pl.when(pl.col("delta_ask_qty") < 0).then(pl.col("ask_qty") * -1).otherwise(pl.lit(0))
              ).alias("ask_flow"),
        ])

        df = df.with_columns(
            (pl.col("bid_flow") - pl.col("ask_flow")).alias("net_flow")
        )

        results = {}
        last_ts = df["timestamp"][-1]

        for key, seconds in HORIZONS.items():
            cutoff = last_ts - pl.duration(seconds=seconds)
            window = df.filter(pl.col("timestamp") >= cutoff)
            if window.is_empty():
                results[key] = 0.0
                continue
            total_vol = window["volume"].sum()
            net = window["net_flow"].sum()
            results[key] = float(net / total_vol) if total_vol > 0 else 0.0

        return results
