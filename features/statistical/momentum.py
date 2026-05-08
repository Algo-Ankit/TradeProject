from typing import Dict
import numpy as np
import polars as pl

from features.base import BaseFeature


class MomentumFeature(BaseFeature):
    @property
    def name(self) -> str:
        return "momentum"

    @property
    def horizon_seconds(self) -> float:
        return 300.0

    @property
    def required_history_ticks(self) -> int:
        return 30

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if len(tick_buffer) < 2:
            return self._zeros()

        last_price = float(tick_buffer["last_price"][-1])
        last_ts = tick_buffer["timestamp"][-1]

        results = {}
        for key, seconds in [("return_1s", 1), ("return_5s", 5), ("return_30s", 30), ("return_1m", 60), ("return_5m", 300)]:
            window = self._window_df(tick_buffer, seconds)
            if len(window) < 2:
                results[key] = 0.0
            else:
                first_price = float(window["last_price"][0])
                if first_price > 0:
                    results[key] = float(np.log(last_price / first_price))
                else:
                    results[key] = 0.0

        for horizon_key, return_key, window_s in [
            ("momentum_zscore_1m", "return_1m", 1800),
            ("momentum_zscore_5m", "return_5m", 9000),
        ]:
            window = self._window_df(tick_buffer, window_s)
            if len(window) < 30:
                results[horizon_key] = 0.0
            else:
                prices = window["last_price"].to_numpy()
                period = 60 if "1m" in return_key else 300
                n = int(window_s / period)
                rolling_returns = []
                for i in range(n, len(prices)):
                    base = prices[max(0, i - n)]
                    if base > 0:
                        rolling_returns.append(np.log(prices[i] / base))
                if len(rolling_returns) < 2:
                    results[horizon_key] = 0.0
                else:
                    arr = np.array(rolling_returns)
                    std = arr.std()
                    results[horizon_key] = float(arr[-1] / std) if std > 1e-10 else 0.0

        if len(tick_buffer) >= 10:
            w5 = self._window_df(tick_buffer, 10)
            w5_prev = self._window_df(tick_buffer[:-5] if len(tick_buffer) > 5 else tick_buffer, 10)
            current_5s = float(np.log(float(tick_buffer["last_price"][-1]) / float(w5["last_price"][0]))) if len(w5) > 1 and float(w5["last_price"][0]) > 0 else 0.0
            prev_5s = float(np.log(float(tick_buffer["last_price"][-5]) / float(w5_prev["last_price"][0]))) if len(w5_prev) > 1 and len(tick_buffer) > 5 and float(w5_prev["last_price"][0]) > 0 else 0.0
            results["price_acceleration"] = current_5s - prev_5s
        else:
            results["price_acceleration"] = 0.0

        return results

    def _zeros(self) -> Dict[str, float]:
        return {
            "return_1s": 0.0, "return_5s": 0.0, "return_30s": 0.0,
            "return_1m": 0.0, "return_5m": 0.0,
            "momentum_zscore_1m": 0.0, "momentum_zscore_5m": 0.0,
            "price_acceleration": 0.0,
        }
