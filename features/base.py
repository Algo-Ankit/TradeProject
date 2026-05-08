from abc import ABC, abstractmethod
from typing import Dict, Union
import polars as pl


class BaseFeature(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def horizon_seconds(self) -> float: ...

    @property
    @abstractmethod
    def required_history_ticks(self) -> int: ...

    @abstractmethod
    def compute(self, tick_buffer: pl.DataFrame) -> Union[float, Dict[str, float]]:
        """
        Compute feature from tick_buffer DataFrame.
        MUST only access data up to the last row — no lookahead.
        tick_buffer columns: timestamp, symbol, last_price, bid, ask, bid_qty, ask_qty, volume, oi
        """
        ...

    def register(self) -> None:
        from features.registry import FeatureRegistry
        FeatureRegistry.get_instance().register(self)

    def _window_df(self, tick_buffer: pl.DataFrame, seconds: float) -> pl.DataFrame:
        if tick_buffer.is_empty():
            return tick_buffer
        last_ts = tick_buffer["timestamp"][-1]
        cutoff = last_ts - pl.duration(seconds=seconds)
        return tick_buffer.filter(pl.col("timestamp") >= cutoff)
