import math
from typing import Dict, Optional
import numpy as np
import polars as pl
from scipy.stats import norm
import structlog

from features.base import BaseFeature

logger = structlog.get_logger()


class OptionsGreeks:
    @staticmethod
    def _d1_d2(S: float, K: float, T: float, r: float, sigma: float):
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0, 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2

    @classmethod
    def delta(cls, S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
        d1, _ = cls._d1_d2(S, K, T, r, sigma)
        if option_type.lower() == "call":
            return float(norm.cdf(d1))
        return float(norm.cdf(d1) - 1)

    @classmethod
    def gamma(cls, S: float, K: float, T: float, r: float, sigma: float) -> float:
        d1, _ = cls._d1_d2(S, K, T, r, sigma)
        if T <= 0 or sigma <= 0 or S <= 0:
            return 0.0
        return float(norm.pdf(d1) / (S * sigma * math.sqrt(T)))

    @classmethod
    def vega(cls, S: float, K: float, T: float, r: float, sigma: float) -> float:
        d1, _ = cls._d1_d2(S, K, T, r, sigma)
        if T <= 0:
            return 0.0
        return float(S * norm.pdf(d1) * math.sqrt(T) / 100)

    @classmethod
    def theta(cls, S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
        if T <= 0 or sigma <= 0:
            return 0.0
        d1, d2 = cls._d1_d2(S, K, T, r, sigma)
        term1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
        if option_type.lower() == "call":
            term2 = -r * K * math.exp(-r * T) * norm.cdf(d2)
        else:
            term2 = r * K * math.exp(-r * T) * norm.cdf(-d2)
        return float((term1 + term2) / 365)

    @classmethod
    def rho(cls, S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
        if T <= 0:
            return 0.0
        _, d2 = cls._d1_d2(S, K, T, r, sigma)
        if option_type.lower() == "call":
            return float(K * T * math.exp(-r * T) * norm.cdf(d2) / 100)
        return float(-K * T * math.exp(-r * T) * norm.cdf(-d2) / 100)

    @classmethod
    def implied_vol(cls, option_price: float, S: float, K: float, T: float, r: float, option_type: str = "call") -> float:
        if T <= 0 or option_price <= 0:
            return 0.0
        sigma = 0.2
        for _ in range(100):
            price = cls._bs_price(S, K, T, r, sigma, option_type)
            v = cls.vega(S, K, T, r, sigma) * 100
            if abs(v) < 1e-10:
                break
            sigma -= (price - option_price) / v
            sigma = max(0.001, min(sigma, 10.0))
        return float(sigma)

    @classmethod
    def _bs_price(cls, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        if T <= 0:
            return max(0, S - K) if option_type == "call" else max(0, K - S)
        d1, d2 = cls._d1_d2(S, K, T, r, sigma)
        if option_type.lower() == "call":
            return float(S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2))
        return float(K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


class HestonModel:
    """Placeholder for Heston stochastic volatility model."""

    def calibrate(self, market_prices: list, strikes: list, expiries: list, S: float, r: float) -> dict:
        raise NotImplementedError("Heston calibration not yet implemented")

    def price(self, S: float, K: float, T: float, r: float, params: dict, option_type: str = "call") -> float:
        raise NotImplementedError("Heston pricing not yet implemented")


class GreeksFeature(BaseFeature):
    def __init__(self, redis_manager=None, risk_free_rate: float = 0.065) -> None:
        self._redis = redis_manager
        self._r = risk_free_rate

    @property
    def name(self) -> str:
        return "greeks"

    @property
    def horizon_seconds(self) -> float:
        return 1.0

    @property
    def required_history_ticks(self) -> int:
        return 1

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        return {
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
        }
