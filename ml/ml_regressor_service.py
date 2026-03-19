"""
ML Regressor Service: Load active regression models and generate predictions.

For each horizon (1h, 3h, 6h), loads the active XGBoost or LightGBM quantile model
and returns predicted_return with lower/upper confidence bounds.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import joblib
import numpy as np
import psycopg2

logger = logging.getLogger("ml_regressor_service")

# 폴백용 피처 컬럼 (artifact에 feature_cols 없을 때만 사용)
_FALLBACK_FEATURE_COLS = [
    "rsi_14", "macd_hist", "bb_pct", "atr_pct", "stoch_k",
    "signal_1h", "signal_4h", "volatility_10", "bb_width",
    "returns_1bar", "returns_4bar", "returns_16bar",
]

HORIZONS = ["1h", "3h", "6h"]


def _get_db_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
    )


def _null_result(horizon: str) -> dict:
    return {
        "horizon": horizon,
        "predicted_return": 0.0,
        "lower_10": 0.0,
        "upper_90": 0.0,
        "confidence_width": 0.0,
        "available": False,
    }


class MLRegressorService:
    """
    Loads active regression models for all horizons.
    Thread-safe for read; call reload() to refresh models.
    """

    def __init__(self) -> None:
        self._models: dict[str, dict] = {}  # horizon → artifact dict
        self._loaded = False

    def _load_models(self) -> None:
        """Load active regression models from DB + disk."""
        try:
            conn = _get_db_conn()
            try:
                self._models = {}
                for horizon in HORIZONS:
                    # Try XGBoost first, then LightGBM
                    for algo in ["xgb", "lgb"]:
                        model_type = f"{algo}_regressor_{horizon}"
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT artifact_path, metrics
                                FROM features.ml_model_registry
                                WHERE model_type = %s AND is_active = TRUE
                                ORDER BY trained_at DESC
                                LIMIT 1
                                """,
                                (model_type,),
                            )
                            row = cur.fetchone()
                        if row is None:
                            continue
                        artifact_path, metrics = row
                        path = Path(artifact_path)
                        if not path.exists():
                            logger.warning("Model artifact not found: %s", path)
                            continue
                        artifact = joblib.load(path)
                        self._models[horizon] = {
                            "artifact": artifact,
                            "algo": algo,
                            "metrics": metrics,
                        }
                        logger.info("Loaded %s for horizon=%s", model_type, horizon)
                        break  # Use first available algo
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to load regression models: %s", exc)

        self._loaded = True

    def reload(self) -> None:
        self._loaded = False
        self._models = {}
        self._load_models()

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_models()

    def predict(self, features: dict) -> dict[str, dict]:
        """
        Given a feature dict, predict return for all horizons.

        features: dict with keys matching FEATURE_COLS
        Returns: {horizon: {predicted_return, lower_10, upper_90, confidence_width, available}}
        """
        self._ensure_loaded()

        results = {}
        for horizon in HORIZONS:
            if horizon not in self._models:
                results[horizon] = _null_result(horizon)
                continue

            try:
                artifact = self._models[horizon]["artifact"]
                scaler = artifact["scaler"]
                models = artifact["models"]  # {0.10: model, 0.50: model, 0.90: model}

                # artifact에 저장된 feature_cols 우선 사용 (없으면 fallback)
                feature_cols = artifact.get("feature_cols", _FALLBACK_FEATURE_COLS)

                # Build feature vector
                feat_vec = np.array(
                    [float(features.get(col, 0.0) or 0.0) for col in feature_cols],
                    dtype=float,
                ).reshape(1, -1)
                feat_scaled = scaler.transform(feat_vec)

                preds = {}
                for q, model in models.items():
                    preds[q] = float(model.predict(feat_scaled)[0])

                median = preds.get(0.50, 0.0)
                lower = preds.get(0.10, median)
                upper = preds.get(0.90, median)
                width = upper - lower

                results[horizon] = {
                    "horizon": horizon,
                    "predicted_return": median,
                    "lower_10": lower,
                    "upper_90": upper,
                    "confidence_width": width,
                    "available": True,
                }
            except Exception as exc:
                logger.warning("Prediction failed for horizon=%s: %s", horizon, exc)
                results[horizon] = _null_result(horizon)

        return results

    def is_available(self, horizon: str = "1h") -> bool:
        self._ensure_loaded()
        return horizon in self._models

    @property
    def loaded_horizons(self) -> list[str]:
        self._ensure_loaded()
        return list(self._models.keys())


# Singleton for use in FastAPI / ensemble service
_service_instance: MLRegressorService | None = None


def get_regressor_service() -> MLRegressorService:
    global _service_instance
    if _service_instance is None:
        _service_instance = MLRegressorService()
    return _service_instance
