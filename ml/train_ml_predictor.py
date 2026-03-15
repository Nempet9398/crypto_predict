"""
ML Predictor Training
XGBoost/LightGBM으로 N-bar 후 가격 방향 예측 모델 학습.

사용:
  python -m ml.train_ml_predictor [--lookback-days 60] [--horizon-bars 4]
"""
import argparse
import json
import os
import pickle
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# ── 설정 ──────────────────────────────────────────────────────────────────────
MODEL_REGISTRY_DIR = os.getenv("MODEL_REGISTRY_DIR", os.path.join(os.path.dirname(__file__), "model_registry"))
os.makedirs(MODEL_REGISTRY_DIR, exist_ok=True)

PREDICTOR_MODEL_PATH = os.path.join(MODEL_REGISTRY_DIR, "eth_predictor.pkl")
PREDICTOR_META_PATH = os.path.join(MODEL_REGISTRY_DIR, "eth_predictor_meta.json")

FEATURE_COLS = [
    "returns_1bar", "returns_4bar", "returns_16bar",
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "bb_pct", "bb_width",
    "atr_pct",
    "stoch_k", "stoch_d",
    "volume_ratio",
    "signal_1h", "signal_4h",
    "volatility_10",
]


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
        df["volume_ratio"] = (df["volume"].astype(float) / (df["volume_sma_20"].astype(float) + 1e-9)).clip(0, 10)
    else:
        df["volume_ratio"] = 1.0

    return df


def train(lookback_days: int = 60, horizon_bars: int = 4,
          exchange: str = "binance", symbol: str = "ETH/USDT"):
    print(f"[train_ml_predictor] Loading features (lookback={lookback_days}d, horizon={horizon_bars} bars)...")
    df = load_features(lookback_days, exchange, symbol)
    if df.empty or len(df) < 200:
        print(f"[train_ml_predictor] Not enough data: {len(df)} rows")
        return False

    df = compute_extra_features(df)
    df["target"] = build_target(df, horizon_bars=horizon_bars)

    # NaN 제거
    df = df.dropna(subset=FEATURE_COLS + ["target"])
    df = df[df["target"].notna()]

    if len(df) < 100:
        print(f"[train_ml_predictor] Not enough valid rows after NaN drop: {len(df)}")
        return False

    X = df[FEATURE_COLS].astype(float).values
    y = df["target"].astype(int).values

    print(f"[train_ml_predictor] Training on {len(X)} samples | Classes: {dict(zip(*np.unique(y, return_counts=True)))}")

    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import f1_score, accuracy_score
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    tscv = TimeSeriesSplit(n_splits=5)
    results = []

    models_to_try = []
    try:
        import xgboost as xgb
        models_to_try.append(("xgboost", xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="mlogloss",
            verbosity=0,
        )))
    except ImportError:
        print("[train_ml_predictor] xgboost not available")

    try:
        import lightgbm as lgb
        models_to_try.append(("lightgbm", lgb.LGBMClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            verbosity=-1,
        )))
    except ImportError:
        print("[train_ml_predictor] lightgbm not available")

    if not models_to_try:
        print("[train_ml_predictor] No ML library available")
        return False

    best_name, best_model, best_f1 = None, None, -1.0

    for name, model_instance in models_to_try:
        fold_f1s = []
        for train_idx, val_idx in tscv.split(X_scaled):
            X_tr, X_val = X_scaled[train_idx], X_scaled[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]

            # XGBoost는 레이블을 0-based로 변환
            if name == "xgboost":
                label_map = {v: i for i, v in enumerate(sorted(set(y_tr)))}
                y_tr_mapped = np.array([label_map[v] for v in y_tr])
                y_val_mapped = np.array([label_map.get(v, 0) for v in y_val])
                model_instance.fit(X_tr, y_tr_mapped)
                preds = model_instance.predict(X_val)
                inv_map = {v: k for k, v in label_map.items()}
                preds = np.array([inv_map.get(p, 0) for p in preds])
            else:
                model_instance.fit(X_tr, y_tr)
                preds = model_instance.predict(X_val)

            f1 = f1_score(y_val, preds, average="macro", zero_division=0)
            fold_f1s.append(f1)

        mean_f1 = float(np.mean(fold_f1s))
        print(f"[train_ml_predictor] {name}: mean F1={mean_f1:.4f}")
        if mean_f1 > best_f1:
            best_f1 = mean_f1
            best_name = name
            best_model = model_instance

    # 전체 데이터로 최종 학습
    if best_name == "xgboost":
        label_map = {v: i for i, v in enumerate(sorted(set(y)))}
        y_mapped = np.array([label_map[v] for v in y])
        best_model.fit(X_scaled, y_mapped)
        # classes_ 복원
        inv_map = {v: k for k, v in label_map.items()}
        best_model.classes_ = np.array([inv_map[i] for i in range(len(label_map))])
    else:
        best_model.fit(X_scaled, y)

    # scaler도 모델에 포함해서 저장
    class PipelinePredictor:
        def __init__(self, scaler, model):
            self.scaler = scaler
            self.model = model
            self.classes_ = model.classes_

        def predict(self, X):
            return self.model.predict(self.scaler.transform(X))

        def predict_proba(self, X):
            return self.model.predict_proba(self.scaler.transform(X))

    pipeline = PipelinePredictor(scaler, best_model)

    # 저장
    with open(PREDICTOR_MODEL_PATH, "wb") as f:
        pickle.dump(pipeline, f)

    # 정확도 평가 (in-sample)
    preds_final = pipeline.predict(X)
    acc = accuracy_score(y, preds_final)

    meta = {
        "model_type": best_name,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "horizon_bars": horizon_bars,
        "n_samples": len(X),
        "best_cv_f1": round(best_f1, 4),
        "train_accuracy": round(float(acc), 4),
        "feature_cols": FEATURE_COLS,
        "exchange": exchange,
        "symbol": symbol,
    }
    with open(PREDICTOR_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[train_ml_predictor] Done! Best: {best_name} | CV F1={best_f1:.4f} | Train acc={acc:.4f}")
    print(f"[train_ml_predictor] Model saved to {PREDICTOR_MODEL_PATH}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--horizon-bars", type=int, default=4)
    parser.add_argument("--exchange", default=os.getenv("EXCHANGE", "binance"))
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "ETH/USDT"))
    args = parser.parse_args()
    success = train(
        lookback_days=args.lookback_days,
        horizon_bars=args.horizon_bars,
        exchange=args.exchange,
        symbol=args.symbol,
    )
    sys.exit(0 if success else 1)
