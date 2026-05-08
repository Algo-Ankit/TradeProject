from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import structlog

logger = structlog.get_logger()

MODELS_DIR = Path("models")


class MLPredictor:
    def __init__(self, model_path: str, feature_names: List[str]) -> None:
        self._feature_names = feature_names
        self._session = None
        self._input_name: str = ""
        self._model_path = model_path
        self._load(model_path)

    def _load(self, model_path: str) -> None:
        try:
            import onnxruntime as rt
            self._session = rt.InferenceSession(model_path)
            self._input_name = self._session.get_inputs()[0].name
            dummy = np.zeros((1, len(self._feature_names)), dtype=np.float32)
            self._session.run(None, {self._input_name: dummy})
            logger.info("onnx_model_loaded", path=model_path, features=len(self._feature_names))
        except Exception as e:
            logger.error("onnx_model_load_failed", path=model_path, error=str(e))

    def predict(self, features: Dict[str, float]) -> Dict:
        if self._session is None:
            return {"prediction": 0, "probability": 0.5}

        X = np.array(
            [features.get(f, 0.0) for f in self._feature_names],
            dtype=np.float32
        ).reshape(1, -1)
        X = np.nan_to_num(X)

        try:
            outputs = self._session.run(None, {self._input_name: X})
            if len(outputs) >= 2:
                proba = outputs[1][0]
                if hasattr(proba, '__len__') and len(proba) >= 2:
                    prob_positive = float(proba[1])
                else:
                    prob_positive = float(proba)
            else:
                raw = float(outputs[0][0])
                prob_positive = 1.0 / (1.0 + np.exp(-raw))

            prediction = 1 if prob_positive >= 0.5 else -1
            confidence = abs(prob_positive - 0.5) * 2
            return {"prediction": prediction, "probability": confidence, "prob_positive": prob_positive}
        except Exception as e:
            logger.warning("onnx_inference_failed", error=str(e))
            return {"prediction": 0, "probability": 0.5, "prob_positive": 0.5}

    async def predict_from_redis(self, symbol: str, redis_manager) -> Dict:
        features = await redis_manager.get_all_features(symbol)
        result = self.predict(features)
        result["symbol"] = symbol
        result["timestamp"] = datetime.now().isoformat()
        return result

    @classmethod
    def load_latest_model(cls, symbol: str, models_dir: str = "models") -> Optional["MLPredictor"]:
        import json
        models = sorted(Path(models_dir).glob(f"{symbol}_directional_*.onnx"), reverse=True)
        if not models:
            logger.warning("no_onnx_model_found", symbol=symbol)
            return None
        meta_path = models[0].with_suffix(".json")
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
            feature_names = meta.get("feature_names", [])
        else:
            feature_names = []
        return cls(str(models[0]), feature_names)
