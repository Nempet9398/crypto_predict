"""
ML Classifier Service: loads the active model from features.ml_model_registry
and provides inference for the ensemble signal engine.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False


def get_db_conn():
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
    )


def load_active_ml_model() -> Optional[dict]:
    """
    Loads the active ML model artifact from the path stored in
    features.ml_model_registry. Returns the artifact dict or None.
    """
    if not HAS_JOBLIB:
        return None
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_id, model_type, artifact_path, feature_cols, metrics
                FROM features.ml_model_registry
                WHERE is_active = TRUE
                ORDER BY trained_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        model_id, model_type, artifact_path, feature_cols, metrics = row
        if not Path(artifact_path).exists():
            return None
        artifact = joblib.load(artifact_path)
        artifact["model_id"] = model_id
        return artifact
    except Exception:
        return None


def predict_direction(features_row: dict, artifact: Optional[dict] = None) -> dict:
    """
    Given a dict of feature values (one row), returns:
      {
        'ml_direction': int,     # -1, 0, +1
        'prob_up': float,        # probability class=+1
        'prob_neutral': float,   # probability class=0
        'prob_down': float,      # probability class=-1
        'available': bool,       # False if no model is loaded
      }
    """
    fallback = {
        "ml_direction": 0,
        "prob_up": 0.33,
        "prob_neutral": 0.34,
        "prob_down": 0.33,
        "available": False,
    }

    if artifact is None:
        artifact = load_active_ml_model()
    if artifact is None:
        return fallback

    model = artifact["model"]
    scaler = artifact["scaler"]
    feature_cols = artifact["feature_cols"]
    label_map_inv = artifact.get("label_map_inv", {0: -1, 1: 0, 2: 1})

    try:
        x = np.array([[features_row.get(col, 0.0) or 0.0 for col in feature_cols]], dtype=float)
        x_scaled = scaler.transform(x)
        y_pred = model.predict(x_scaled)[0]
        y_prob = model.predict_proba(x_scaled)[0]  # shape (3,) for classes 0,1,2

        # Map sklearn classes back to -1/0/+1
        direction = label_map_inv.get(int(y_pred), 0)

        # y_prob[0] = down, y_prob[1] = neutral, y_prob[2] = up
        prob_down = float(y_prob[0])
        prob_neutral = float(y_prob[1])
        prob_up = float(y_prob[2])

        return {
            "ml_direction": direction,
            "prob_up": prob_up,
            "prob_neutral": prob_neutral,
            "prob_down": prob_down,
            "available": True,
        }
    except Exception:
        return fallback
