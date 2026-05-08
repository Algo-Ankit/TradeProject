from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import numpy as np
import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


class TestKalmanHedgeRatio:
    def test_converges_on_cointegrated_pair(self):
        from strategies.stat_arb.kalman_filter import KalmanHedgeRatio
        kf = KalmanHedgeRatio(Q=1e-4, R=1e-2)
        true_hr = 2.5
        np.random.seed(42)
        price_b = np.cumsum(np.random.normal(0, 1, 500)) + 1000
        for pb in price_b:
            pa = true_hr * pb + 100 + np.random.normal(0, 0.1)
            kf.update(pa, pb)
        assert abs(kf.hedge_ratio - true_hr) < 1.0

    def test_returns_expected_keys(self):
        from strategies.stat_arb.kalman_filter import KalmanHedgeRatio
        kf = KalmanHedgeRatio()
        result = kf.update(1000.0, 500.0)
        for key in ["hedge_ratio", "intercept", "spread", "spread_variance", "spread_zscore"]:
            assert key in result

    def test_spread_zscore_stabilizes(self):
        from strategies.stat_arb.kalman_filter import KalmanHedgeRatio
        kf = KalmanHedgeRatio()
        np.random.seed(0)
        for i in range(100):
            pb = 1000.0 + np.random.normal()
            pa = 2.0 * pb + 50 + np.random.normal() * 0.1
            result = kf.update(pa, pb)
        assert abs(result["spread_zscore"]) < 10


class TestSpreadTraderSignals:
    def _make_strategy(self):
        redis_mock = AsyncMock()
        redis_mock.get_feature = AsyncMock(return_value=None)
        redis_mock.get_all_positions = AsyncMock(return_value={})
        redis_mock.get_position = AsyncMock(return_value=None)
        redis_mock.update_position = AsyncMock()
        event_bus = AsyncMock()
        event_bus.publish = AsyncMock()

        from strategies.stat_arb.spread_trader import SpreadTrader
        config = {
            "max_position_inr": 500_000,
            "max_open_pairs": 4,
            "entry_zscore": 2.0,
            "exit_zscore": 0.5,
            "stop_zscore": 3.5,
        }
        strategy = SpreadTrader("stat_arb", config, redis_mock, event_bus, MagicMock())
        return strategy

    def _make_tick(self, symbol: str, price: float):
        from core.events import TickEvent
        return TickEvent(
            timestamp=datetime.now(IST),
            source_module="test",
            symbol=symbol,
            exchange="NSE",
            last_price=price,
            bid=price - 0.5,
            ask=price + 0.5,
            bid_qty=1000,
            ask_qty=1000,
            volume=100000,
            oi=0,
        )

    @pytest.mark.asyncio
    async def test_no_signal_without_pairs(self):
        strategy = self._make_strategy()
        tick = self._make_tick("RELIANCE", 2500.0)
        signal = await strategy.on_tick(tick)
        assert signal is None

    @pytest.mark.asyncio
    async def test_entry_at_high_zscore(self):
        from strategies.stat_arb.kalman_filter import KalmanHedgeRatio
        strategy = self._make_strategy()
        pair_key = ("RELIANCE", "TCS")
        kf = KalmanHedgeRatio(Q=0.0)
        np.random.seed(0)
        for _ in range(50):
            kf.update(2500.0, 3500.0 / 1.5)
        strategy._pairs[pair_key] = {
            "kalman": kf,
            "half_life": 5,
            "hedge_ratio": 1.5,
            "spread_variances": [],
        }

        strategy._latest_prices["RELIANCE"] = 2500.0
        strategy._latest_prices["TCS"] = 3500.0 / 1.5
        strategy._latest_ts["RELIANCE"] = datetime.now(IST)
        strategy._latest_ts["TCS"] = datetime.now(IST)

        tick = self._make_tick("RELIANCE", 2500.0 * 1.2)
        strategy._latest_prices["RELIANCE"] = 2500.0 * 1.2

    @pytest.mark.asyncio
    async def test_exit_at_low_zscore(self):
        strategy = self._make_strategy()
        pair_key = ("RELIANCE", "TCS")
        strategy._active_positions[pair_key] = {
            "entry_z": 2.5,
            "entry_time": datetime.now(IST),
            "side_a": "SELL",
            "qty_a": 10,
            "qty_b": 15,
            "kalman_state": {},
        }
        strategy._latest_prices["RELIANCE"] = 2500.0
        strategy._latest_prices["TCS"] = 3500.0

        from strategies.stat_arb.kalman_filter import KalmanHedgeRatio
        kf = KalmanHedgeRatio()
        mock_result = {"hedge_ratio": 1.5, "intercept": 0, "spread": 0.01, "spread_variance": 0.01, "spread_zscore": 0.3}
        signal = await strategy._check_exit(pair_key, 0.3, mock_result)
        assert signal is not None
        assert pair_key not in strategy._active_positions


class TestOptionsGreeks:
    def test_call_delta_range(self):
        from features.options.greeks import OptionsGreeks
        delta = OptionsGreeks.delta(S=100, K=100, T=0.25, r=0.05, sigma=0.2, option_type="call")
        assert 0.4 < delta < 0.7

    def test_put_delta_range(self):
        from features.options.greeks import OptionsGreeks
        delta = OptionsGreeks.delta(S=100, K=100, T=0.25, r=0.05, sigma=0.2, option_type="put")
        assert -0.7 < delta < -0.3

    def test_gamma_positive(self):
        from features.options.greeks import OptionsGreeks
        gamma = OptionsGreeks.gamma(S=100, K=100, T=0.25, r=0.05, sigma=0.2)
        assert gamma > 0

    def test_put_call_parity(self):
        from features.options.greeks import OptionsGreeks
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        call_delta = OptionsGreeks.delta(S, K, T, r, sigma, "call")
        put_delta = OptionsGreeks.delta(S, K, T, r, sigma, "put")
        # call_delta - put_delta = 1 (put_delta is negative by convention)
        assert abs(call_delta - put_delta - 1.0) < 0.01

    def test_implied_vol_roundtrip(self):
        from features.options.greeks import OptionsGreeks
        S, K, T, r = 100, 100, 0.25, 0.05
        true_sigma = 0.25
        price = OptionsGreeks._bs_price(S, K, T, r, true_sigma, "call")
        recovered_iv = OptionsGreeks.implied_vol(price, S, K, T, r, "call")
        assert abs(recovered_iv - true_sigma) < 0.001


class TestDeflatedSharpe:
    def test_high_sharpe_high_dsr(self):
        from research.validation.deflated_sharpe import deflated_sharpe_ratio
        dsr = deflated_sharpe_ratio(sharpe=3.0, n_trials=10, skewness=0, kurtosis=3, n_observations=252)
        assert dsr > 0.5

    def test_low_sharpe_low_dsr(self):
        from research.validation.deflated_sharpe import deflated_sharpe_ratio
        dsr = deflated_sharpe_ratio(sharpe=0.3, n_trials=100, skewness=0, kurtosis=3, n_observations=252)
        assert dsr < 0.5

    def test_dsr_in_zero_one(self):
        from research.validation.deflated_sharpe import deflated_sharpe_ratio
        for sr in [-1, 0, 0.5, 1.0, 2.0]:
            dsr = deflated_sharpe_ratio(sharpe=sr, n_trials=20, skewness=0, kurtosis=3, n_observations=100)
            assert 0.0 <= dsr <= 1.0
