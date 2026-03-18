"""
Signal Engine

신호 생성 로직:
  1. 기술지표 합성 스코어 (RSI, MACD, BB, Stochastic, EMA)  — 가중치: DB 설정
  2. ML 회귀 스코어 (Quantile Regression, 수익률 예측)       — 가중치: DB 설정
  3. ML 분류 스코어 (방향 예측, prob_up - prob_down)          — 가중치: DB 설정
  4. 멀티타임프레임 1h 신호                                   — 가중치: DB 설정
  5. 멀티타임프레임 4h 신호                                   — 가중치: DB 설정

앙상블 가중치는 features.ensemble_config 테이블에서 로드됩니다.
GET /ensemble/config 로 조회, PUT /ensemble/config 로 실시간 수정 가능.

필터:
  - 변동성 레짐 (고변동성 시 임계값 강화)
  - MTF agreement 필터
  - ML 최소 확률 임계값

출력:
  - signal: 'long' | 'short' | 'no-trade'
  - score, confidence
  - ATR 기반 TP/SL
  - Half-Kelly 포지션 사이징
"""
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from app.core.db import get_db_conn

# ── Ensemble Config (DB-backed, in-memory cache) ──────────────────────────────

_ENSEMBLE_CONFIG: dict | None = None

# Default weights — used as fallback if DB is not available
_DEFAULT_ENSEMBLE_CONFIG = {
    "w_tech": 0.35,
    "w_ml_regressor": 0.30,
    "w_ml_classifier": 0.15,
    "w_mtf_1h": 0.13,
    "w_mtf_4h": 0.07,
    "threshold_normal": 0.08,
    "threshold_high_vol": 0.12,
    "min_ml_prob_normal": 0.45,
    "min_ml_prob_high_vol": 0.52,
}


def _load_ensemble_config() -> dict:
    """Load ensemble weights from DB. Falls back to defaults on error."""
    global _ENSEMBLE_CONFIG
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM features.ensemble_config")
            rows = cur.fetchall()
        conn.close()
        if rows:
            cfg = {row[0]: float(row[1]) for row in rows}
            # Fill missing keys with defaults
            for k, v in _DEFAULT_ENSEMBLE_CONFIG.items():
                cfg.setdefault(k, v)
            _ENSEMBLE_CONFIG = cfg
            return cfg
    except Exception:
        pass
    _ENSEMBLE_CONFIG = _DEFAULT_ENSEMBLE_CONFIG.copy()
    return _ENSEMBLE_CONFIG


def _get_ensemble_config() -> dict:
    """Return cached ensemble config (lazy load on first call)."""
    global _ENSEMBLE_CONFIG
    if _ENSEMBLE_CONFIG is None:
        return _load_ensemble_config()
    return _ENSEMBLE_CONFIG


def reload_ensemble_config() -> dict:
    """Force reload ensemble config from DB (called after PUT /ensemble/config)."""
    global _ENSEMBLE_CONFIG
    _ENSEMBLE_CONFIG = None
    return _load_ensemble_config()


# ── ML Predictor (lazy load) ─────────────────────────────────────────────────
_ML_ARTIFACT: Optional[dict] = None
_ML_ARTIFACT_LOADED = False


def _get_ml_path() -> str:
    return os.path.join(os.path.dirname(__file__), "../../../../ml")


def _ensure_ml_path():
    p = _get_ml_path()
    if p not in sys.path:
        sys.path.insert(0, p)


def _get_ml_artifact() -> Optional[dict]:
    global _ML_ARTIFACT, _ML_ARTIFACT_LOADED
    if _ML_ARTIFACT_LOADED:
        return _ML_ARTIFACT
    try:
        _ensure_ml_path()
        from ml_predictor_service import load_active_ml_model
        _ML_ARTIFACT = load_active_ml_model()
    except Exception:
        _ML_ARTIFACT = None
    _ML_ARTIFACT_LOADED = True
    return _ML_ARTIFACT


def reload_ml_artifact():
    global _ML_ARTIFACT, _ML_ARTIFACT_LOADED
    _ML_ARTIFACT_LOADED = False
    _ML_ARTIFACT = None
    return _get_ml_artifact()


def _predict_ml(features_row: dict) -> dict:
    """ML 분류 예측 실행. 모델 없으면 neutral 반환."""
    artifact = _get_ml_artifact()
    if artifact is None:
        return {"direction": 0, "prob_up": 0.333, "prob_down": 0.333, "available": False}
    try:
        _ensure_ml_path()
        from ml_predictor_service import predict
        return predict(features_row, artifact=artifact)
    except Exception:
        return {"direction": 0, "prob_up": 0.333, "prob_down": 0.333, "available": False}


# ── ML Regressor (lazy load) ──────────────────────────────────────────────────
_ML_REGRESSOR_SERVICE = None
_ML_REGRESSOR_LOADED = False


def _get_regressor_service():
    global _ML_REGRESSOR_SERVICE, _ML_REGRESSOR_LOADED
    if _ML_REGRESSOR_LOADED:
        return _ML_REGRESSOR_SERVICE
    try:
        _ensure_ml_path()
        from ml_regressor_service import get_regressor_service
        _ML_REGRESSOR_SERVICE = get_regressor_service()
    except Exception:
        _ML_REGRESSOR_SERVICE = None
    _ML_REGRESSOR_LOADED = True
    return _ML_REGRESSOR_SERVICE


def _predict_ml_regression(features_row: dict) -> dict:
    """ML 회귀 예측. 1h/3h/6h 수익률 + 신뢰구간 반환."""
    _null = {
        "ml_reg_available": False,
        "ml_return_1h": 0.0, "ml_lower_1h": 0.0, "ml_upper_1h": 0.0,
        "ml_conf_width_1h": 0.0,
        "ml_return_3h": 0.0, "ml_return_6h": 0.0,
    }
    svc = _get_regressor_service()
    if svc is None:
        return _null
    try:
        results = svc.predict(features_row)
        r1h = results.get("1h", {})
        r3h = results.get("3h", {})
        r6h = results.get("6h", {})
        if not r1h.get("available"):
            return _null
        return {
            "ml_reg_available": True,
            "ml_return_1h": r1h["predicted_return"],
            "ml_lower_1h": r1h["lower_10"],
            "ml_upper_1h": r1h["upper_90"],
            "ml_conf_width_1h": r1h["confidence_width"],
            "ml_return_3h": r3h.get("predicted_return", 0.0),
            "ml_return_6h": r6h.get("predicted_return", 0.0),
        }
    except Exception:
        return _null


# ── DB Helpers ────────────────────────────────────────────────────────────────

def _fetch_latest_features(exchange: str, symbol: str) -> Optional[dict]:
    try:
        conn = get_db_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM features.eth_features
                WHERE exchange = %s AND symbol = %s
                  AND atr_14 IS NOT NULL
                  AND rsi_14 IS NOT NULL
                ORDER BY ts DESC
                LIMIT 1
            """, (exchange, symbol))
            row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _upsert_signal(exchange: str, symbol: str, ts: datetime,
                   timeframe: str, payload: dict):
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO features.signals (
                    exchange, symbol, ts, timeframe,
                    signal, score, confidence,
                    tech_score, ml_direction, ml_prob_up, ml_prob_down,
                    mtf_1h, mtf_4h, vol_regime,
                    atr_14, current_close, tp_price, sl_price,
                    rr_ratio, position_size_pct, computed_at
                ) VALUES %s
                ON CONFLICT (exchange, symbol, ts, timeframe) DO UPDATE SET
                    signal            = EXCLUDED.signal,
                    score             = EXCLUDED.score,
                    confidence        = EXCLUDED.confidence,
                    tech_score        = EXCLUDED.tech_score,
                    ml_direction      = EXCLUDED.ml_direction,
                    ml_prob_up        = EXCLUDED.ml_prob_up,
                    ml_prob_down      = EXCLUDED.ml_prob_down,
                    mtf_1h            = EXCLUDED.mtf_1h,
                    mtf_4h            = EXCLUDED.mtf_4h,
                    vol_regime        = EXCLUDED.vol_regime,
                    atr_14            = EXCLUDED.atr_14,
                    current_close     = EXCLUDED.current_close,
                    tp_price          = EXCLUDED.tp_price,
                    sl_price          = EXCLUDED.sl_price,
                    rr_ratio          = EXCLUDED.rr_ratio,
                    position_size_pct = EXCLUDED.position_size_pct,
                    computed_at       = EXCLUDED.computed_at
            """, [(
                exchange, symbol, ts, timeframe,
                payload["signal"], payload["score"], payload["confidence"],
                payload["tech_score"],
                payload["ml_direction"], payload["ml_prob_up"], payload["ml_prob_down"],
                payload["mtf_1h"], payload["mtf_4h"], payload["vol_regime"],
                payload["atr_14"], payload["current_close"],
                payload.get("tp_price"), payload.get("sl_price"),
                payload["rr_ratio"], payload["position_size_pct"],
                datetime.now(timezone.utc),
            )])
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Technical Composite Score ─────────────────────────────────────────────────

def _compute_tech_score(row: dict) -> float:
    """
    기술지표로부터 -1 ~ +1 범위의 합성 스코어 계산.
    각 지표를 정규화해서 가중 평균.
    """
    score = 0.0
    weight_total = 0.0

    # RSI (14): 70 이상 → 매도 압력, 30 이하 → 매수 압력
    rsi = row.get("rsi_14")
    if rsi is not None:
        rsi = float(rsi)
        # 50 중심으로 정규화: (rsi - 50) / 50 → [-1, +1]
        rsi_score = (rsi - 50.0) / 50.0
        # 단 과매수/과매도 구간에선 역방향 신호
        if rsi >= 70:
            rsi_score = -0.6  # 과매수 → 매도 압력
        elif rsi <= 30:
            rsi_score = 0.6   # 과매도 → 매수 압력
        score += 0.25 * rsi_score
        weight_total += 0.25

    # MACD: 히스토그램 방향이 핵심
    macd_hist = row.get("macd_hist")
    macd_line = row.get("macd_line")
    if macd_hist is not None and macd_line is not None:
        macd_hist = float(macd_hist)
        macd_line = float(macd_line)
        # 히스토그램이 0 위 = bullish, 아래 = bearish
        # 절대값으로 정규화 (너무 크면 cap)
        close = float(row.get("close") or 1.0)
        macd_norm = macd_hist / (close * 0.01 + 1e-9)  # 종가 대비 1% 기준
        macd_score = max(-1.0, min(1.0, macd_norm))
        score += 0.25 * macd_score
        weight_total += 0.25

    # Bollinger Band %B: 0 이하 = oversold, 1 이상 = overbought
    bb_pct = row.get("bb_pct")
    if bb_pct is not None:
        bb_pct = float(bb_pct)
        if bb_pct > 1.0:
            bb_score = -0.7   # 상단 돌파 → 과매수
        elif bb_pct < 0.0:
            bb_score = 0.7    # 하단 돌파 → 과매도
        else:
            bb_score = (bb_pct - 0.5) * 2  # [-1, +1] 정규화
        score += 0.20 * bb_score
        weight_total += 0.20

    # Stochastic: 20 이하 = oversold, 80 이상 = overbought
    stoch_k = row.get("stoch_k")
    stoch_d = row.get("stoch_d")
    if stoch_k is not None and stoch_d is not None:
        stoch_k = float(stoch_k)
        stoch_d = float(stoch_d)
        stoch_score = (stoch_k - 50.0) / 50.0
        if stoch_k >= 80:
            stoch_score = -0.5
        elif stoch_k <= 20:
            stoch_score = 0.5
        # K가 D 위에 있으면 추가 강세 신호
        if stoch_k > stoch_d:
            stoch_score += 0.1
        else:
            stoch_score -= 0.1
        stoch_score = max(-1.0, min(1.0, stoch_score))
        score += 0.15 * stoch_score
        weight_total += 0.15

    # EMA 정렬: close vs EMA20 vs EMA50
    ema_20 = row.get("ema_20")
    ema_50 = row.get("ema_50")
    close_val = row.get("close")
    if ema_20 is not None and ema_50 is not None and close_val is not None:
        close_val = float(close_val)
        ema_20 = float(ema_20)
        ema_50 = float(ema_50)
        ema_score = 0.0
        if close_val > ema_20 > ema_50:
            ema_score = 0.8   # 완전 강세 정렬
        elif close_val < ema_20 < ema_50:
            ema_score = -0.8  # 완전 약세 정렬
        elif close_val > ema_20:
            ema_score = 0.3
        elif close_val < ema_20:
            ema_score = -0.3
        score += 0.15 * ema_score
        weight_total += 0.15

    if weight_total <= 0:
        return 0.0
    return round(score, 4)


# ── Main Signal Computation ───────────────────────────────────────────────────

def compute_signal(
    exchange: str = "binance",
    symbol: str = "ETH/USDT",
    timeframe: str = "15m",
    atr_tp_multiple: float = 2.0,
    atr_sl_multiple: float = 1.0,
    min_ml_prob: float = 0.45,
    require_mtf_agreement: bool = True,
    max_kelly_fraction: float = 0.25,
) -> dict[str, Any]:
    """
    현재 신호 계산 (ARIMA 없음).
    기술지표 + ML + MTF 기반으로 LONG/SHORT/NO-TRADE 결정.
    """
    # ── 1. 최신 features 로드 ─────────────────────────────────────────────
    features_row = _fetch_latest_features(exchange, symbol)
    if features_row is None:
        features_row = {}

    current_close = float(features_row.get("close") or 0.0)
    atr_14 = float(features_row.get("atr_14") or 0.0)
    vol_regime = str(features_row.get("vol_regime") or "unknown")
    mtf_1h = int(features_row.get("signal_1h") or 0)
    mtf_4h = int(features_row.get("signal_4h") or 0)

    # ── 2. 기술지표 합성 스코어 ──────────────────────────────────────────
    tech_score = _compute_tech_score(features_row)

    # ── 3. ML 분류 예측 ──────────────────────────────────────────────────
    ml_result = _predict_ml(features_row)
    ml_direction = ml_result["direction"]
    ml_prob_up = ml_result["prob_up"]
    ml_prob_down = ml_result["prob_down"]
    ml_available = ml_result["available"]

    # ── 4. ML 회귀 예측 (수익률 + 신뢰구간) ─────────────────────────────
    ml_reg = _predict_ml_regression(features_row)
    ml_reg_available = ml_reg["ml_reg_available"]
    ml_return_1h = ml_reg["ml_return_1h"]

    # ── 6. MTF 스코어 ────────────────────────────────────────────────────
    mtf_1h_score = mtf_1h * 0.5   # -0.5, 0, +0.5
    mtf_4h_score = mtf_4h * 0.5

    # ── 7. 최종 앙상블 스코어 (DB에서 로드된 가중치 사용) ───────────────
    cfg = _get_ensemble_config()
    W_TECH = cfg["w_tech"]
    W_ML_REG = cfg["w_ml_regressor"]
    W_ML_CLS = cfg["w_ml_classifier"]
    W_MTF1H = cfg["w_mtf_1h"]
    W_MTF4H = cfg["w_mtf_4h"]

    # ml_score를 regressor + classifier 가중 합산으로 분리
    if ml_reg_available:
        ci_confidence = max(0.5, 1.0 - ml_reg["ml_conf_width_1h"] * 10)
        ml_reg_score = max(-1.0, min(1.0, ml_return_1h / 0.01)) * ci_confidence
    else:
        ml_reg_score = 0.0
    ml_cls_score = ml_prob_up - ml_prob_down

    score = (
        W_TECH * tech_score
        + W_ML_REG * ml_reg_score
        + W_ML_CLS * ml_cls_score
        + W_MTF1H * mtf_1h_score
        + W_MTF4H * mtf_4h_score
    )

    # ── 8. 변동성 레짐에 따른 임계값 조정 ───────────────────────────────
    threshold = cfg["threshold_normal"]
    ml_min = cfg.get("min_ml_prob_normal", min_ml_prob)
    if vol_regime == "high":
        threshold = cfg["threshold_high_vol"]
        ml_min = max(ml_min, cfg.get("min_ml_prob_high_vol", 0.52))

    # ── 9. 신호 결정 + 필터 ─────────────────────────────────────────────
    signal = "no-trade"

    long_ok = (
        score > threshold
        and (not ml_available or ml_prob_up >= ml_min)
        and (not require_mtf_agreement or mtf_1h >= 0)
    )
    short_ok = (
        score < -threshold
        and (not ml_available or ml_prob_down >= ml_min)
        and (not require_mtf_agreement or mtf_1h <= 0)
    )

    if long_ok:
        signal = "long"
    elif short_ok:
        signal = "short"

    # ── 10. ATR 기반 TP/SL ───────────────────────────────────────────────
    rr_ratio = atr_tp_multiple / max(atr_sl_multiple, 0.01)
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None

    if atr_14 > 0 and current_close > 0:
        if signal == "long":
            tp_price = round(current_close + atr_tp_multiple * atr_14, 4)
            sl_price = round(current_close - atr_sl_multiple * atr_14, 4)
        elif signal == "short":
            tp_price = round(current_close - atr_tp_multiple * atr_14, 4)
            sl_price = round(current_close + atr_sl_multiple * atr_14, 4)

    # ── 11. Half-Kelly 포지션 사이징 ─────────────────────────────────────
    if signal == "long":
        prob_win = ml_prob_up if ml_available else 0.5
    elif signal == "short":
        prob_win = ml_prob_down if ml_available else 0.5
    else:
        prob_win = 0.5

    prob_loss = 1.0 - prob_win
    kelly = max(0.0, (prob_win * rr_ratio - prob_loss) / rr_ratio)
    position_size_pct = round(min(kelly * 0.5, max_kelly_fraction) * 100, 2)

    # ── 12. Confidence ───────────────────────────────────────────────────
    confidence = round(min(abs(score) * 2.0, 1.0), 4)

    result: dict[str, Any] = {
        "signal": signal,
        "score": round(score, 4),
        "confidence": confidence,
        "tech_score": round(tech_score, 4),
        # ML
        "ml_direction": ml_direction,
        "ml_prob_up": round(ml_prob_up, 4),
        "ml_prob_down": round(ml_prob_down, 4),
        "ml_available": ml_available,
        # ML Regression (수익률 예측 + 신뢰구간)
        "ml_reg_available": ml_reg["ml_reg_available"],
        "ml_return_1h": round(ml_reg["ml_return_1h"], 6),
        "ml_lower_1h": round(ml_reg["ml_lower_1h"], 6),
        "ml_upper_1h": round(ml_reg["ml_upper_1h"], 6),
        "ml_conf_width_1h": round(ml_reg["ml_conf_width_1h"], 6),
        "ml_return_3h": round(ml_reg["ml_return_3h"], 6),
        "ml_return_6h": round(ml_reg["ml_return_6h"], 6),
        # MTF
        "mtf_1h": mtf_1h,
        "mtf_4h": mtf_4h,
        # Regime
        "vol_regime": vol_regime,
        "atr_14": round(atr_14, 4),
        "current_close": round(current_close, 4),
        # Risk
        "tp_price": tp_price,
        "sl_price": sl_price,
        "rr_ratio": round(rr_ratio, 2),
        "position_size_pct": position_size_pct,
        # Meta
        "timeframe": timeframe,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── 12. DB 저장 ──────────────────────────────────────────────────────
    if features_row.get("ts"):
        _upsert_signal(exchange, symbol, features_row["ts"], timeframe, result)

    return result
