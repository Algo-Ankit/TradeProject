from typing import Dict
import polars as pl

from features.base import BaseFeature


class DepthFeature(BaseFeature):
    @property
    def name(self) -> str:
        return "depth"

    @property
    def horizon_seconds(self) -> float:
        return 1.0

    @property
    def required_history_ticks(self) -> int:
        return 1

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        if tick_buffer.is_empty():
            return self._zeros()

        last = tick_buffer[-1]
        cols = tick_buffer.columns

        def _get(col: str, default: float = 0.0) -> float:
            if col in cols:
                v = last[col][0]
                return float(v) if v is not None else default
            return default

        bid_depth = [
            _get("bid_qty_l1") or _get("bid_qty"),
            _get("bid_qty_l2"),
            _get("bid_qty_l3"),
            _get("bid_qty_l4"),
            _get("bid_qty_l5"),
        ]
        ask_depth = [
            _get("ask_qty_l1") or _get("ask_qty"),
            _get("ask_qty_l2"),
            _get("ask_qty_l3"),
            _get("ask_qty_l4"),
            _get("ask_qty_l5"),
        ]

        if "order_book" in cols:
            ob = last["order_book"][0]
            if ob and isinstance(ob, dict):
                buys = ob.get("buy", [])
                sells = ob.get("sell", [])
                bid_depth = [float(b.get("quantity", 0)) for b in buys[:5]]
                ask_depth = [float(s.get("quantity", 0)) for s in sells[:5]]
                while len(bid_depth) < 5:
                    bid_depth.append(0.0)
                while len(ask_depth) < 5:
                    ask_depth.append(0.0)

        bid_total_5 = sum(bid_depth)
        ask_total_5 = sum(ask_depth)
        total_5 = bid_total_5 + ask_total_5

        depth_imbalance = (bid_total_5 - ask_total_5) / total_5 if total_5 > 0 else 0.0

        bid_l1 = bid_depth[0]
        ask_l1 = ask_depth[0]
        bid_l3 = sum(bid_depth[:3])
        ask_l3 = sum(ask_depth[:3])

        return {
            "depth_imbalance": depth_imbalance,
            "bid_ask_ratio_l1": bid_l1 / ask_l1 if ask_l1 > 0 else 1.0,
            "bid_ask_ratio_l3": bid_l3 / ask_l3 if ask_l3 > 0 else 1.0,
            "bid_ask_ratio_l5": bid_total_5 / ask_total_5 if ask_total_5 > 0 else 1.0,
        }

    def _zeros(self) -> Dict[str, float]:
        return {
            "depth_imbalance": 0.0,
            "bid_ask_ratio_l1": 1.0,
            "bid_ask_ratio_l3": 1.0,
            "bid_ask_ratio_l5": 1.0,
        }
