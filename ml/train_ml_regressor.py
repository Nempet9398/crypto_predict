"""
ML Regression Model Training: XGBoost / LightGBM + Optuna AutoML
Predicts forward returns at 1h, 3h, 6h horizons using quantile regression.

Pipeline:
  1. Feature selection via SHAP importance (selects top-k features per horizon)
  2. Optuna Bayesian hyperparameter search (TPESampler + MedianPruner)
  3. Walk-forward TimeSeriesSplit CV (no lookahead)
  4. Best algo (XGB vs LGB) selected by CV MAE
  5. Final model trained on full data with best hyperparams + selected features

Outputs per horizon:
  - predicted_return  (median, q=0.50)
  - lower_10          (10th percentile)
  - upper_90          (90th percentile)
  → Confidence interval width indicates prediction reliability

Quality gate: cv_dir_acc > 0.50 required for promotion.
Registered in features.ml_model_registry with model_type = '{algo}_regressor_{horizon}'.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

# Full candidate feature set (feature selection prunes this per horizon)
# 3원칙 기반 피처 구성:
#   원칙 1: 값보다 이벤트를 (크로스오버, 돌파 이벤트)
#   원칙 2: 방향성을 피처로 (이전 봉 값 → 방향 감지)
#   원칙 3: 시장 국면 맥락 (ADX, regime)
CANDIDATE_FEATURE_COLS = [
    # ── 기본 기술지표 ──────────────────────────────────────────
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "bb_pct",
    "bb_width",
    "atr_pct",
    "stoch_k",
    "stoch_d",
    "volatility_10",
    "obv",
    "vwap",
    "volume_ratio",
    "ema_20",
    "ema_50",
    # ── MTF 신호 ──────────────────────────────────────────────
    "signal_1h",
    "signal_4h",
    # ── Lag returns (lookahead 없음) ──────────────────────────
    "returns_lag_1",
    "returns_lag_2",
    "returns_lag_3",
    "returns_lag_4",
    "returns_lag_6",
    "returns_lag_8",
    # ── 시간 인코딩 ───────────────────────────────────────────
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    # ── 원칙 2: 방향성 피처 (이전 봉) ────────────────────────
    "rsi_prev",
    "macd_hist_prev",
    "atr_pct_prev",
    "bb_pct_prev",
    # ── 원칙 1: 이벤트 피처 (돌파 순간) ─────────────────────
    "macd_golden_cross",
    "macd_dead_cross",
    "stoch_golden_cross",
    "stoch_dead_cross",
    "rsi_cross_30_up",
    "rsi_cross_70_down",
    "volume_above_avg",
    "ma_regular_arrangement",
    # ── 원칙 3: 추세 강도 (MarketRegime) ─────────────────────
    "adx",
    # ── 심리적 지지/저항선 ─────────────────────────────────────
    "dist_to_round_100",
    "dist_to_round_500",
    "dist_to_round_1000",
    "near_round_100",
    "near_round_500",
    "near_round_1000",
    "broke_round_100_up",
    "broke_round_100_down",
]

# Feature selection config
FEATURE_SELECTION_TOP_K = 15      # Keep top-k features per horizon
FEATURE_SELECTION_MIN_K = 8       # Minimum features to keep

# Optuna tuning config
OPTUNA_N_TRIALS = 40              # Bayesian trials (efficient vs grid search)
OPTUNA_CV_SPLITS = 4              # Inner CV folds for tuning (faster)
OUTER_CV_SPLITS = 5               # Outer CV folds for final evaluation

MIN_TRAIN_ROWS = 500


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
            ts, close, returns_1bar,
            rsi_14, macd_line, macd_signal, macd_hist,
            bb_pct, bb_width, atr_pct,
            stoch_k, stoch_d, volatility_10,
            obv, vwap, volume_ratio,
            ema_20, ema_50,
            signal_1h, signal_4h,
            -- 원칙 2: 방향성 피처
            rsi_prev, macd_hist_prev, atr_pct_prev, bb_pct_prev,
            -- 원칙 1: 이벤트 피처
            macd_golden_cross, macd_dead_cross,
            stoch_golden_cross, stoch_dead_cross,
            rsi_cross_30_up, rsi_cross_70_down,
            volume_above_avg, ma_regular_arrangement,
            -- 원칙 3: ADX
            adx
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
        df[f"returns_lag_{lag}"] = df["returns_1bar"].shift(lag)

    # Cyclic time encoding
    hour = df.index.hour
    dow = df.index.dayofweek
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    # Forward return targets (future values — shifted backward)
    for horizon_name, n_bars in HORIZONS.items():
        col = f"target_return_{horizon_name}"
        df[col] = (df["close"].shift(-n_bars) / df["close"]) - 1.0

    return df.reset_index()


def build_xy(df: pd.DataFrame, horizon: str, feature_cols: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """Build feature matrix X and target y for a given horizon."""
    target_col = f"target_return_{horizon}"
    available = [c for c in feature_cols if c in df.columns]
    needed_cols = available + [target_col]
    sub = df[needed_cols].dropna()
    X = sub[available].astype(float)
    y = sub[target_col].astype(float)
    return X, y


# ── Feature Selection ─────────────────────────────────────────────────────────

def select_features_by_importance(
    X: pd.DataFrame,
    y: pd.Series,
    top_k: int = FEATURE_SELECTION_TOP_K,
) -> list[str]:
    """
    Select top-k features using XGBoost built-in feature importance.
    Falls back to all features if selection fails or too few candidates.

    Strategy:
      1. Train a quick XGBoost model on a recent subset (last 60% of data)
      2. Rank features by 'gain' importance (most signal-rich)
      3. Return top-k feature names (min: FEATURE_SELECTION_MIN_K)
    """
    try:
        import xgboost as xgb

        # Use recent 60% of data for importance estimation (faster, more relevant)
        n = len(X)
        start = int(n * 0.4)
        X_sub = X.iloc[start:].values
        y_sub = y.iloc[start:].values

        scaler = StandardScaler()
        X_sub_s = scaler.fit_transform(X_sub)

        model = xgb.XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            tree_method="hist",
            random_state=42,
            verbosity=0,
        )
        model.fit(X_sub_s, y_sub)

        importances = model.feature_importances_
        feature_importance = sorted(
            zip(X.columns, importances),
            key=lambda x: x[1],
            reverse=True,
        )
        # Keep top-k but at least FEATURE_SELECTION_MIN_K
        k = max(FEATURE_SELECTION_MIN_K, min(top_k, len(feature_importance)))
        selected = [name for name, imp in feature_importance[:k] if imp > 0]

        # Ensure minimum
        if len(selected) < FEATURE_SELECTION_MIN_K:
            selected = [name for name, _ in feature_importance[:FEATURE_SELECTION_MIN_K]]

        logger.info("Feature selection: %d → %d features selected: %s",
                    len(X.columns), len(selected), selected)
        return selected

    except Exception as exc:
        logger.warning("Feature selection failed (%s), using all features", exc)
        return list(X.columns)


# ── Optuna Hyperparameter Tuning ──────────────────────────────────────────────

def _make_xgb_model(trial, quantile: float):
    """Build XGBoost quantile model with Optuna-suggested hyperparams."""
    import xgboost as xgb
    return xgb.XGBRegressor(
        n_estimators=trial.suggest_int("n_estimators", 100, 600),
        max_depth=trial.suggest_int("max_depth", 3, 7),
        learning_rate=trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
        subsample=trial.suggest_float("subsample", 0.6, 1.0),
        colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
        min_child_weight=trial.suggest_int("min_child_weight", 1, 10),
        reg_alpha=trial.suggest_float("reg_alpha", 1e-4, 1.0, log=True),
        reg_lambda=trial.suggest_float("reg_lambda", 1e-4, 2.0, log=True),
        objective="reg:quantileerror",
        quantile_alpha=quantile,
        tree_method="hist",
        random_state=42,
        verbosity=0,
    )


def _make_lgb_model(trial, quantile: float):
    """Build LightGBM quantile model with Optuna-suggested hyperparams."""
    import lightgbm as lgb
    return lgb.LGBMRegressor(
        n_estimators=trial.suggest_int("n_estimators", 100, 600),
        max_depth=trial.suggest_int("max_depth", 3, 7),
        learning_rate=trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
        num_leaves=trial.suggest_int("num_leaves", 20, 80),
        subsample=trial.suggest_float("subsample", 0.6, 1.0),
        colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
        min_child_samples=trial.suggest_int("min_child_samples", 5, 50),
        reg_alpha=trial.suggest_float("reg_alpha", 1e-4, 1.0, log=True),
        reg_lambda=trial.suggest_float("reg_lambda", 1e-4, 2.0, log=True),
        objective="quantile",
        alpha=quantile,
        random_state=42,
        verbosity=-1,
    )


def tune_hyperparams_optuna(
    X: np.ndarray,
    y: np.ndarray,
    algo: str,
    n_trials: int = OPTUNA_N_TRIALS,
    cv_splits: int = OPTUNA_CV_SPLITS,
) -> dict:
    """
    Optuna Bayesian hyperparameter search for a given algo.
    Uses MedianPruner to early-stop poor trials (resource efficient).
    Optimizes median quantile (q=0.50) MAE on inner CV.

    Returns best hyperparams dict.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        logger.warning("Optuna not installed. Using default hyperparams.")
        return _default_hyperparams(algo)

    tscv = TimeSeriesSplit(n_splits=cv_splits)

    def objective(trial):
        # Build model for q=0.50 (used for MAE tuning)
        if algo == "xgb":
            try:
                model = _make_xgb_model(trial, quantile=0.50)
            except ImportError:
                raise optuna.exceptions.TrialPruned()
        else:
            try:
                model = _make_lgb_model(trial, quantile=0.50)
            except ImportError:
                raise optuna.exceptions.TrialPruned()

        fold_maes = []
        for fold_i, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_tr, y_tr = X[train_idx], y[train_idx]
            X_val, y_val = X[val_idx], y[val_idx]

            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_val_s = scaler.transform(X_val)

            model.fit(X_tr_s, y_tr)
            preds = model.predict(X_val_s)
            mae = float(np.mean(np.abs(preds - y_val)))
            fold_maes.append(mae)

            # Report intermediate value for pruning
            trial.report(np.mean(fold_maes), fold_i)
            if trial.should_prune():
                raise optuna.exceptions.TrialPruned()

        return float(np.mean(fold_maes))

    pruner = optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=1)
    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(direction="minimize", pruner=pruner, sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_params = study.best_params
    best_mae = study.best_value
    n_completed = len([t for t in study.trials if t.state.name == "COMPLETE"])
    logger.info("[Optuna/%s] best MAE=%.6f | trials=%d/%d | params=%s",
                algo, best_mae, n_completed, n_trials, best_params)
    return best_params


def _default_hyperparams(algo: str) -> dict:
    """Fallback hyperparams if Optuna is unavailable."""
    return {
        "n_estimators": 300,
        "max_depth": 4,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
    }


def train_quantile_models_with_params(
    X_train: np.ndarray,
    y_train: np.ndarray,
    best_params: dict,
    algo: str,
) -> Optional[dict]:
    """
    Train XGBoost or LightGBM quantile models (q=0.1, 0.5, 0.9)
    using the given hyperparams. Returns {quantile: model} or None.
    """
    q_models = {}
    for q in QUANTILES:
        try:
            if algo == "xgb":
                import xgboost as xgb
                model = xgb.XGBRegressor(
                    **{k: v for k, v in best_params.items()},
                    objective="reg:quantileerror",
                    quantile_alpha=q,
                    tree_method="hist",
                    random_state=42,
                    verbosity=0,
                )
            else:
                import lightgbm as lgb
                model = lgb.LGBMRegressor(
                    **{k: v for k, v in best_params.items()},
                    objective="quantile",
                    alpha=q,
                    random_state=42,
                    verbosity=-1,
                )
            model.fit(X_train, y_train)
            q_models[q] = model
        except Exception as exc:
            logger.warning("Failed to train %s q=%.2f: %s", algo, q, exc)
    return q_models if q_models else None


def evaluate_cv_with_params(
    X: pd.DataFrame,
    y: pd.Series,
    best_params_xgb: dict,
    best_params_lgb: dict,
    n_splits: int = OUTER_CV_SPLITS,
) -> dict[str, dict]:
    """Walk-forward outer CV with tuned hyperparams. Returns metrics per algo."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores: dict[str, list] = {"xgb": [], "lgb": []}

    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_tr, y_tr = X.iloc[train_idx].values, y.iloc[train_idx].values
        X_te, y_te = X.iloc[test_idx].values, y.iloc[test_idx].values

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)

        for algo, best_params in [("xgb", best_params_xgb), ("lgb", best_params_lgb)]:
            q_models = train_quantile_models_with_params(X_tr_s, y_tr, best_params, algo)
            if not q_models or 0.50 not in q_models:
                continue
            preds = q_models[0.50].predict(X_te_s)
            mae = float(np.mean(np.abs(preds - y_te)))
            rmse = float(np.sqrt(np.mean((preds - y_te) ** 2)))
            dir_acc = float(np.mean(np.sign(preds) == np.sign(y_te)))
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
    best_params: dict,
) -> int:
    """Insert model record into ml_model_registry. Returns model_id."""
    sql = """
        INSERT INTO features.ml_model_registry
            (model_type, trained_at, feature_cols, hyperparams, metrics, is_active, artifact_path)
        VALUES (%s, %s, %s, %s, %s, FALSE, %s)
        RETURNING model_id
    """
    model_type = f"{algo}_regressor_{horizon}"
    hyperparams = {**best_params, "quantiles": QUANTILES, "tuning": "optuna"}
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
    """
    Full AutoML pipeline for a single horizon:
      1. Feature selection (SHAP importance)
      2. Optuna hyperparameter tuning (XGB + LGB separately)
      3. Outer CV evaluation with tuned params
      4. Best algo selection by CV MAE
      5. Final model training on full data
      6. Artifact save + DB registration
    """
    logger.info("=" * 60)
    logger.info("Training horizon=%s ...", horizon)

    # df에 실제 존재하는 후보 피처만 사용 (신규 피처 미생성 시 graceful fallback)
    available_candidates = [c for c in CANDIDATE_FEATURE_COLS if c in df.columns]
    X_all, y = build_xy(df, horizon, available_candidates)
    if len(X_all) < MIN_TRAIN_ROWS:
        logger.warning("Skipping horizon=%s: only %d rows (need %d)", horizon, len(X_all), MIN_TRAIN_ROWS)
        return None

    # Step 1: Feature selection
    logger.info("[%s] Step 1/4: Feature selection from %d candidates ...", horizon, len(X_all.columns))
    selected_features = select_features_by_importance(X_all, y)
    X, y = build_xy(df, horizon, selected_features)

    logger.info("[%s] Using %d features: %s", horizon, len(selected_features), selected_features)

    # Step 2: Optuna tuning for each algo
    logger.info("[%s] Step 2/4: Optuna tuning (XGB + LGB, %d trials each) ...", horizon, OPTUNA_N_TRIALS)
    X_np = X.values
    y_np = y.values

    best_params_xgb = _default_hyperparams("xgb")
    best_params_lgb = _default_hyperparams("lgb")

    try:
        import xgboost  # noqa
        best_params_xgb = tune_hyperparams_optuna(X_np, y_np, algo="xgb")
    except ImportError:
        logger.warning("XGBoost not available, skipping XGB tuning")

    try:
        import lightgbm  # noqa
        best_params_lgb = tune_hyperparams_optuna(X_np, y_np, algo="lgb")
    except ImportError:
        logger.warning("LightGBM not available, skipping LGB tuning")

    # Step 3: Outer CV evaluation with tuned params
    logger.info("[%s] Step 3/4: Outer CV evaluation ...", horizon)
    cv_metrics = evaluate_cv_with_params(X, y, best_params_xgb, best_params_lgb)
    if not cv_metrics:
        logger.warning("No models trained for horizon=%s", horizon)
        return None

    # Step 4: Best algo selection
    best_algo = min(cv_metrics, key=lambda a: cv_metrics[a]["cv_mae"])
    best_params = best_params_xgb if best_algo == "xgb" else best_params_lgb
    logger.info("[%s] Best algo: %s | CV MAE=%.6f | dir_acc=%.3f",
                horizon, best_algo, cv_metrics[best_algo]["cv_mae"], cv_metrics[best_algo]["cv_dir_acc"])

    # Step 5: Final model on full data
    logger.info("[%s] Step 4/4: Final training on full data (%d rows) ...", horizon, len(X))
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_np)
    q_models = train_quantile_models_with_params(X_scaled, y_np, best_params, best_algo)
    if not q_models:
        return None

    # Step 6: Artifact save
    ts_str = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    artifact = {
        "scaler": scaler,
        "models": q_models,          # {0.10: model, 0.50: model, 0.90: model}
        "feature_cols": selected_features,
        "horizon": horizon,
        "algo": best_algo,
        "best_params": best_params,
        "trained_at": datetime.now(tz=UTC).isoformat(),
    }
    path = REGISTRY_DIR / f"{best_algo}_regressor_{horizon}_{ts_str}.joblib"
    joblib.dump(artifact, path)
    logger.info("[%s] Artifact saved: %s", horizon, path)

    model_id = register_model(
        conn,
        artifact_path=str(path),
        horizon=horizon,
        algo=best_algo,
        metrics={**cv_metrics.get(best_algo, {}), "n_train": len(X)},
        feature_cols=selected_features,
        best_params=best_params,
    )
    logger.info("[%s] Registered model_id=%d (%s_regressor_%s)", horizon, model_id, best_algo, horizon)

    return {
        "horizon": horizon,
        "best_algo": best_algo,
        "selected_features": selected_features,
        "best_params": best_params,
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
            logger.info("=" * 60)
            logger.info("Training complete. %d horizon(s) trained.", len(results))
            for r in results:
                logger.info("  [%s] algo=%s features=%d dir_acc=%.3f",
                            r["horizon"], r["best_algo"], len(r["selected_features"]),
                            r["cv_metrics"][r["best_algo"]]["cv_dir_acc"])
        else:
            logger.warning("No models were successfully trained.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
