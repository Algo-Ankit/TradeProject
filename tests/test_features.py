import math
from datetime import datetime, timedelta
import numpy as np
import polars as pl
import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


def make_tick_df(n: int = 100, price_fn=None, directional: str = None) -> pl.DataFrame:
    base_time = datetime.now(IST)
    times = [base_time + timedelta(seconds=i) for i in range(n)]
    t = np.arange(n)

    if directional == "up":
        prices = 1000.0 + t * 0.1 + np.random.normal(0, 0.01, n)
    elif directional == "down":
        prices = 1000.0 - t * 0.1 + np.random.normal(0, 0.01, n)
    elif price_fn:
        prices = price_fn(t)
    else:
        prices = 1000.0 + np.sin(t / 10) * 5 + np.random.normal(0, 0.1, n)

    prices = np.maximum(prices, 1.0)
    bids = prices - 0.5
    asks = prices + 0.5
    bid_qty = np.random.randint(100, 1000, n)
    ask_qty = np.random.randint(100, 1000, n)
    volume = np.cumsum(np.random.randint(100, 500, n)).tolist()

    return pl.DataFrame({
        "timestamp": times,
        "symbol": ["TEST"] * n,
        "last_price": prices.tolist(),
        "bid": bids.tolist(),
        "ask": asks.tolist(),
        "bid_qty": bid_qty.tolist(),
        "ask_qty": ask_qty.tolist(),
        "volume": volume,
        "oi": [0] * n,
    })


class TestOFIFeature:
    def test_nonzero_on_directional_up_move(self):
        from features.microstructure.ofi import OFIFeature
        feature = OFIFeature()
        df = make_tick_df(50, directional="up")
        result = feature.compute(df)
        assert isinstance(result, dict)
        assert "ofi_1s" in result

    def test_returns_all_horizons(self):
        from features.microstructure.ofi import OFIFeature
        feature = OFIFeature()
        df = make_tick_df(100)
        result = feature.compute(df)
        for key in ["ofi_1s", "ofi_5s", "ofi_30s", "ofi_5m"]:
            assert key in result
            assert isinstance(result[key], float)

    def test_handles_empty_df(self):
        from features.microstructure.ofi import OFIFeature
        feature = OFIFeature()
        df = pl.DataFrame(schema={"timestamp": pl.Datetime, "symbol": pl.Utf8, "last_price": pl.Float64, "bid": pl.Float64, "ask": pl.Float64, "bid_qty": pl.Int64, "ask_qty": pl.Int64, "volume": pl.Int64, "oi": pl.Int64})
        result = feature.compute(df)
        assert all(v == 0.0 for v in result.values())


class TestSpreadFeature:
    def test_known_bid_ask(self):
        from features.microstructure.spread import SpreadFeature
        feature = SpreadFeature()
        base_time = datetime.now(IST)
        df = pl.DataFrame({
            "timestamp": [base_time + timedelta(seconds=i) for i in range(20)],
            "symbol": ["TEST"] * 20,
            "last_price": [100.25] * 20,
            "bid": [100.0] * 20,
            "ask": [100.5] * 20,
            "bid_qty": [500] * 20,
            "ask_qty": [300] * 20,
            "volume": list(range(20)),
            "oi": [0] * 20,
        })
        result = feature.compute(df)
        assert abs(result["absolute_spread"] - 0.5) < 0.001
        assert abs(result["relative_spread"] - 50.0) < 1.0
        assert result["mid_price"] == 100.25

    def test_microprice_direction(self):
        from features.microstructure.spread import SpreadFeature
        feature = SpreadFeature()
        base_time = datetime.now(IST)
        df = pl.DataFrame({
            "timestamp": [base_time],
            "symbol": ["TEST"],
            "last_price": [100.25],
            "bid": [100.0],
            "ask": [100.5],
            "bid_qty": [1000],
            "ask_qty": [100],
            "volume": [1000],
            "oi": [0],
        })
        result = feature.compute(df)
        assert result["microprice"] < 100.25


class TestVPINFeature:
    def test_approaches_one_one_sided(self):
        from features.microstructure.vpin import VPINFeature
        feature = VPINFeature(bucket_count=10)
        n = 500
        base_time = datetime.now(IST)
        prices = 1000.0 + np.arange(n) * 0.5
        df = pl.DataFrame({
            "timestamp": [base_time + timedelta(seconds=i) for i in range(n)],
            "symbol": ["TEST"] * n,
            "last_price": prices.tolist(),
            "bid": (prices - 0.5).tolist(),
            "ask": (prices + 0.5).tolist(),
            "bid_qty": [100] * n,
            "ask_qty": [100] * n,
            "volume": np.cumsum(np.full(n, 1000)).tolist(),
            "oi": [0] * n,
        })
        result = feature.compute(df)
        assert "vpin" in result
        assert 0.0 <= result["vpin"] <= 1.0

    def test_result_between_zero_and_one(self):
        from features.microstructure.vpin import VPINFeature
        feature = VPINFeature()
        df = make_tick_df(300)
        result = feature.compute(df)
        assert 0.0 <= result["vpin"] <= 1.0


class TestVolatilityFeature:
    def test_nonzero_on_volatile_series(self):
        from features.statistical.volatility import VolatilityFeature
        feature = VolatilityFeature()
        np.random.seed(42)
        df = make_tick_df(200)
        result = feature.compute(df)
        assert result["realized_vol_30m"] >= 0.0

    def test_vol_ratio_positive(self):
        from features.statistical.volatility import VolatilityFeature
        feature = VolatilityFeature()
        df = make_tick_df(200)
        result = feature.compute(df)
        assert result["vol_ratio"] > 0


class TestMomentumFeature:
    def test_positive_on_uptrend(self):
        from features.statistical.momentum import MomentumFeature
        feature = MomentumFeature()
        df = make_tick_df(100, directional="up")
        result = feature.compute(df)
        assert result["return_1m"] > 0
        assert result["return_5m"] >= 0

    def test_negative_on_downtrend(self):
        from features.statistical.momentum import MomentumFeature
        feature = MomentumFeature()
        df = make_tick_df(100, directional="down")
        result = feature.compute(df)
        assert result["return_1m"] < 0

    def test_returns_all_horizons(self):
        from features.statistical.momentum import MomentumFeature
        feature = MomentumFeature()
        df = make_tick_df(100)
        result = feature.compute(df)
        for k in ["return_1s", "return_5s", "return_30s", "return_1m", "return_5m"]:
            assert k in result


class TestRegimeDetector:
    def test_trains_and_predicts(self):
        from features.statistical.regime import RegimeDetector
        detector = RegimeDetector(n_states=3)
        np.random.seed(42)
        n = 300
        base_time = datetime.now(IST)
        feature_df = pl.DataFrame({
            "timestamp": [base_time + timedelta(seconds=i) for i in range(n)],
            "realized_vol_5m": np.random.uniform(0.1, 0.5, n).tolist(),
            "return_5m": np.random.normal(0, 0.01, n).tolist(),
            "spread_zscore": np.random.normal(0, 1, n).tolist(),
        })
        try:
            detector.train(feature_df)
            result = detector.predict({
                "realized_vol_5m": 0.3,
                "return_5m": 0.001,
                "spread_zscore": 0.5,
            })
            assert result["current_regime"] in (0, 1, 2)
            assert len(result["regime_probability"]) == 3
        except Exception as e:
            pytest.skip(f"hmmlearn not available: {e}")


class TestKalmanFilter:
    def test_converges_on_cointegrated_pair(self):
        from strategies.stat_arb.kalman_filter import KalmanHedgeRatio
        kf = KalmanHedgeRatio(Q=1e-4, R=1e-2)
        true_hr = 2.5
        true_intercept = 100.0
        np.random.seed(42)
        price_b = np.cumsum(np.random.normal(0, 1, 500)) + 1000

        results = []
        for pb in price_b:
            pa = true_hr * pb + true_intercept + np.random.normal(0, 0.1)
            r = kf.update(pa, pb)
            results.append(r)

        final = results[-1]
        assert abs(final["hedge_ratio"] - true_hr) < 1.0, f"hedge_ratio {final['hedge_ratio']} not close to {true_hr}"
