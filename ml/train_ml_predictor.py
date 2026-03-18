"""
ML Predictor Training (Direction Classification) + Optuna AutoML
XGBoost/LightGBM으로 N-bar 후 가격 방향 예측 모델 학습.

Pipeline:
  1. Feature selection via XGBoost importance (selects top-k features)
  2. Class imbalance handling (scale_pos_weight / class_weight)
  3. Optuna Bayesian hyperparameter search (TPESampler + MedianPruner)
  4. Walk-forward TimeSeriesSplit CV (no lookahead)
  5. Best algo (XGB vs LGB) selected by CV macro-F1
  6. Final model trained on full data with best hyperparams + selected features

사용:
  python -m ml.train_ml_predictor [--lookback-days 60] [--horizon-bars 4]
"""
import argparse
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2

# ── 설정 ──────────────────────────────────────────────────────────────────────
MODEL_REGISTRY_DIR = os.getenv(
    "MODEL_REGISTRY_DIR",
    os.path.join(os.path.dirname(__file__), "model_registry"),
)
os.makedirs(MODEL_REGISTRY_DIR, exist_ok=True)

PREDICTOR_MODEL_PATH = os.path.join(MODEL_REGISTRY_DIR, "eth_predictor.pkl")
PREDICTOR_META_PATH = os.path.join(MODEL_REGISTRY_DIR, "eth_predictor_meta.json")

# Full candidate feature set (feature selection prunes this)
# 3원칙 기반 피처 구성:
#   원칙 1: 값보다 이벤트를 (크로스오버, 돌파 이벤트)
#   원칙 2: 방향성을 피처로 (이전 봉 값 → 방향 감지)
#   원칙 3: 시장 국면 맥락 (ADX, regime)
CANDIDATE_FEATURE_COLS = [
    # ── 기본 기술지표 ──────────────────────────────────────────
    "returns_1bar", "returns_4bar", "returns_16bar",
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "bb_pct", "bb_width",
    "atr_pct",
    "stoch_k", "stoch_d",
    "volume_ratio",
    "signal_1h", "signal_4h",
    "volatility_10",
    "ema_20", "ema_50",
    "obv",
    "vwap",
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
    # ── 원칙 3: 추세 강도 ─────────────────────────────────────
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
FEATURE_SELECTION_TOP_K = 14
FEATURE_SELECTION_MIN_K = 8

# Optuna config
OPTUNA_N_TRIALS = 40
OPTUNA_CV_SPLITS = 4
OUTER_CV_SPLITS = 5


def get_db_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        dbname=os.getenv("DB_NAME", "ethdb"),
    )


def load_features(lookback_days: int = 60, exchange: str = "binance", symbol: str = "ETH/USDT") -> pd.DataFrame:
    conn = get_db_conn()
    df = pd.read_sql(f"""
        SELECT *
        FROM features.eth_features
        WHERE exchange = %s
          AND symbol = %s
          AND ts >= NOW() - INTERVAL '{lookback_days} days'
          AND rsi_14 IS NOT NULL
          AND atr_14 IS NOT NULL
        ORDER BY ts
    """, conn, params=(exchange, symbol))
    conn.close()
    return df


def build_target(df: pd.DataFrame, horizon_bars: int = 4, threshold: float = 0.003) -> pd.Series:
    """horizon_bars 후 수익률 기준으로 방향 레이블 생성."""
    fwd_return = df["close"].shift(-horizon_bars) / df["close"] - 1
    target = pd.Series(0, index=df.index, dtype="int8")
    target[fwd_return > threshold] = 1
    target[fwd_return < -threshold] = -1
    target[fwd_return.isna()] = np.nan
    return target


def compute_extra_features(df: pd.DataFrame) -> pd.DataFrame:
    """returns_1bar, returns_4bar, returns_16bar, volume_ratio 추가 계산."""
    df = df.copy()
    close = df["close"].astype(float)
    df["returns_1bar"] = close.pct_change(1).fillna(0.0)
    df["returns_4bar"] = close.pct_change(4).fillna(0.0)
    df["returns_16bar"] = close.pct_change(16).fillna(0.0)

    if "volume" in df.columns and "volume_sma_20" in df.columns:
        df["volume_ratio"] = (
            df["volume"].astype(float) / (df["volume_sma_20"].astype(float) + 1e-9)
        ).clip(0, 10)
    else:
        df["volume_ratio"] = 1.0

    return df


# ── Feature Selection ─────────────────────────────────────────────────────────

def select_features_by_importance(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    top_k: int = FEATURE_SELECTION_TOP_K,
) -> list[str]:
    """
    Select top-k features using XGBoost built-in 'gain' importance.
    Uses recent 60% of data for estimation (more market-relevant).
    Falls back to all features if XGBoost unavailable or selection fails.
    """
    try:
        import xgboost as xgb
        from sklearn.preprocessing import StandardScaler

        n = len(X)
        start = int(n * 0.4)
        X_sub = X[start:]
        y_sub = y[start:]

        # Map labels to 0-based for XGBoost
        classes = sorted(set(y_sub))
        label_map = {v: i for i, v in enumerate(classes)}
        y_sub_mapped = np.array([label_map[v] for v in y_sub])

        scaler = StandardScaler()
        X_sub_s = scaler.fit_transform(X_sub)

        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            use_label_encoder=False,
            eval_metric="mlogloss",
            verbosity=0,
        )
        model.fit(X_sub_s, y_sub_mapped)

        importances = model.feature_importances_
        ranked = sorted(
            zip(feature_names, importances),
            key=lambda x: x[1],
            reverse=True,
        )
        k = max(FEATURE_SELECTION_MIN_K, min(top_k, len(ranked)))
        selected = [name for name, imp in ranked[:k] if imp > 0]
        if len(selected) < FEATURE_SELECTION_MIN_K:
            selected = [name for name, _ in ranked[:FEATURE_SELECTION_MIN_K]]

        print(f"[feature_selection] {len(feature_names)} → {len(selected)} features: {selected}")
        return selected

    except Exception as exc:
        print(f"[feature_selection] Failed ({exc}), using all features")
        return feature_names


# ── Optuna Hyperparameter Tuning ──────────────────────────────────────────────

def _default_hyperparams(algo: str) -> dict:
    return {
        "n_estimators": 300,
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
    }


def tune_hyperparams_optuna(
    X: np.ndarray,
    y: np.ndarray,
    algo: str,
    n_trials: int = OPTUNA_N_TRIALS,
    cv_splits: int = OPTUNA_CV_SPLITS,
) -> dict:
    """
    Optuna Bayesian hyperparameter search.
    Optimizes macro-F1 on inner walk-forward CV.
    Uses MedianPruner to kill poor trials early (resource efficient).
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        print(f"[optuna] Not installed. Using default hyperparams for {algo}.")
        return _default_hyperparams(algo)

    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import f1_score
    from sklearn.preprocessing import StandardScaler

    tscv = TimeSeriesSplit(n_splits=cv_splits)

    # Compute class weights for imbalance
    classes, counts = np.unique(y, return_counts=True)
    class_weight_map = {c: len(y) / (len(classes) * cnt) for c, cnt in zip(classes, counts)}

    def objective(trial):
        if algo == "xgb":
            try:
                import xgboost as xgb
                # scale_pos_weight only applies to binary; for multi-class we use sample_weight
                model = xgb.XGBClassifier(
                    n_estimators=trial.suggest_int("n_estimators", 100, 600),
                    max_depth=trial.suggest_int("max_depth", 3, 8),
                    learning_rate=trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                    subsample=trial.suggest_float("subsample", 0.6, 1.0),
                    colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
                    min_child_weight=trial.suggest_int("min_child_weight", 1, 10),
                    reg_alpha=trial.suggest_float("reg_alpha", 1e-4, 1.0, log=True),
                    reg_lambda=trial.suggest_float("reg_lambda", 1e-4, 2.0, log=True),
                    use_label_encoder=False,
                    eval_metric="mlogloss",
                    verbosity=0,
                )
            except ImportError:
                raise optuna.exceptions.TrialPruned()
        else:
            try:
                import lightgbm as lgb
                model = lgb.LGBMClassifier(
                    n_estimators=trial.suggest_int("n_estimators", 100, 600),
                    max_depth=trial.suggest_int("max_depth", 3, 8),
                    learning_rate=trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                    num_leaves=trial.suggest_int("num_leaves", 20, 80),
                    subsample=trial.suggest_float("subsample", 0.6, 1.0),
                    colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
                    min_child_samples=trial.suggest_int("min_child_samples", 5, 50),
                    reg_alpha=trial.suggest_float("reg_alpha", 1e-4, 1.0, log=True),
                    reg_lambda=trial.suggest_float("reg_lambda", 1e-4, 2.0, log=True),
                    class_weight="balanced",
                    verbosity=-1,
                )
            except ImportError:
                raise optuna.exceptions.TrialPruned()

        fold_f1s = []
        for fold_i, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]

            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_val_s = scaler.transform(X_val)

            sample_weight = np.array([class_weight_map.get(c, 1.0) for c in y_tr])

            if algo == "xgb":
                label_map = {v: i for i, v in enumerate(sorted(set(y_tr)))}
                y_tr_mapped = np.array([label_map[v] for v in y_tr])
                y_val_mapped = np.array([label_map.get(v, 0) for v in y_val])
                model.fit(X_tr_s, y_tr_mapped, sample_weight=sample_weight)
                preds = model.predict(X_val_s)
                inv_map = {v: k for k, v in label_map.items()}
                preds = np.array([inv_map.get(p, 0) for p in preds])
            else:
                model.fit(X_tr_s, y_tr)
                preds = model.predict(X_val_s)

            f1 = f1_score(y_val, preds, average="macro", zero_division=0)
            fold_f1s.append(f1)

            trial.report(np.mean(fold_f1s), fold_i)
            if trial.should_prune():
                raise optuna.exceptions.TrialPruned()

        return -float(np.mean(fold_f1s))  # minimize negative F1

    pruner = optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=1)
    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(direction="minimize", pruner=pruner, sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_params = study.best_params
    best_f1 = -study.best_value
    n_completed = len([t for t in study.trials if t.state.name == "COMPLETE"])
    print(f"[Optuna/{algo}] best F1={best_f1:.4f} | trials={n_completed}/{n_trials} | params={best_params}")
    return best_params


def _build_model(algo: str, best_params: dict):
    """Instantiate model with tuned params."""
    if algo == "xgboost":
        import xgboost as xgb
        return xgb.XGBClassifier(
            **best_params,
            use_label_encoder=False,
            eval_metric="mlogloss",
            verbosity=0,
        )
    else:
        import lightgbm as lgb
        return lgb.LGBMClassifier(
            **best_params,
            class_weight="balanced",
            verbosity=-1,
        )


def train(
    lookback_days: int = 60,
    horizon_bars: int = 4,
    exchange: str = "binance",
    symbol: str = "ETH/USDT",
    n_trials: int = OPTUNA_N_TRIALS,
    outer_cv_splits: int = OUTER_CV_SPLITS,
):
    """
    Full AutoML training pipeline:
      1. Load features
      2. Feature selection
      3. Optuna tuning (XGB + LGB)
      4. Outer CV evaluation
      5. Final model training
      6. Save artifact + metadata
    """
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import f1_score, accuracy_score
    from sklearn.preprocessing import StandardScaler

    print(f"[train_ml_predictor] Loading features (lookback={lookback_days}d, horizon={horizon_bars} bars)...")
    df = load_features(lookback_days, exchange, symbol)
    if df.empty or len(df) < 200:
        print(f"[train_ml_predictor] Not enough data: {len(df)} rows")
        return False

    df = compute_extra_features(df)
    df["target"] = build_target(df, horizon_bars=horizon_bars)

    # Only keep candidate features that exist in df
    available_candidates = [c for c in CANDIDATE_FEATURE_COLS if c in df.columns]
    df = df.dropna(subset=available_candidates + ["target"])
    df = df[df["target"].notna()]

    if len(df) < 100:
        print(f"[train_ml_predictor] Not enough valid rows after NaN drop: {len(df)}")
        return False

    X_all = df[available_candidates].astype(float).values
    y = df["target"].astype(int).values

    classes, counts = np.unique(y, return_counts=True)
    class_dist = dict(zip(classes.tolist(), counts.tolist()))
    print(f"[train_ml_predictor] {len(X_all)} samples | Classes: {class_dist}")

    # ── Step 1: Feature selection ──────────────────────────────────────────
    print("[train_ml_predictor] Step 1/4: Feature selection ...")
    selected_features = select_features_by_importance(X_all, y, available_candidates)
    selected_indices = [available_candidates.index(f) for f in selected_features]
    X = X_all[:, selected_indices]

    # ── Step 2: Optuna tuning ──────────────────────────────────────────────
    print(f"[train_ml_predictor] Step 2/4: Optuna tuning ({n_trials} trials each algo) ...")
    best_params_xgb = _default_hyperparams("xgb")
    best_params_lgb = _default_hyperparams("lgb")

    try:
        import xgboost  # noqa
        best_params_xgb = tune_hyperparams_optuna(X, y, algo="xgb", n_trials=n_trials)
    except ImportError:
        print("[train_ml_predictor] xgboost not available")

    try:
        import lightgbm  # noqa
        best_params_lgb = tune_hyperparams_optuna(X, y, algo="lgb", n_trials=n_trials)
    except ImportError:
        print("[train_ml_predictor] lightgbm not available")

    # ── Step 3: Outer CV with tuned params ────────────────────────────────
    print("[train_ml_predictor] Step 3/4: Outer CV evaluation ...")
    tscv = TimeSeriesSplit(n_splits=outer_cv_splits)
    class_weight_map = {c: len(y) / (len(classes) * cnt) for c, cnt in zip(classes, counts)}

    results = []
    for algo_name, best_params in [("xgboost", best_params_xgb), ("lightgbm", best_params_lgb)]:
        try:
            model_instance = _build_model(algo_name, best_params)
        except ImportError:
            continue

        fold_f1s = []
        for train_idx, val_idx in tscv.split(X):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]

            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_val_s = scaler.transform(X_val)

            sample_weight = np.array([class_weight_map.get(c, 1.0) for c in y_tr])

            if algo_name == "xgboost":
                label_map = {v: i for i, v in enumerate(sorted(set(y_tr)))}
                y_tr_mapped = np.array([label_map[v] for v in y_tr])
                y_val_mapped = np.array([label_map.get(v, 0) for v in y_val])
                model_instance.fit(X_tr_s, y_tr_mapped, sample_weight=sample_weight)
                preds = model_instance.predict(X_val_s)
                inv_map = {v: k for k, v in label_map.items()}
                preds = np.array([inv_map.get(p, 0) for p in preds])
            else:
                model_instance.fit(X_tr_s, y_tr)
                preds = model_instance.predict(X_val_s)

            f1 = f1_score(y_val, preds, average="macro", zero_division=0)
            fold_f1s.append(f1)

        mean_f1 = float(np.mean(fold_f1s))
        print(f"[train_ml_predictor] {algo_name}: CV F1={mean_f1:.4f}")
        results.append((algo_name, model_instance, best_params, mean_f1))

    if not results:
        print("[train_ml_predictor] No ML library available")
        return False

    best_name, best_model, best_params_final, best_f1 = max(results, key=lambda r: r[3])
    print(f"[train_ml_predictor] Best algo: {best_name} | CV F1={best_f1:.4f}")

    # ── Step 4: Final training on full data ───────────────────────────────
    print("[train_ml_predictor] Step 4/4: Final training on full data ...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    sample_weight_full = np.array([class_weight_map.get(c, 1.0) for c in y])

    if best_name == "xgboost":
        label_map = {v: i for i, v in enumerate(sorted(set(y)))}
        y_mapped = np.array([label_map[v] for v in y])
        best_model.fit(X_scaled, y_mapped, sample_weight=sample_weight_full)
        inv_map = {v: k for k, v in label_map.items()}
        best_model.classes_ = np.array([inv_map[i] for i in range(len(label_map))])
    else:
        best_model.fit(X_scaled, y)

    class PipelinePredictor:
        """Scaler + model wrapper for single-call predict."""
        def __init__(self, scaler, model, feature_cols, label_map=None):
            self.scaler = scaler
            self.model = model
            self.classes_ = model.classes_
            self.feature_cols = feature_cols
            self._label_map = label_map  # only for xgboost

        def predict(self, X):
            return self.model.predict(self.scaler.transform(X))

        def predict_proba(self, X):
            return self.model.predict_proba(self.scaler.transform(X))

    pipeline = PipelinePredictor(scaler, best_model, selected_features)

    with open(PREDICTOR_MODEL_PATH, "wb") as f:
        pickle.dump(pipeline, f)

    # In-sample accuracy
    preds_final = pipeline.predict(X_scaled)
    if best_name == "xgboost":
        preds_final = np.array([inv_map.get(p, 0) for p in preds_final])
    acc = accuracy_score(y, preds_final)

    meta = {
        "model_type": best_name,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "horizon_bars": horizon_bars,
        "n_samples": len(X),
        "best_cv_f1": round(best_f1, 4),
        "train_accuracy": round(float(acc), 4),
        "feature_cols": selected_features,
        "all_candidate_features": available_candidates,
        "best_params": best_params_final,
        "class_distribution": class_dist,
        "exchange": exchange,
        "symbol": symbol,
        "tuning": "optuna",
    }
    with open(PREDICTOR_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[train_ml_predictor] Done! Best: {best_name} | CV F1={best_f1:.4f} | Train acc={acc:.4f}")
    print(f"[train_ml_predictor] Selected features ({len(selected_features)}): {selected_features}")
    print(f"[train_ml_predictor] Model saved to {PREDICTOR_MODEL_PATH}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--horizon-bars", type=int, default=4)
    parser.add_argument("--n-trials", type=int, default=OPTUNA_N_TRIALS)
    parser.add_argument("--outer-cv-splits", type=int, default=OUTER_CV_SPLITS)
    parser.add_argument("--exchange", default=os.getenv("EXCHANGE", "binance"))
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "ETH/USDT"))
    args = parser.parse_args()
    success = train(
        lookback_days=args.lookback_days,
        horizon_bars=args.horizon_bars,
        n_trials=args.n_trials,
        outer_cv_splits=args.outer_cv_splits,
        exchange=args.exchange,
        symbol=args.symbol,
    )
    sys.exit(0 if success else 1)
