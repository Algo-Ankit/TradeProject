from typing import List
import numpy as np
import structlog

logger = structlog.get_logger()


class FeatureSelector:
    def __init__(self, importance_threshold: float = 0.0, correlation_threshold: float = 0.95) -> None:
        self._importance_threshold = importance_threshold
        self._corr_threshold = correlation_threshold

    def select(self, X: np.ndarray, y: np.ndarray, feature_names: List[str]) -> List[str]:
        if len(X) == 0 or len(feature_names) == 0:
            return feature_names

        importances = self._compute_importance(X, y)
        if importances is None:
            return feature_names

        nonzero_mask = importances > self._importance_threshold
        selected_names = [n for n, keep in zip(feature_names, nonzero_mask) if keep]
        selected_X = X[:, nonzero_mask]
        removed_zero = len(feature_names) - len(selected_names)
        logger.info("feature_selection_zero_importance", removed=removed_zero)

        if selected_X.shape[1] > 1:
            corr_mask = self._remove_correlated(selected_X, importances[nonzero_mask])
            final_names = [n for n, keep in zip(selected_names, corr_mask) if keep]
            removed_corr = len(selected_names) - len(final_names)
            logger.info("feature_selection_correlated", removed=removed_corr)
        else:
            final_names = selected_names

        logger.info("feature_selection_complete", original=len(feature_names), selected=len(final_names))
        return final_names

    def _compute_importance(self, X: np.ndarray, y: np.ndarray):
        try:
            import lightgbm as lgb
            model = lgb.LGBMClassifier(n_estimators=100, max_depth=4, learning_rate=0.1, verbosity=-1, random_state=42)
            model.fit(X, y)
            return model.feature_importances_.astype(float)
        except Exception as e:
            logger.warning("feature_importance_failed", error=str(e))
            return None

    def _remove_correlated(self, X: np.ndarray, importances: np.ndarray) -> List[bool]:
        n = X.shape[1]
        keep = [True] * n
        try:
            corr = np.corrcoef(X.T)
            for i in range(n):
                if not keep[i]:
                    continue
                for j in range(i + 1, n):
                    if not keep[j]:
                        continue
                    if abs(corr[i, j]) > self._corr_threshold:
                        if importances[i] >= importances[j]:
                            keep[j] = False
                        else:
                            keep[i] = False
                            break
        except Exception as e:
            logger.warning("correlation_removal_failed", error=str(e))
        return keep
