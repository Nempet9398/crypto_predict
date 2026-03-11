"""
Ensemble Signal Engine.

Combines:
  1. ARIMA forecast directional probability (weight 0.30)
  2. XGBoost/LightGBM ML classification probability (weight 0.40)
  3. 1h multi-timeframe signal (weight 0.20)
  4. 4h multi-timeframe signal (weight 0.10)

Plus:
  - Volatility regime filter
  - ATR-based TP/SL (replaces fixed-percentage TP/SL)
  - Half-Kelly position sizing suggestion
  - Persistence to features.ensemble_signals
"""
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from app.core.db import get_conn

# Import ARIMA signal computation
from app.core.arima_service import compute_signal as arima_compute_signal

# ML classifier service (graceful fallback if model not trained yet)
_ML_ARTIFACT: Optional[dict] = None
_ML_ARTIFACT_LOADED = False


def _get_ml_artifact() -> Optional[dict]:
    global _ML_ARTIFACT, _ML_ARTIFACT_LOADED
    if _ML_ARTIFACT_LOADED:
        return _ML_ARTIFACT
    try:
        # Import from ml package — works when ml/ is on sys.path
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../ml"))
        from ml_classifier_service import load_active_ml_model
        _ML_ARTIFACT = load_active_ml_model()
    except Exception:
        _ML_ARTIFACT = None
    _ML_ARTIFACT_LOADED = True
    return _ML_ARTIFACT


def reload_ml_artifact():
    """Force-reload the ML model (call after retraining)."""
    global _ML_ARTIFACT, _ML_ARTIFACT_LOADED
    _ML_ARTIFACT_LOADED = False
    _ML_ARTIFACT = None
    return _get_ml_artifact()


def _predict_ml_direction(features_row: dict) -> dict:
    """Returns ML probabilities; falls back to neutral 0.33/0.34/0.33 if no model."""
    artifact = _get_ml_artifact()
    if artifact is None:
        return {
            "ml_direction": 0,
            "prob_up": 0.33,
            "prob_neutral": 0.34,
            "prob_down": 0.33,
            "available": False,
        }
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../ml"))
        from ml_classifier_service import predict_direction
        return predict_direction(features_row, artifact=artifact)
    except Exception:
        return {
            "ml_direction": 0,
            "prob_up": 0.33,
            "prob_neutral": 0.34,
            "prob_down": 0.33,
            "available": False,
        }


def _fetch_latest_features(exchange: str, symbol: str) -> Optional[dict]:
    """Fetches the most recent row from features.eth_features."""
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM features.eth_features
                WHERE exchange = %s AND symbol = %s
                  AND atr_14 IS NOT NULL
                ORDER BY ts DESC
                LIMIT 1
            """, (exchange, symbol))
            row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _get_arima_direction(arima_result: dict) -> tuple[int, float, float]:
    """Extracts direction and probabilities from ARIMA compute_signal output."""
    prob_up = float(arima_result.get("prob_up", 0.33))
    prob_down = float(arima_result.get("prob_down", 0.33))
    if prob_up > prob_down and prob_up >= 0.50:
        direction = 1
    elif prob_down > prob_up and prob_down >= 0.50:
        direction = -1
    else:
        direction = 0
    return direction, prob_up, prob_down


def _upsert_ensemble_signal(exchange: str, symbol: str, ts: datetime,
                             timeframe: str, payload: dict):
    """Persists the computed ensemble signal to the DB."""
    try:
        conn = get_conn()
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
            """, [(
                exchange, symbol, ts, timeframe,
                payload["arima_direction"],
                payload["arima_prob_up"],
                payload["arima_prob_down"],
                payload["ml_direction"],
                payload["ml_prob_up"],
                payload["ml_prob_down"],
                payload["mtf_1h"],
                payload["mtf_4h"],
                payload["vol_regime"],
                payload["signal"],
                payload["score"],
                payload["confidence"],
                payload["atr_14"],
                payload["current_close"],
                payload.get("tp_price"),
                payload.get("sl_price"),
                payload["rr_ratio"],
                payload["position_size_pct"],
                datetime.now(timezone.utc),
            )])
        conn.commit()
        conn.close()
    except Exception:
        pass  # non-fatal: signal still returned to caller


def compute_ensemble_signal(
    exchange: str,
    symbol: str,
    timeframe: str = "15m",
    horizon_hours: int = 6,
    atr_tp_multiple: float = 2.0,
    atr_sl_multiple: float = 1.0,
    min_ml_prob: float = 0.45,
    min_arima_prob: float = 0.50,
    require_mtf_agreement: bool = True,
    max_kelly_fraction: float = 0.25,
    arima_up_pct: float = 1.5,
    arima_down_pct: float = 1.5,
) -> dict[str, Any]:
    """
    Computes the ensemble trading signal.

    Returns a comprehensive signal dict including:
      - signal: 'long' | 'short' | 'no-trade'
      - score: weighted directional score (-1 to +1)
      - ARIMA and ML component probabilities
      - Multi-timeframe confirmation (1h, 4h)
      - Volatility regime
      - ATR-based TP and SL prices
      - Half-Kelly position size suggestion (%)
    """
    # ── 1. ARIMA component ───────────────────────────────────────────────────
    try:
        arima_result = arima_compute_signal(
            horizon_hours=horizon_hours,
            up_pct=arima_up_pct,
            down_pct=arima_down_pct,
            method="normal",
            timeframe=timeframe,
        )
        arima_direction, arima_prob_up, arima_prob_down = _get_arima_direction(arima_result)
        current_close = float(arima_result.get("current_close", 0.0))
    except Exception as e:
        arima_direction, arima_prob_up, arima_prob_down = 0, 0.33, 0.33
        current_close = 0.0

    # ── 2. Latest features row ───────────────────────────────────────────────
    features_row = _fetch_latest_features(exchange, symbol)
    if features_row is None:
        features_row = {}

    if current_close == 0.0:
        current_close = float(features_row.get("close") or 0.0)

    # ── 3. ML classifier ─────────────────────────────────────────────────────
    ml_result = _predict_ml_direction(features_row)
    ml_direction = ml_result["ml_direction"]
    ml_prob_up = ml_result["prob_up"]
    ml_prob_down = ml_result["prob_down"]
    ml_available = ml_result["available"]

    # ── 4. Multi-timeframe signals ───────────────────────────────────────────
    mtf_1h = int(features_row.get("signal_1h") or 0)
    mtf_4h = int(features_row.get("signal_4h") or 0)

    # ── 5. Volatility regime ─────────────────────────────────────────────────
    vol_regime = str(features_row.get("vol_regime") or "unknown")
    atr_14 = float(features_row.get("atr_14") or 0.0)

    # ── 6. Weighted ensemble score ───────────────────────────────────────────
    # Weights: ARIMA=0.30, ML=0.40, MTF-1h=0.20, MTF-4h=0.10
    WEIGHTS = {"arima": 0.30, "ml": 0.40, "mtf_1h": 0.20, "mtf_4h": 0.10}

    arima_score = arima_prob_up - arima_prob_down   # range ~[-1, +1]
    ml_score = ml_prob_up - ml_prob_down             # range ~[-1, +1]
    mtf_1h_score = mtf_1h * 0.5                     # -0.5, 0, +0.5
    mtf_4h_score = mtf_4h * 0.5

    score = (
        WEIGHTS["arima"] * arima_score
        + WEIGHTS["ml"] * ml_score
        + WEIGHTS["mtf_1h"] * mtf_1h_score
        + WEIGHTS["mtf_4h"] * mtf_4h_score
    )

    # ── 7. Signal decision with filters ──────────────────────────────────────
    def _passes_long_filter() -> bool:
        if ml_available and ml_prob_up < min_ml_prob:
            return False
        if arima_prob_up < min_arima_prob:
            return False
        if vol_regime == "high":
            # In high vol, require stronger confirmation
            if ml_available and ml_prob_up < 0.55:
                return False
        if require_mtf_agreement and (mtf_1h < 0 or mtf_4h < 0):
            # If either higher TF is explicitly bearish, suppress long
            return False
        return True

    def _passes_short_filter() -> bool:
        if ml_available and ml_prob_down < min_ml_prob:
            return False
        if arima_prob_down < min_arima_prob:
            return False
        if vol_regime == "high":
            if ml_available and ml_prob_down < 0.55:
                return False
        if require_mtf_agreement and (mtf_1h > 0 or mtf_4h > 0):
            return False
        return True

    signal = "no-trade"
    if score > 0.08 and _passes_long_filter():
        signal = "long"
    elif score < -0.08 and _passes_short_filter():
        signal = "short"

    # ── 8. ATR-based TP/SL ───────────────────────────────────────────────────
    rr_ratio = atr_tp_multiple / max(atr_sl_multiple, 0.01)
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None

    if atr_14 > 0 and current_close > 0:
        if signal == "long":
            tp_price = current_close + atr_tp_multiple * atr_14
            sl_price = current_close - atr_sl_multiple * atr_14
        elif signal == "short":
            tp_price = current_close - atr_tp_multiple * atr_14
            sl_price = current_close + atr_sl_multiple * atr_14

    # ── 9. Half-Kelly position sizing ────────────────────────────────────────
    if signal == "long":
        prob_win = ml_prob_up if ml_available else arima_prob_up
    elif signal == "short":
        prob_win = ml_prob_down if ml_available else arima_prob_down
    else:
        prob_win = 0.5

    prob_loss = 1.0 - prob_win
    kelly = max(0.0, (prob_win * rr_ratio - prob_loss) / rr_ratio)
    position_size_pct = round(min(kelly * 0.5, max_kelly_fraction) * 100, 2)

    # ── 10. Confidence score ─────────────────────────────────────────────────
    confidence = round(min(abs(score) * 2, 1.0), 4)  # normalize to [0,1]

    result: dict[str, Any] = {
        "signal": signal,
        "score": round(score, 4),
        "confidence": confidence,
        # ARIMA
        "arima_direction": arima_direction,
        "arima_prob_up": round(arima_prob_up, 4),
        "arima_prob_down": round(arima_prob_down, 4),
        # ML
        "ml_direction": ml_direction,
        "ml_prob_up": round(ml_prob_up, 4),
        "ml_prob_down": round(ml_prob_down, 4),
        "ml_available": ml_available,
        # MTF
        "mtf_1h": mtf_1h,
        "mtf_4h": mtf_4h,
        # Regime & ATR
        "vol_regime": vol_regime,
        "atr_14": round(atr_14, 4),
        "current_close": round(current_close, 4),
        # TP/SL
        "tp_price": round(tp_price, 4) if tp_price is not None else None,
        "sl_price": round(sl_price, 4) if sl_price is not None else None,
        "rr_ratio": round(rr_ratio, 2),
        # Position sizing
        "position_size_pct": position_size_pct,
        # Metadata
        "horizon_hours": horizon_hours,
        "timeframe": timeframe,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── 11. Persist to DB ─────────────────────────────────────────────────────
    if features_row.get("ts"):
        _upsert_ensemble_signal(exchange, symbol, features_row["ts"], timeframe, result)

    return result
