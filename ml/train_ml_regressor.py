"""
ML Regression Model Training: XGBoost / LightGBM
Predicts forward returns at 1h, 3h, 6h horizons using quantile regression.

Outputs per horizon:
  - predicted_return  (median, q=0.50)
  - lower_10          (10th percentile)
  - upper_90          (90th percentile)
  → Confidence interval width indicates prediction reliability

Model selection: TimeSeriesSplit CV, winner = lowest MAE on test folds.
Registered in features.ml_model_registry with model_type = '{algo}_regressor_{horizon}'.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ml_regressor")

UTC = timezone.utc
REGISTRY_DIR = Path(os.getenv("MODEL_REGISTRY_DIR", "/app/model_registry"))
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

HORIZONS = {
    "1h": 4,    # 4 × 15m = 1h
    "3h": 12,   # 12 × 15m = 3h
    "6h": 24,   # 24 × 15m = 6h
}

# Quantile levels for prediction intervals
QUANTILES = [0.10, 0.50, 0.90]

FEATURE_COLS = [
    # Technical indicators
    "rsi_14",
    "macd_hist",
    "bb_pct",
    "atr_pct",
    "stoch_k",
    "signal_1h",
    "signal_4h",
    "volatility_10",
    "bb_width",
    # Lag returns (no lookahead bias)
    "returns_lag_1",
    "returns_lag_2",
    "returns_lag_3",
    "returns_lag_4",
    "returns_lag_6",
    "returns_lag_8",
    # Time features (cyclic encoding)
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
]

MIN_TRAIN_ROWS = 500  # Minimum rows needed to train


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def get_db_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=get_env("DB_HOST"),
        port=int(get_env("DB_PORT", "5432")),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD"),
        dbname=get_env("DB_NAME"),
    )


def load_features(conn: psycopg2.extensions.connection, exchange: str, symbol: str) -> pd.DataFrame:
    """Load features from DB and engineer lag + time features."""
    query = """
        SELECT
            ts,
            close,
            returns_1h,
            rsi_14,
            macd_hist,
            bb_pct,
            bb_width,
            atr_pct,
            stoch_k,
            volatility_10,
            signal_1h,
            signal_4h
        FROM features.eth_features
        WHERE exchange = %s AND symbol = %s
          AND rsi_14 IS NOT NULL
        ORDER BY ts
    """
    df = pd.read_sql(query, conn, params=(exchange, symbol))
    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()

    # Lag return features (past returns, no lookahead)
    for lag in [1, 2, 3, 4, 6, 8]:
        df[f"returns_lag_{lag}"] = df["returns_1h"].shift(lag)

    # Cyclic time encoding
    hour = df.index.hour
    dow = df.index.dayofweek
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    # Forward return targets (these are shifted backward — future values)
    for horizon_name, n_bars in HORIZONS.items():
        col = f"target_return_{horizon_name}"
        df[col] = (df["close"].shift(-n_bars) / df["close"]) - 1.0

    return df.reset_index()


def build_xy(df: pd.DataFrame, horizon: str) -> tuple[pd.DataFrame, pd.Series]:
    """Build feature matrix X and target y for a given horizon."""
    target_col = f"target_return_{horizon}"
    needed_cols = FEATURE_COLS + [target_col]

    sub = df[needed_cols].dropna()
    X = sub[FEATURE_COLS].astype(float)
    y = sub[target_col].astype(float)
    return X, y


def train_quantile_models(
    X_train: np.ndarray,
    y_train: np.ndarray,
) -> dict:
    """Train XGBoost and LightGBM quantile regression models.
    Returns dict with 'xgb' and 'lgb' keys, each mapping quantile → model.
    """
    results = {}

    # --- XGBoost ---
    try:
        import xgboost as xgb
        xgb_models = {}
        for q in QUANTILES:
            model = xgb.XGBRegressor(
                n_estimators=300,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="reg:quantileerror",
                quantile_alpha=q,
                tree_method="hist",
                random_state=42,
                verbosity=0,
            )
            model.fit(X_train, y_train)
            xgb_models[q] = model
        results["xgb"] = xgb_models
        logger.info("XGBoost quantile models trained.")
    except Exception as exc:
        logger.warning("XGBoost training failed: %s", exc)

    # --- LightGBM ---
    try:
        import lightgbm as lgb
        lgb_models = {}
        for q in QUANTILES:
            model = lgb.LGBMRegressor(
                n_estimators=300,
                max_depth=4,
                learning_rate=0.05,
                num_leaves=31,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="quantile",
                alpha=q,
                random_state=42,
                verbosity=-1,
            )
            model.fit(X_train, y_train)
            lgb_models[q] = model
        results["lgb"] = lgb_models
        logger.info("LightGBM quantile models trained.")
    except Exception as exc:
        logger.warning("LightGBM training failed: %s", exc)

    return results


def evaluate_cv(
    X: pd.DataFrame,
    y: pd.Series,
    n_splits: int = 5,
) -> dict[str, dict]:
    """Walk-forward TimeSeriesSplit CV. Returns MAE, RMSE, directional accuracy per algo."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores: dict[str, list] = {"xgb": [], "lgb": []}

    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_tr, y_tr = X.iloc[train_idx].values, y.iloc[train_idx].values
        X_te, y_te = X.iloc[test_idx].values, y.iloc[test_idx].values

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)

        fold_models = train_quantile_models(X_tr_s, y_tr)

        for algo, qmodels in fold_models.items():
            if 0.50 not in qmodels:
                continue
            preds = qmodels[0.50].predict(X_te_s)
            mae = np.mean(np.abs(preds - y_te))
            rmse = np.sqrt(np.mean((preds - y_te) ** 2))
            dir_acc = np.mean(np.sign(preds) == np.sign(y_te))
            scores[algo].append({"mae": mae, "rmse": rmse, "dir_acc": dir_acc})
            logger.debug("  fold %d %s MAE=%.6f dir_acc=%.3f", fold_idx, algo, mae, dir_acc)

    metrics = {}
    for algo, fold_scores in scores.items():
        if not fold_scores:
            continue
        metrics[algo] = {
            "cv_mae":     float(np.mean([s["mae"] for s in fold_scores])),
            "cv_rmse":    float(np.mean([s["rmse"] for s in fold_scores])),
            "cv_dir_acc": float(np.mean([s["dir_acc"] for s in fold_scores])),
        }
    return metrics


def register_model(
    conn: psycopg2.extensions.connection,
    artifact_path: str,
    horizon: str,
    algo: str,
    metrics: dict,
    feature_cols: list[str],
) -> int:
    """Insert model record into ml_model_registry. Returns model_id."""
    sql = """
        INSERT INTO features.ml_model_registry
            (model_type, trained_at, feature_cols, hyperparams, metrics, is_active, artifact_path)
        VALUES (%s, %s, %s, %s, %s, FALSE, %s)
        RETURNING model_id
    """
    model_type = f"{algo}_regressor_{horizon}"
    hyperparams = {
        "n_estimators": 300,
        "max_depth": 4,
        "learning_rate": 0.05,
        "quantiles": QUANTILES,
    }
    with conn.cursor() as cur:
        cur.execute(sql, (
            model_type,
            datetime.now(tz=UTC).isoformat(),
            json.dumps(feature_cols),
            json.dumps(hyperparams),
            json.dumps(metrics),
            artifact_path,
        ))
        model_id = cur.fetchone()[0]
    conn.commit()
    return model_id


def train_horizon(
    conn: psycopg2.extensions.connection,
    df: pd.DataFrame,
    horizon: str,
    exchange: str,
    symbol: str,
) -> dict | None:
    """Train ML regressor for a single horizon. Returns result dict or None."""
    logger.info("Training horizon=%s ...", horizon)

    X, y = build_xy(df, horizon)
    if len(X) < MIN_TRAIN_ROWS:
        logger.warning("Skipping horizon=%s: only %d rows (need %d)", horizon, len(X), MIN_TRAIN_ROWS)
        return None

    # CV evaluation
    cv_metrics = evaluate_cv(X, y, n_splits=5)
    if not cv_metrics:
        logger.warning("No models trained for horizon=%s", horizon)
        return None

    # Final model on full data
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X.values)

    best_algo = min(cv_metrics, key=lambda a: cv_metrics[a]["cv_mae"])
    logger.info("Best algo for horizon=%s: %s (CV MAE=%.6f, dir_acc=%.3f)",
                horizon, best_algo, cv_metrics[best_algo]["cv_mae"], cv_metrics[best_algo]["cv_dir_acc"])

    all_models = train_quantile_models(X_scaled, y.values)
    if not all_models:
        return None

    # Save artifact
    ts_str = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    for algo, qmodels in all_models.items():
        if not qmodels:
            continue
        artifact = {
            "scaler": scaler,
            "models": qmodels,   # {0.10: model, 0.50: model, 0.90: model}
            "feature_cols": FEATURE_COLS,
            "horizon": horizon,
            "algo": algo,
            "trained_at": datetime.now(tz=UTC).isoformat(),
        }
        path = REGISTRY_DIR / f"{algo}_regressor_{horizon}_{ts_str}.joblib"
        joblib.dump(artifact, path)
        logger.info("Saved artifact: %s", path)

        model_id = register_model(
            conn,
            artifact_path=str(path),
            horizon=horizon,
            algo=algo,
            metrics={**cv_metrics.get(algo, {}), "n_train": len(X)},
            feature_cols=FEATURE_COLS,
        )
        logger.info("Registered model_id=%d (%s_regressor_%s)", model_id, algo, horizon)

    return {
        "horizon": horizon,
        "best_algo": best_algo,
        "cv_metrics": cv_metrics,
        "n_train": len(X),
    }


def promote_best_models(conn: psycopg2.extensions.connection) -> None:
    """
    For each (algo, horizon), find the latest model with cv_dir_acc > 0.50
    and mark it is_active=TRUE. Deactivate all others of same type.
    Directional accuracy > 0.50 = better than random = useful signal.
    """
    ALGO_HORIZONS = [
        (algo, horizon)
        for algo in ["xgb", "lgb"]
        for horizon in HORIZONS
    ]

    for algo, horizon in ALGO_HORIZONS:
        model_type = f"{algo}_regressor_{horizon}"
        # Find best qualifying model (highest dir_acc, trained most recently as tiebreak)
        sql_find = """
            SELECT model_id,
                   (metrics->>'cv_dir_acc')::FLOAT AS dir_acc
            FROM features.ml_model_registry
            WHERE model_type = %s
              AND (metrics->>'cv_dir_acc')::FLOAT > 0.50
            ORDER BY trained_at DESC
            LIMIT 1
        """
        with conn.cursor() as cur:
            cur.execute(sql_find, (model_type,))
            row = cur.fetchone()

        if row is None:
            logger.info("No qualifying model for %s (dir_acc > 0.50 threshold)", model_type)
            continue

        model_id, dir_acc = row
        # Deactivate all of this type, then activate winner
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE features.ml_model_registry SET is_active = FALSE WHERE model_type = %s",
                (model_type,)
            )
            cur.execute(
                "UPDATE features.ml_model_registry SET is_active = TRUE WHERE model_id = %s",
                (model_id,)
            )
        conn.commit()
        logger.info("Promoted model_id=%d (%s) dir_acc=%.3f", model_id, model_type, dir_acc)


def main() -> None:
    exchange = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")

    conn = get_db_conn()
    try:
        logger.info("Loading features for %s %s ...", exchange, symbol)
        df = load_features(conn, exchange, symbol)
        if df.empty:
            logger.error("No feature data found. Run technical_indicators.py first.")
            return

        logger.info("Loaded %d rows. Training regressors for horizons: %s", len(df), list(HORIZONS.keys()))

        results = []
        for horizon in HORIZONS:
            result = train_horizon(conn, df, horizon, exchange, symbol)
            if result:
                results.append(result)

        if results:
            promote_best_models(conn)
            logger.info("Training complete. %d horizon(s) trained.", len(results))
        else:
            logger.warning("No models were successfully trained.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
