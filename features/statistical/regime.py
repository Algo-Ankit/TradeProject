import os
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import polars as pl
import structlog

from features.base import BaseFeature

logger = structlog.get_logger()

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(exist_ok=True)

STATE_NAMES = {0: "TRENDING", 1: "MEAN_REVERTING", 2: "CHOPPY"}


class RegimeDetector:
    def __init__(self, n_states: int = 3) -> None:
        self._n_states = n_states
        self._model = None
        self._is_trained = False

    def train(self, feature_history: pl.DataFrame) -> None:
        try:
            from hmmlearn import hmm
        except ImportError:
            logger.error("hmmlearn_not_installed")
            return

        required = ["realized_vol_5m", "return_5m", "spread_zscore"]
        available = [c for c in required if c in feature_history.columns]
        if not available:
            logger.warning("regime_train_no_features")
            return

        X = feature_history.select(available).to_numpy()
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        self._model = hmm.GaussianHMM(
            n_components=self._n_states,
            covariance_type="full",
            n_iter=100,
            random_state=42,
        )
        try:
            self._model.fit(X)
            self._is_trained = True
            self._save_model()
            logger.info("regime_detector_trained", samples=len(X))
        except Exception as e:
            logger.error("regime_train_failed", error=str(e))

    def predict(self, current_features: Dict[str, float]) -> Dict:
        if not self._is_trained or self._model is None:
            return {"current_regime": 0, "regime_name": "TRENDING", "regime_probability": [1.0, 0.0, 0.0]}

        features = [
            current_features.get("realized_vol_5m", 0.0),
            current_features.get("return_5m", 0.0),
            current_features.get("spread_zscore", 0.0),
        ]
        X = np.array([features])
        try:
            state = int(self._model.predict(X)[0])
            probs = self._model.predict_proba(X)[0].tolist()
            return {
                "current_regime": state,
                "regime_name": STATE_NAMES.get(state, "UNKNOWN"),
                "regime_probability": probs,
            }
        except Exception as e:
            logger.warning("regime_predict_failed", error=str(e))
            return {"current_regime": 0, "regime_name": "TRENDING", "regime_probability": [1.0, 0.0, 0.0]}

    def _save_model(self) -> None:
        import joblib
        path = MODELS_DIR / f"regime_hmm_{date.today()}.joblib"
        joblib.dump(self._model, path)
        logger.info("regime_model_saved", path=str(path))

    def load_latest_model(self) -> bool:
        import joblib
        models = sorted(MODELS_DIR.glob("regime_hmm_*.joblib"), reverse=True)
        if not models:
            return False
        try:
            self._model = joblib.load(models[0])
            self._is_trained = True
            logger.info("regime_model_loaded", path=str(models[0]))
            return True
        except Exception as e:
            logger.error("regime_model_load_failed", error=str(e))
            return False


_detector = RegimeDetector()


class RegimeFeature(BaseFeature):
    def __init__(self, detector: Optional[RegimeDetector] = None) -> None:
        self._detector = detector or _detector

    @property
    def name(self) -> str:
        return "regime"

    @property
    def horizon_seconds(self) -> float:
        return 300.0

    @property
    def required_history_ticks(self) -> int:
        return 100

    def compute(self, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        features = {
            "realized_vol_5m": 0.0,
            "return_5m": 0.0,
            "spread_zscore": 0.0,
        }
        result = self._detector.predict(features)
        probs = result.get("regime_probability", [1.0, 0.0, 0.0])
        while len(probs) < 3:
            probs.append(0.0)
        return {
            "regime_0_prob": probs[0],
            "regime_1_prob": probs[1],
            "regime_2_prob": probs[2],
            "current_regime": float(result.get("current_regime", 0)),
        }
