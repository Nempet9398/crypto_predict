"""
ML Predictor Service
XGBoost/LightGBM 기반 시계열 예측 모델 로드 및 추론.
"""
import json
import os
import pickle
from typing import Optional

import numpy as np

MODEL_REGISTRY_DIR = os.getenv("MODEL_REGISTRY_DIR", os.path.join(os.path.dirname(__file__), "model_registry"))
PREDICTOR_MODEL_PATH = os.path.join(MODEL_REGISTRY_DIR, "eth_predictor.pkl")
PREDICTOR_META_PATH = os.path.join(MODEL_REGISTRY_DIR, "eth_predictor_meta.json")


def load_active_ml_model() -> Optional[dict]:
    """모델 파일 로드. 없으면 None 반환."""
    if not os.path.exists(PREDICTOR_MODEL_PATH):
        return None
    try:
        with open(PREDICTOR_MODEL_PATH, "rb") as f:
            model = pickle.load(f)
        meta = {}
        if os.path.exists(PREDICTOR_META_PATH):
            with open(PREDICTOR_META_PATH, "r") as f:
                meta = json.load(f)
        return {"model": model, "meta": meta}
    except Exception:
        return None


# 폴백용 피처 컬럼 (학습 시 저장된 feature_cols 없을 때만 사용)
_FALLBACK_FEATURE_COLS = [
    "returns_1bar", "returns_4bar", "returns_16bar",
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "bb_pct", "bb_width",
    "atr_pct",
    "stoch_k", "stoch_d",
    "volume_ratio",
    "signal_1h", "signal_4h",
    "volatility_10",
]


def _build_feature_vector(row: dict, feature_cols: Optional[list] = None) -> Optional[np.ndarray]:
    """features_row dict에서 모델 입력 벡터 생성. artifact의 feature_cols 우선 사용."""
    cols = feature_cols if feature_cols else _FALLBACK_FEATURE_COLS
    values = []
    for col in cols:
        v = row.get(col)
        if v is None:
            values.append(0.0)
        else:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(0.0)
    return np.array(values, dtype=np.float32).reshape(1, -1)


def predict(features_row: dict, artifact: Optional[dict] = None) -> dict:
    """
    주어진 features_row에 대해 방향 및 확률 예측.
    Returns: {"direction": int, "prob_up": float, "prob_down": float, "available": bool}
    """
    if artifact is None:
        artifact = load_active_ml_model()

    if artifact is None:
        return {"direction": 0, "prob_up": 0.333, "prob_down": 0.333, "available": False}

    try:
        model = artifact["model"]
        # 학습 시 저장된 feature_cols 사용 (없으면 fallback)
        feature_cols = artifact.get("meta", {}).get("feature_cols")
        X = _build_feature_vector(features_row, feature_cols)

        if hasattr(model, "predict_proba"):
            # 분류 모델: classes = [-1, 0, 1]
            proba = model.predict_proba(X)[0]
            classes = list(model.classes_)
            prob_down = float(proba[classes.index(-1)]) if -1 in classes else 0.0
            prob_neutral = float(proba[classes.index(0)]) if 0 in classes else 0.0
            prob_up = float(proba[classes.index(1)]) if 1 in classes else 0.0

            if prob_up > prob_down and prob_up > prob_neutral:
                direction = 1
            elif prob_down > prob_up and prob_down > prob_neutral:
                direction = -1
            else:
                direction = 0

            return {
                "direction": direction,
                "prob_up": round(prob_up, 4),
                "prob_down": round(prob_down, 4),
                "prob_neutral": round(prob_neutral, 4),
                "available": True,
            }
        else:
            # 회귀 모델: 예측 수익률로 방향 추정
            pred_return = float(model.predict(X)[0])
            threshold = 0.003  # 0.3%
            if pred_return > threshold:
                direction, prob_up, prob_down = 1, 0.65, 0.20
            elif pred_return < -threshold:
                direction, prob_up, prob_down = -1, 0.20, 0.65
            else:
                direction, prob_up, prob_down = 0, 0.333, 0.333

            return {
                "direction": direction,
                "prob_up": prob_up,
                "prob_down": prob_down,
                "pred_return": round(pred_return, 6),
                "available": True,
            }
    except Exception:
        return {"direction": 0, "prob_up": 0.333, "prob_down": 0.333, "available": False}
