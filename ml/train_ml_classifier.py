"""
ML Classifier Training: XGBoost + LightGBM for directional prediction.

Trains a 3-class classifier (up=1, neutral=0, down=-1) using features from
features.eth_features, evaluates with TimeSeriesSplit, and registers the
winning model in features.ml_model_registry.

Usage:
    python ml/train_ml_classifier.py
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score, f1_score, log_loss, precision_score, recall_score,
    classification_report,
)

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False
    print("[warn] xgboost not installed – skipping XGBoost", file=sys.stderr)

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    print("[warn] lightgbm not installed – skipping LightGBM", file=sys.stderr)

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False
    print("[warn] joblib not installed – model will not be saved", file=sys.stderr)

MODEL_REGISTRY_DIR = Path(__file__).parent / "model_registry"
MODEL_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

# Features used for training (drop close, obv due to scale/lookahead issues)
FEATURE_COLS = [
    "returns_1h", "sma_5", "sma_10", "ema_10", "volatility_10",
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "bb_width", "bb_pct",
    "atr_pct",
    "stoch_k", "stoch_d",
    "signal_1h", "signal_4h",
]

# Target column
TARGET_COL = "target_direction"

# Label mapping for sklearn (XGB requires 0-indexed classes)
LABEL_MAP = {-1: 0, 0: 1, 1: 2}
LABEL_MAP_INV = {0: -1, 1: 0, 2: 1}


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def load_features(conn, exchange: str, symbol: str) -> pd.DataFrame:
    query = f"""
        SELECT ts, {', '.join(FEATURE_COLS)}, {TARGET_COL}
        FROM features.eth_features
        WHERE exchange = %s AND symbol = %s
          AND {TARGET_COL} IS NOT NULL
          AND rsi_14 IS NOT NULL
        ORDER BY ts
    """
    df = pd.read_sql(query, conn, params=(exchange, symbol))
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def evaluate_model(model, X_test, y_test_mapped, model_name: str) -> dict:
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)
    metrics = {
        "accuracy": float(accuracy_score(y_test_mapped, y_pred)),
        "f1_macro": float(f1_score(y_test_mapped, y_pred, average="macro", zero_division=0)),
        "f1_weighted": float(f1_score(y_test_mapped, y_pred, average="weighted", zero_division=0)),
        "precision_macro": float(precision_score(y_test_mapped, y_pred, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y_test_mapped, y_pred, average="macro", zero_division=0)),
        "log_loss": float(log_loss(y_test_mapped, y_prob)),
    }
    print(f"\n[{model_name}] Evaluation:")
    print(f"  Accuracy:    {metrics['accuracy']:.4f}")
    print(f"  F1 (macro):  {metrics['f1_macro']:.4f}")
    print(f"  Log Loss:    {metrics['log_loss']:.4f}")
    print(classification_report(
        y_test_mapped, y_pred,
        target_names=["down", "neutral", "up"],
        zero_division=0,
    ))
    return metrics


def cross_val_score_ts(model_cls, X, y_mapped, n_splits=5):
    """Walk-forward TimeSeriesSplit CV — returns mean F1 macro."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y_mapped[train_idx], y_mapped[val_idx]
        m = model_cls()
        m.fit(X_tr, y_tr)
        y_pred = m.predict(X_val)
        score = f1_score(y_val, y_pred, average="macro", zero_division=0)
        scores.append(score)
        print(f"  Fold {fold+1}: F1={score:.4f}")
    return float(np.mean(scores))


def build_xgb_factory():
    if not HAS_XGB:
        return None
    def factory():
        return xgb.XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=10,
            eval_metric="mlogloss",
            use_label_encoder=False,
            random_state=42,
            n_jobs=-1,
        )
    return factory


def build_lgb_factory():
    if not HAS_LGB:
        return None
    def factory():
        return lgb.LGBMClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
            verbose=-1,
        )
    return factory


def register_model(conn, model_id: str, model_type: str, artifact_path: str,
                   feature_cols: list, hyperparams: dict, metrics: dict):
    # Deactivate all existing models of the same type
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE features.ml_model_registry SET is_active = FALSE WHERE model_type = %s",
            (model_type,),
        )
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO features.ml_model_registry
                (model_id, model_type, trained_at, feature_cols, hyperparams, metrics, is_active, artifact_path)
            VALUES %s
            ON CONFLICT (model_id) DO UPDATE SET
                metrics      = EXCLUDED.metrics,
                is_active    = EXCLUDED.is_active,
                artifact_path = EXCLUDED.artifact_path,
                trained_at   = EXCLUDED.trained_at
            """,
            [(
                model_id, model_type,
                datetime.now(timezone.utc),
                json.dumps(feature_cols),
                json.dumps(hyperparams),
                json.dumps(metrics),
                True,
                artifact_path,
            )],
        )
    conn.commit()
    print(f"[register] Model '{model_id}' ({model_type}) registered as active.")


def main():
    exchange = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")
    db_host = get_env("DB_HOST")
    db_port = int(get_env("DB_PORT", "5432"))
    db_user = get_env("DB_USER")
    db_pass = get_env("DB_PASSWORD")
    db_name = get_env("DB_NAME")

    conn = psycopg2.connect(
        host=db_host, port=db_port, user=db_user, password=db_pass, dbname=db_name,
    )

    print("[train_ml] Loading features from DB…")
    df = load_features(conn, exchange, symbol)
    if len(df) < 200:
        print(f"[train_ml] Not enough data ({len(df)} rows). Need at least 200.", file=sys.stderr)
        conn.close()
        return

    # Drop rows with any NaN in feature columns
    df_clean = df[FEATURE_COLS + [TARGET_COL]].dropna()
    print(f"[train_ml] Training on {len(df_clean)} rows, {len(FEATURE_COLS)} features")

    X_raw = df_clean[FEATURE_COLS].values
    y_raw = df_clean[TARGET_COL].values.astype(int)
    y_mapped = np.array([LABEL_MAP[int(v)] for v in y_raw])

    # Time-series train/test split (80/20, no shuffle)
    split_idx = int(len(X_raw) * 0.8)
    X_train_raw, X_test_raw = X_raw[:split_idx], X_raw[split_idx:]
    y_train, y_test = y_mapped[:split_idx], y_mapped[split_idx:]

    # Scale features (fit on train only)
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train_raw)
    X_test = scaler.transform(X_test_raw)

    results = {}
    models = {}

    # ── XGBoost ──────────────────────────────────────────────────────────────
    xgb_factory = build_xgb_factory()
    if xgb_factory is not None:
        print("\n[XGBoost] Walk-forward CV…")
        # CV on full scaled set
        X_scaled_full = scaler.transform(X_raw)
        xgb_cv_score = cross_val_score_ts(xgb_factory, X_scaled_full, y_mapped)
        print(f"[XGBoost] Mean CV F1 (macro): {xgb_cv_score:.4f}")

        print("[XGBoost] Training final model…")
        xgb_model = xgb_factory()
        xgb_model.fit(X_train, y_train)
        xgb_metrics = evaluate_model(xgb_model, X_test, y_test, "XGBoost")
        xgb_metrics["cv_f1_macro"] = xgb_cv_score
        results["xgboost"] = xgb_metrics
        models["xgboost"] = (xgb_model, scaler, xgb_factory().__class__.__name__)

    # ── LightGBM ─────────────────────────────────────────────────────────────
    lgb_factory = build_lgb_factory()
    if lgb_factory is not None:
        print("\n[LightGBM] Walk-forward CV…")
        X_scaled_full = scaler.transform(X_raw)
        lgb_cv_score = cross_val_score_ts(lgb_factory, X_scaled_full, y_mapped)
        print(f"[LightGBM] Mean CV F1 (macro): {lgb_cv_score:.4f}")

        print("[LightGBM] Training final model…")
        lgb_model = lgb_factory()
        lgb_model.fit(X_train, y_train)
        lgb_metrics = evaluate_model(lgb_model, X_test, y_test, "LightGBM")
        lgb_metrics["cv_f1_macro"] = lgb_cv_score
        results["lightgbm"] = lgb_metrics
        models["lightgbm"] = (lgb_model, scaler, lgb_factory().__class__.__name__)

    if not results:
        print("[train_ml] No models trained (missing xgboost and lightgbm).", file=sys.stderr)
        conn.close()
        return

    # Pick winner by CV F1
    best_type = max(results, key=lambda k: results[k].get("cv_f1_macro", 0))
    best_model, best_scaler, best_cls_name = models[best_type]
    best_metrics = results[best_type]
    print(f"\n[train_ml] Winner: {best_type} (CV F1={best_metrics['cv_f1_macro']:.4f})")

    # Save artifact
    if HAS_JOBLIB:
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        model_id = f"ml_{best_type}_{ts_str}_{uuid.uuid4().hex[:6]}"
        artifact_path = str(MODEL_REGISTRY_DIR / f"{model_id}.joblib")
        joblib.dump({
            "model": best_model,
            "scaler": best_scaler,
            "feature_cols": FEATURE_COLS,
            "label_map": LABEL_MAP,
            "label_map_inv": LABEL_MAP_INV,
            "model_type": best_type,
            "metrics": best_metrics,
        }, artifact_path)
        print(f"[train_ml] Saved artifact: {artifact_path}")

        hyperparams = {k: v for k, v in best_model.get_params().items()
                       if isinstance(v, (int, float, str, bool, type(None)))}

        register_model(
            conn,
            model_id=model_id,
            model_type=best_type,
            artifact_path=artifact_path,
            feature_cols=FEATURE_COLS,
            hyperparams=hyperparams,
            metrics=best_metrics,
        )
    else:
        print("[train_ml] joblib not available – model not persisted.")

    conn.close()
    print("[train_ml] Done.")


if __name__ == "__main__":
    main()
