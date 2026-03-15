"""
Signals Router
- GET /signals/current   — 현재 신호 (기술지표 + ML 기반)
- GET /signals/history   — 과거 신호 기록
- POST /ml/train         — ML 모델 학습 트리거
- GET /ml/status         — ML 모델 상태
- GET /ml/predictions    — ML 예측 기록
- GET /ml/accuracy       — 예측 정확도 통계
"""
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.core.db import fetch_all, fetch_one
from app.core.signal_service import compute_signal, reload_ml_artifact

router = APIRouter(tags=["signals"])

EXCHANGE = os.getenv("EXCHANGE", "binance")
SYMBOL = os.getenv("SYMBOL", "ETH/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")


# ── 현재 신호 ─────────────────────────────────────────────────────────────────

@router.get("/signals/current")
def get_current_signal(
    atr_tp: float = Query(2.0, description="TP = 진입가 + atr_tp × ATR"),
    atr_sl: float = Query(1.0, description="SL = 진입가 - atr_sl × ATR"),
    min_ml_prob: float = Query(0.45),
    require_mtf: bool = Query(True),
):
    """현재 기술지표 + ML 앙상블 신호 반환."""
    result = compute_signal(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        atr_tp_multiple=atr_tp,
        atr_sl_multiple=atr_sl,
        min_ml_prob=min_ml_prob,
        require_mtf_agreement=require_mtf,
    )
    return result


# ── 신호 이력 ─────────────────────────────────────────────────────────────────

@router.get("/signals/history")
def get_signal_history(
    limit: int = Query(50, ge=1, le=500),
    signal_filter: str = Query("all", description="all | long | short | no-trade"),
):
    """DB에 저장된 과거 신호 목록."""
    where = "exchange = %s AND symbol = %s AND timeframe = %s"
    params = [EXCHANGE, SYMBOL, TIMEFRAME]

    if signal_filter != "all":
        where += " AND signal = %s"
        params.append(signal_filter)

    rows = fetch_all(f"""
        SELECT ts, signal, score, confidence, tech_score,
               ml_prob_up, ml_prob_down, mtf_1h, mtf_4h, vol_regime,
               atr_14, current_close, tp_price, sl_price, rr_ratio,
               position_size_pct, computed_at
        FROM features.signals
        WHERE {where}
        ORDER BY ts DESC
        LIMIT %s
    """, params + [limit])

    signals = []
    for row in (rows or []):
        r = dict(row)
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif v is not None:
                try:
                    r[k] = float(v) if isinstance(v, (int, float)) else v
                except (TypeError, ValueError):
                    pass
        signals.append(r)

    return {"signals": signals, "count": len(signals)}


# ── ML 모델 상태 ──────────────────────────────────────────────────────────────

@router.get("/ml/status")
def get_ml_status():
    """ML 모델 메타데이터 반환."""
    import json
    meta_path = os.path.join(
        os.getenv("MODEL_REGISTRY_DIR", "/app/model_registry"),
        "eth_predictor_meta.json"
    )
    model_path = os.path.join(
        os.getenv("MODEL_REGISTRY_DIR", "/app/model_registry"),
        "eth_predictor.pkl"
    )
    if not os.path.exists(meta_path):
        return {"trained": False, "message": "모델이 아직 학습되지 않았습니다."}
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        meta["trained"] = True
        meta["model_exists"] = os.path.exists(model_path)
        return meta
    except Exception as e:
        return {"trained": False, "error": str(e)}


# ── ML 학습 트리거 ────────────────────────────────────────────────────────────

_training_status = {"running": False, "last_result": None, "started_at": None}


def _run_training(lookback_days: int, horizon_bars: int):
    global _training_status
    _training_status["running"] = True
    _training_status["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        ml_dir = os.path.join(os.path.dirname(__file__), "../../../../ml")
        result = subprocess.run(
            [sys.executable, "train_ml_predictor.py",
             f"--lookback-days={lookback_days}",
             f"--horizon-bars={horizon_bars}"],
            capture_output=True, text=True, cwd=ml_dir, timeout=600,
        )
        success = result.returncode == 0
        _training_status["last_result"] = {
            "success": success,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if success:
            reload_ml_artifact()  # 캐시 갱신
    except Exception as e:
        _training_status["last_result"] = {
            "success": False,
            "error": str(e),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        _training_status["running"] = False


@router.post("/ml/train")
def train_ml_model(
    background_tasks: BackgroundTasks,
    lookback_days: int = Query(60, ge=7, le=365),
    horizon_bars: int = Query(4, ge=1, le=32, description="예측 대상: 4=1h, 16=4h, 32=8h"),
):
    """ML 예측 모델 백그라운드 학습 시작."""
    if _training_status["running"]:
        return {"status": "already_running", "started_at": _training_status["started_at"]}
    background_tasks.add_task(_run_training, lookback_days, horizon_bars)
    return {
        "status": "started",
        "lookback_days": lookback_days,
        "horizon_bars": horizon_bars,
        "message": f"{lookback_days}일 데이터로 학습 시작. /ml/train-status 에서 진행 상태 확인 가능.",
    }


@router.get("/ml/train-status")
def get_training_status():
    """ML 학습 진행 상태 확인."""
    return {
        "running": _training_status["running"],
        "started_at": _training_status["started_at"],
        "last_result": _training_status["last_result"],
    }


# ── ML 예측 기록 ──────────────────────────────────────────────────────────────

@router.get("/ml/predictions")
def get_ml_predictions(
    limit: int = Query(100, ge=1, le=1000),
    horizon_bars: int = Query(4),
):
    """저장된 ML 예측값 목록 (actual 포함)."""
    rows = fetch_all("""
        SELECT ts, horizon_bars, pred_return, pred_close, pred_direction,
               pred_confidence, actual_return, actual_close, actual_direction,
               model_id, computed_at
        FROM features.ml_predictions
        WHERE exchange = %s AND symbol = %s AND horizon_bars = %s
        ORDER BY ts DESC
        LIMIT %s
    """, [EXCHANGE, SYMBOL, horizon_bars, limit])

    preds = []
    for row in (rows or []):
        r = dict(row)
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
        preds.append(r)

    return {"predictions": preds, "count": len(preds)}


# ── ML 정확도 통계 ────────────────────────────────────────────────────────────

@router.get("/ml/accuracy")
def get_ml_accuracy(
    days: int = Query(30, ge=1, le=365, description="기간 (일)"),
    horizon_bars: int = Query(4),
):
    """
    예측 정확도 계산.
    actual_direction이 채워진 레코드 기준.
    """
    row = fetch_one("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN pred_direction = actual_direction THEN 1 ELSE 0 END) as correct,
            SUM(CASE WHEN pred_direction = 1 AND actual_direction = 1 THEN 1 ELSE 0 END) as long_correct,
            SUM(CASE WHEN pred_direction = 1 THEN 1 ELSE 0 END) as long_total,
            SUM(CASE WHEN pred_direction = -1 AND actual_direction = -1 THEN 1 ELSE 0 END) as short_correct,
            SUM(CASE WHEN pred_direction = -1 THEN 1 ELSE 0 END) as short_total,
            AVG(ABS(pred_return - actual_return)) as mae,
            MIN(ts) as from_ts,
            MAX(ts) as to_ts
        FROM features.ml_predictions
        WHERE exchange = %s AND symbol = %s
          AND horizon_bars = %s
          AND actual_direction IS NOT NULL
          AND ts >= NOW() - INTERVAL %s
    """, [EXCHANGE, SYMBOL, horizon_bars, f"{days} days"])

    if not row or not row["total"]:
        return {
            "period_days": days,
            "horizon_bars": horizon_bars,
            "total": 0,
            "accuracy": None,
            "message": "아직 실제 결과가 채워진 예측이 없습니다.",
        }

    total = int(row["total"])
    correct = int(row["correct"] or 0)
    long_correct = int(row["long_correct"] or 0)
    long_total = int(row["long_total"] or 0)
    short_correct = int(row["short_correct"] or 0)
    short_total = int(row["short_total"] or 0)

    return {
        "period_days": days,
        "horizon_bars": horizon_bars,
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total > 0 else None,
        "long_accuracy": round(long_correct / long_total, 4) if long_total > 0 else None,
        "short_accuracy": round(short_correct / short_total, 4) if short_total > 0 else None,
        "mae": round(float(row["mae"]), 6) if row["mae"] else None,
        "from_ts": row["from_ts"].isoformat() if row["from_ts"] else None,
        "to_ts": row["to_ts"].isoformat() if row["to_ts"] else None,
    }
