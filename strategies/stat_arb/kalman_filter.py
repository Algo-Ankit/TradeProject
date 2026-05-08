from collections import deque
from typing import Dict
import numpy as np


class KalmanHedgeRatio:
    def __init__(self, Q: float = 1e-5, R: float = 1e-3) -> None:
        self._Q = np.eye(2) * Q
        self._R = R
        self._P = np.eye(2) * 1.0
        self._x = np.array([1.0, 0.0])
        self._spread_history: deque = deque(maxlen=1000)

    @property
    def hedge_ratio(self) -> float:
        return float(self._x[0])

    @property
    def intercept(self) -> float:
        return float(self._x[1])

    def update(self, price_a: float, price_b: float) -> Dict[str, float]:
        H = np.array([[price_b, 1.0]])

        P_pred = self._P + self._Q

        S = H @ P_pred @ H.T + self._R
        K = (P_pred @ H.T) / S[0, 0]

        y_hat = float((H @ self._x).item())
        innovation = price_a - y_hat

        self._x = self._x + K.flatten() * innovation
        I = np.eye(2)
        self._P = (I - K @ H) @ P_pred

        spread = innovation
        spread_variance = float(S[0, 0])
        self._spread_history.append(spread)

        spread_mean = float(np.mean(self._spread_history))
        spread_std = float(np.std(self._spread_history))
        spread_zscore = (spread - spread_mean) / spread_std if spread_std > 1e-10 else 0.0

        return {
            "hedge_ratio": float(self._x[0]),
            "intercept": float(self._x[1]),
            "spread": spread,
            "spread_variance": spread_variance,
            "spread_zscore": spread_zscore,
            "spread_mean": spread_mean,
            "spread_std": spread_std,
        }

    def reset(self) -> None:
        self._P = np.eye(2) * 1.0
        self._x = np.array([1.0, 0.0])
        self._spread_history.clear()

    @property
    def n_observations(self) -> int:
        return len(self._spread_history)
