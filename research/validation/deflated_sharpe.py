import math
from typing import Dict
import numpy as np
from scipy.stats import norm

EULER_GAMMA = 0.5772156649


def deflated_sharpe_ratio(
    observed_sharpe: float = None,
    n_trials: int = 1,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    n_observations: int = 252,
    *,
    sharpe: float = None,
) -> float:
    """
    Deflated Sharpe Ratio (López de Prado 2014).
    Adjusts observed Sharpe for multiple testing, skewness, and kurtosis.
    Returns probability that the strategy is NOT a false discovery.
    DSR > 0.5 is the acceptance threshold.
    """
    if sharpe is not None:
        observed_sharpe = sharpe
    if observed_sharpe is None:
        observed_sharpe = 0.0

    if n_trials <= 1:
        return 1.0
    if n_observations <= 1:
        return 0.0

    e = math.e
    e_max_sr = (
        (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials) +
        EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * e))
    )

    sr = observed_sharpe
    var_sr = (
        1 + 0.5 * sr ** 2 - skewness * sr + (kurtosis - 3) / 4 * sr ** 2
    ) / max(n_observations - 1, 1)

    if var_sr <= 0:
        return 0.0

    dsr = float(norm.cdf((sr - e_max_sr) / math.sqrt(var_sr)))
    return dsr


def compute_strategy_stats(returns: np.ndarray) -> Dict[str, float]:
    """Compute statistics needed for DSR from a daily return array."""
    if len(returns) < 2:
        return {"sharpe": 0.0, "skewness": 0.0, "kurtosis": 3.0, "n_observations": len(returns)}

    mean = float(np.mean(returns))
    std = float(np.std(returns))
    if std == 0:
        return {"sharpe": 0.0, "skewness": 0.0, "kurtosis": 3.0, "n_observations": len(returns)}

    sharpe = mean / std * math.sqrt(252)
    skewness = float(np.mean(((returns - mean) / std) ** 3))
    kurtosis = float(np.mean(((returns - mean) / std) ** 4))

    return {
        "sharpe": sharpe,
        "skewness": skewness,
        "kurtosis": kurtosis,
        "n_observations": len(returns),
    }
