"""
Standalone script to pre-compute ensemble signals for the latest N hours
and persist them to features.ensemble_signals.

Designed to run as an Airflow task (hourly) after features are updated.

Usage:
    python ml/compute_ensemble_signals.py [--hours N]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# Resolve project root so imports work regardless of cwd
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "ml"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "api"))

try:
    from ml_classifier_service import load_active_ml_model, predict_direction
    HAS_ML = True
except ImportError:
    HAS_ML = False


def get_env(name, default=None):
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def get_db_conn():
    return psycopg2.connect(
        host=get_env("DB_HOST"),
        port=int(get_env("DB_PORT", "5432")),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD"),
        dbname=get_env("DB_NAME"),
    )


FEATURE_COLS = [
    "returns_1h", "sma_5", "sma_10", "ema_10", "volatility_10",
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "bb_width", "bb_pct", "atr_pct", "stoch_k", "stoch_d",
    "signal_1h", "signal_4h",
]

WEIGHTS = {"arima": 0.30, "ml": 0.40, "mtf_1h": 0.20, "mtf_4h": 0.10}


def load_recent_features(conn, exchange: str, symbol: str, hours: int) -> pd.DataFrame:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = """
        SELECT ts, close, atr_14, atr_pct, vol_regime,
               signal_1h, signal_4h,
               returns_1h, sma_5, sma_10, ema_10, volatility_10,
               rsi_14, macd_line, macd_signal, macd_hist,
               bb_width, bb_pct, stoch_k, stoch_d
        FROM features.eth_features
        WHERE exchange = %s AND symbol = %s
          AND ts >= %s
          AND atr_14 IS NOT NULL
        ORDER BY ts
    """
    df = pd.read_sql(query, conn, params=(exchange, symbol, cutoff))
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def _fake_arima_direction(close: float, features_row: dict) -> tuple[int, float, float]:
    """
    Lightweight directional proxy when full ARIMA model is unavailable.
    Uses EMA10 vs close and MACD hist sign as a simple trend signal.
    """
    ema = features_row.get("ema_10") or close
    macd_hist = features_row.get("macd_hist") or 0.0
    trend = 1 if close > ema else -1
    macd_dir = 1 if macd_hist > 0 else (-1 if macd_hist < 0 else 0)
    composite = trend + macd_dir  # range -2..+2
    prob_up = 0.33 + 0.08 * composite
    prob_down = 0.33 - 0.08 * composite
    direction = 0
    if prob_up > 0.45:
        direction = 1
    elif prob_down > 0.45:
        direction = -1
    return direction, max(0.0, min(1.0, prob_up)), max(0.0, min(1.0, prob_down))


def compute_row_signal(row: dict, ml_artifact, rr_ratio: float = 2.0) -> dict:
    close = float(row.get("close") or 0.0)
    atr_14 = float(row.get("atr_14") or 0.0)
    mtf_1h = int(row.get("signal_1h") or 0)
    mtf_4h = int(row.get("signal_4h") or 0)
    vol_regime = str(row.get("vol_regime") or "unknown")

    # ARIMA proxy
    arima_direction, arima_prob_up, arima_prob_down = _fake_arima_direction(close, row)

    # ML prediction
    if HAS_ML and ml_artifact is not None:
        ml_result = predict_direction(row, artifact=ml_artifact)
        ml_direction = ml_result["ml_direction"]
        ml_prob_up = ml_result["prob_up"]
        ml_prob_down = ml_result["prob_down"]
    else:
        ml_direction, ml_prob_up, ml_prob_down = 0, 0.33, 0.33

    # Weighted score
    arima_score = arima_prob_up - arima_prob_down
    ml_score = ml_prob_up - ml_prob_down
    mtf_1h_score = mtf_1h * 0.5
    mtf_4h_score = mtf_4h * 0.5
    score = (
        WEIGHTS["arima"] * arima_score
        + WEIGHTS["ml"] * ml_score
        + WEIGHTS["mtf_1h"] * mtf_1h_score
        + WEIGHTS["mtf_4h"] * mtf_4h_score
    )

    # Signal
    signal = "no-trade"
    if score > 0.08 and arima_prob_up >= 0.45 and ml_prob_up >= 0.40:
        if not (mtf_1h < 0 or mtf_4h < 0):
            signal = "long"
    elif score < -0.08 and arima_prob_down >= 0.45 and ml_prob_down >= 0.40:
        if not (mtf_1h > 0 or mtf_4h > 0):
            signal = "short"

    # TP/SL
    tp_price = sl_price = None
    atr_tp_multiple, atr_sl_multiple = 2.0, 1.0
    if atr_14 > 0 and close > 0:
        if signal == "long":
            tp_price = close + atr_tp_multiple * atr_14
            sl_price = close - atr_sl_multiple * atr_14
        elif signal == "short":
            tp_price = close - atr_tp_multiple * atr_14
            sl_price = close + atr_sl_multiple * atr_14

    # Kelly
    prob_win = ml_prob_up if signal == "long" else (ml_prob_down if signal == "short" else 0.5)
    prob_loss = 1.0 - prob_win
    kelly = max(0.0, (prob_win * rr_ratio - prob_loss) / rr_ratio)
    position_size_pct = round(min(kelly * 0.5, 0.25) * 100, 2)

    return {
        "arima_direction": arima_direction,
        "arima_prob_up": arima_prob_up,
        "arima_prob_down": arima_prob_down,
        "ml_direction": ml_direction,
        "ml_prob_up": ml_prob_up,
        "ml_prob_down": ml_prob_down,
        "mtf_1h": mtf_1h,
        "mtf_4h": mtf_4h,
        "vol_regime": vol_regime,
        "signal": signal,
        "score": round(score, 4),
        "confidence": round(min(abs(score) * 2, 1.0), 4),
        "atr_14": atr_14,
        "current_close": close,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "rr_ratio": rr_ratio,
        "position_size_pct": position_size_pct,
    }


def main(hours: int = 6):
    exchange = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")
    timeframe = get_env("TIMEFRAME", "15m")

    conn = get_db_conn()
    df = load_recent_features(conn, exchange, symbol, hours)
    if df.empty:
        print(f"[ensemble] No features found for last {hours}h")
        conn.close()
        return

    ml_artifact = load_active_ml_model() if HAS_ML else None
    if ml_artifact is None:
        print("[ensemble] No active ML model found — using ARIMA proxy only")

    rows_to_upsert = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        sig = compute_row_signal(row_dict, ml_artifact)
        rows_to_upsert.append((
            exchange, symbol,
            row["ts"].to_pydatetime().replace(tzinfo=timezone.utc),
            timeframe,
            sig["arima_direction"], sig["arima_prob_up"], sig["arima_prob_down"],
            sig["ml_direction"], sig["ml_prob_up"], sig["ml_prob_down"],
            sig["mtf_1h"], sig["mtf_4h"], sig["vol_regime"],
            sig["signal"], sig["score"], sig["confidence"],
            sig["atr_14"], sig["current_close"],
            sig["tp_price"], sig["sl_price"],
            sig["rr_ratio"], sig["position_size_pct"],
            datetime.now(timezone.utc),
        ))

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO features.ensemble_signals (
                exchange, symbol, ts, timeframe,
                arima_direction, arima_prob_up, arima_prob_down,
                ml_direction, ml_prob_up, ml_prob_down,
                mtf_1h, mtf_4h, vol_regime,
                ensemble_signal, score, confidence,
                atr_14, current_close, tp_price, sl_price,
                rr_ratio, position_size_pct, computed_at
            )
            VALUES %s
            ON CONFLICT (exchange, symbol, ts, timeframe)
            DO UPDATE SET
                arima_direction   = EXCLUDED.arima_direction,
                arima_prob_up     = EXCLUDED.arima_prob_up,
                arima_prob_down   = EXCLUDED.arima_prob_down,
                ml_direction      = EXCLUDED.ml_direction,
                ml_prob_up        = EXCLUDED.ml_prob_up,
                ml_prob_down      = EXCLUDED.ml_prob_down,
                mtf_1h            = EXCLUDED.mtf_1h,
                mtf_4h            = EXCLUDED.mtf_4h,
                vol_regime        = EXCLUDED.vol_regime,
                ensemble_signal   = EXCLUDED.ensemble_signal,
                score             = EXCLUDED.score,
                confidence        = EXCLUDED.confidence,
                atr_14            = EXCLUDED.atr_14,
                current_close     = EXCLUDED.current_close,
                tp_price          = EXCLUDED.tp_price,
                sl_price          = EXCLUDED.sl_price,
                rr_ratio          = EXCLUDED.rr_ratio,
                position_size_pct = EXCLUDED.position_size_pct,
                computed_at       = EXCLUDED.computed_at
        """, rows_to_upsert)
    conn.commit()
    conn.close()
    print(f"[ensemble] Upserted {len(rows_to_upsert)} signal rows for {symbol}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=6)
    args = parser.parse_args()
    main(hours=args.hours)
