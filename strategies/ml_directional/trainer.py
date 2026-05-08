import math
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
import numpy as np
import structlog

logger = structlog.get_logger()

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(exist_ok=True)

LGB_PARAMS = {
    "num_leaves": 31,
    "max_depth": 6,
    "learning_rate": 0.05,
    "n_estimators": 500,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_samples": 50,
    "objective": "binary",
    "metric": "binary_logloss",
    "verbosity": -1,
    "random_state": 42,
}


class ModelTrainer:
    def __init__(self, clickhouse_writer, config: dict, feature_registry) -> None:
        self._clickhouse = clickhouse_writer
        self._config = config
        self._registry = feature_registry

    async def prepare_training_data(self, symbol: str, start_date: date, end_date: date) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        feature_sql = f"""
            SELECT timestamp, feature_name, value
            FROM trading.features
            WHERE symbol = '{symbol}'
              AND toDate(timestamp) >= '{start_date}'
              AND toDate(timestamp) <= '{end_date}'
            ORDER BY timestamp, feature_name
        """
        price_sql = f"""
            SELECT timestamp, last_price
            FROM trading.ticks
            WHERE symbol = '{symbol}'
              AND toDate(timestamp) >= '{start_date}'
              AND toDate(timestamp) <= '{end_date}'
            ORDER BY timestamp
        """

        try:
            feature_rows = await self._clickhouse.query(feature_sql)
            price_rows = await self._clickhouse.query(price_sql)
        except Exception as e:
            logger.error("trainer_data_fetch_failed", symbol=symbol, error=str(e))
            return np.array([]), np.array([]), []

        if not feature_rows or not price_rows:
            return np.array([]), np.array([]), []

        import polars as pl
        feat_df = pl.DataFrame(feature_rows)
        price_df = pl.DataFrame(price_rows)

        feat_wide = feat_df.pivot(values="value", index="timestamp", on="feature_name", aggregate_function="mean")
        feature_cols = [c for c in feat_wide.columns if c != "timestamp"]

        price_df = price_df.with_columns([
            pl.col("last_price").shift(-60).alias("future_price")
        ])
        price_df = price_df.with_columns([
            pl.when(pl.col("future_price") > pl.col("last_price")).then(pl.lit(1))
             .when(pl.col("future_price") < pl.col("last_price")).then(pl.lit(0))
             .otherwise(pl.lit(None)).alias("target")
        ]).drop_nulls(subset=["target"])

        merged = feat_wide.join(
            price_df.select(["timestamp", "target"]),
            on="timestamp",
            how="inner"
        ).drop_nulls()

        if merged.is_empty():
            return np.array([]), np.array([]), []

        X = merged.select(feature_cols).to_numpy().astype(np.float32)
        y = merged["target"].to_numpy().astype(np.int32)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        return X, y, feature_cols

    def train(self, X: np.ndarray, y: np.ndarray, feature_names: List[str]):
        try:
            import lightgbm as lgb
        except ImportError:
            logger.error("lightgbm_not_installed")
            return None

        if len(X) == 0:
            return None

        gap_size = min(300, len(X) // 10)
        n_splits = 5
        fold_size = len(X) // n_splits
        cv_scores = []

        for fold in range(n_splits):
            val_start = fold * fold_size
            val_end = val_start + fold_size
            train_idx = list(range(0, max(0, val_start - gap_size))) + list(range(min(len(X), val_end + gap_size), len(X)))
            val_idx = list(range(val_start, val_end))
            if not train_idx or not val_idx:
                continue
            X_tr, y_tr = X[train_idx], y[train_idx]
            X_val, y_val = X[val_idx], y[val_idx]

            model = lgb.LGBMClassifier(**LGB_PARAMS)
            model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)])
            preds = model.predict_proba(X_val)[:, 1]
            from sklearn.metrics import roc_auc_score
            try:
                auc = roc_auc_score(y_val, preds)
                cv_scores.append(auc)
            except Exception:
                pass

        logger.info("lgb_cv_complete", symbol="?", cv_auc=round(float(np.mean(cv_scores)), 4) if cv_scores else 0)

        final_model = lgb.LGBMClassifier(**LGB_PARAMS)
        final_model.fit(X, y)

        importances = final_model.feature_importances_
        top_20 = sorted(zip(feature_names, importances), key=lambda x: -x[1])[:20]
        logger.info("top_features", features=[(n, int(v)) for n, v in top_20])

        return final_model

    async def convert_to_onnx(self, model, feature_names: List[str], symbol: str) -> str:
        try:
            from skl2onnx import convert_sklearn
            from skl2onnx.common.data_types import FloatTensorType
        except ImportError:
            logger.error("skl2onnx_not_installed")
            return ""

        n_features = len(feature_names)
        initial_type = [("float_input", FloatTensorType([None, n_features]))]
        try:
            onnx_model = convert_sklearn(model, initial_types=initial_type, target_opset=12)
            path = MODELS_DIR / f"{symbol}_directional_{date.today()}.onnx"
            with open(path, "wb") as f:
                f.write(onnx_model.SerializeToString())
            logger.info("onnx_model_saved", symbol=symbol, path=str(path))
            return str(path)
        except Exception as e:
            logger.error("onnx_conversion_failed", error=str(e))
            return ""

    def compute_dsr(self, sharpe: float, n_trials: int, skewness: float, kurtosis: float, n_observations: int) -> float:
        from scipy.stats import norm
        euler_gamma = 0.5772156649
        e = math.e
        if n_trials <= 1:
            return 1.0
        e_max_sr = (
            (1 - euler_gamma) * norm.ppf(1 - 1 / n_trials) +
            euler_gamma * norm.ppf(1 - 1 / (n_trials * e))
        )
        var_sr = (1 + 0.5 * sharpe ** 2 - skewness * sharpe + (kurtosis - 3) / 4 * sharpe ** 2) / max(n_observations - 1, 1)
        if var_sr <= 0:
            return 0.0
        return float(norm.cdf((sharpe - e_max_sr) / math.sqrt(var_sr)))

    async def run_walk_forward(self, symbol: str, total_days: int = 365) -> None:
        train_window = 120
        test_window = 30
        step = 30
        end = date.today()
        start = end - timedelta(days=total_days)

        current = start
        fold = 0
        while current + timedelta(days=train_window + test_window) <= end:
            fold += 1
            train_start = current
            train_end = current + timedelta(days=train_window)
            test_start = train_end
            test_end = train_end + timedelta(days=test_window)

            X, y, feature_names = await self.prepare_training_data(symbol, train_start, train_end)
            if len(X) < 100:
                current += timedelta(days=step)
                continue

            model = self.train(X, y, feature_names)
            if model is None:
                current += timedelta(days=step)
                continue

            X_test, y_test, _ = await self.prepare_training_data(symbol, test_start, test_end)
            if len(X_test) > 0:
                preds = model.predict(X_test)
                returns = np.where(preds == y_test, 1.0, -1.0) * 0.001
                sharpe = float(np.mean(returns) / (np.std(returns) + 1e-10) * math.sqrt(252))
                dsr = self.compute_dsr(sharpe, fold, float(np.mean(returns ** 3) / (np.std(returns) ** 3 + 1e-10)), float(np.mean(returns ** 4) / (np.std(returns) ** 4 + 1e-10)), len(returns))
                logger.info("walk_forward_fold", fold=fold, symbol=symbol, sharpe=round(sharpe, 3), dsr=round(dsr, 3))
                if dsr > 0.5:
                    await self.convert_to_onnx(model, feature_names, symbol)

            current += timedelta(days=step)
