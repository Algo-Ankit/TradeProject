from typing import Dict, List, Optional, Union
import polars as pl
import structlog

from features.base import BaseFeature

logger = structlog.get_logger()


class FeatureRegistry:
    _instance: Optional["FeatureRegistry"] = None

    def __init__(self) -> None:
        self._features: Dict[str, BaseFeature] = {}

    @classmethod
    def get_instance(cls) -> "FeatureRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, feature: BaseFeature) -> None:
        self._features[feature.name] = feature
        logger.debug("feature_registered", name=feature.name)

    def get(self, name: str) -> Optional[BaseFeature]:
        return self._features.get(name)

    def compute_all(self, symbol: str, tick_buffer: pl.DataFrame) -> Dict[str, float]:
        results: Dict[str, float] = {}
        for name, feature in self._features.items():
            if len(tick_buffer) < feature.required_history_ticks:
                continue
            try:
                output = feature.compute(tick_buffer)
                if isinstance(output, dict):
                    results.update(output)
                elif isinstance(output, (int, float)):
                    results[name] = float(output)
            except Exception as e:
                logger.warning("feature_compute_error", feature=name, symbol=symbol, error=str(e))
        return results

    def list_features(self) -> List[Dict]:
        return [
            {
                "name": f.name,
                "horizon_seconds": f.horizon_seconds,
                "required_history_ticks": f.required_history_ticks,
            }
            for f in self._features.values()
        ]
