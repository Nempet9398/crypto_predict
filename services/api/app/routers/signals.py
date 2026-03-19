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
    signal_filter: str = Query("all", description="all | strong-long | long | neutral | short | strong-short | no-trade"),
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

# 학습 단계 정의: (단계명, 시작 %, 끝 %)
_TRAIN_STAGES = [
    ("데이터 로드 중",           0,  10),
    ("피처 선택 중",            10,  20),
    ("Optuna 튜닝 중 (XGB)",    20,  45),
    ("Optuna 튜닝 중 (LGB)",    45,  70),
    ("최종 모델 CV 평가 중",     70,  85),
    ("모델 저장 & 등록 중",      85,  95),
    ("완료",                    95, 100),
]

_training_status = {
    "running": False,
    "last_result": None,
    "started_at": None,
    "progress": 0,          # 0~100
    "stage": "",            # 현재 단계명
    "params": {},           # 학습에 사용된 파라미터
    "logs": [],             # 최근 로그 (최대 50줄)
}


def _parse_progress(line: str) -> tuple[int | None, str | None]:
    """stdout 한 줄에서 진행률과 단계명 추출."""
    for stage_name, pct_start, pct_end in _TRAIN_STAGES:
        if any(kw in line for kw in [
            "Loading features", "피처 로드",
            "Feature selection", "피처 선택", "Step 1",
            "Optuna tuning", "Step 2", "XGB",
            "LGB", "LightGBM",
            "Outer CV", "Walk-Forward", "Step 3",
            "Saving", "저장", "Step 4",
            "Finished", "완료", "Done",
        ]):
            # 단계별 키워드 매핑
            kw_map = [
                (["Loading features", "피처 로드"],                   ("데이터 로드 중",          5)),
                (["Step 1", "Feature selection", "피처 선택"],        ("피처 선택 중",            15)),
                (["Optuna", "Step 2", "XGB", "trials"],               ("Optuna 튜닝 중 (XGB)",    35)),
                (["LGB", "LightGBM"],                                 ("Optuna 튜닝 중 (LGB)",    60)),
                (["Outer CV", "Walk-Forward", "Step 3", "fold"],      ("최종 모델 CV 평가 중",    78)),
                (["Saving", "저장", "Registering", "Step 4"],         ("모델 저장 & 등록 중",     90)),
                (["Finished", "완료", "Done", "Best model"],          ("완료",                   100)),
            ]
            for keywords, (name, pct) in kw_map:
                if any(k in line for k in keywords):
                    return pct, name
    return None, None


def _run_training(
    lookback_days: int,
    horizon_bars: int,
    n_trials: int,
    outer_cv_splits: int,
    timeout_sec: int,
    train_regressor: bool,
    quality_gate: float,
):
    global _training_status
    now = datetime.now(timezone.utc)
    _training_status.update({
        "running": True,
        "started_at": now.isoformat(),
        "progress": 0,
        "stage": "학습 준비 중",
        "logs": [],
        "params": {
            "lookback_days": lookback_days,
            "horizon_bars": horizon_bars,
            "n_trials": n_trials,
            "outer_cv_splits": outer_cv_splits,
            "train_regressor": train_regressor,
            "quality_gate": quality_gate,
        },
        "last_result": None,
    })

    ml_dir = os.path.join(os.path.dirname(__file__), "../../../../ml")

    def _run_script(script: str, extra_args: list[str]) -> tuple[bool, str, str]:
        cmd = [
            sys.executable, script,
            f"--lookback-days={lookback_days}",
            f"--horizon-bars={horizon_bars}",
            f"--n-trials={n_trials}",
            f"--outer-cv-splits={outer_cv_splits}",
        ] + extra_args
        try:
            proc = subprocess.Popen(
                cmd, cwd=ml_dir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
            stdout_lines = []
            # stdout를 실시간으로 읽어서 진행률 파싱
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                stdout_lines.append(line)
                _training_status["logs"] = _training_status["logs"][-49:] + [line]
                pct, stage = _parse_progress(line)
                if pct is not None:
                    _training_status["progress"] = pct
                    _training_status["stage"] = stage

            proc.wait(timeout=timeout_sec)
            stderr_out = proc.stderr.read()
            success = proc.returncode == 0
            return success, "\n".join(stdout_lines[-2000:]), stderr_out[-1000:]
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "", f"타임아웃 ({timeout_sec}초 초과)"
        except Exception as exc:
            return False, "", str(exc)

    try:
        # 분류 모델
        _training_status["stage"] = "분류 모델 학습 중"
        _training_status["progress"] = 5
        ok1, out1, err1 = _run_script("train_ml_predictor.py", [])

        # 회귀 모델 (옵션)
        ok2, out2, err2 = True, "", ""
        if train_regressor:
            _training_status["stage"] = "회귀 모델 학습 중"
            _training_status["progress"] = 55
            ok2, out2, err2 = _run_script("train_ml_regressor.py", [])

        success = ok1 and ok2
        _training_status["progress"] = 100 if success else _training_status["progress"]
        _training_status["stage"] = "완료" if success else "오류 발생"
        _training_status["last_result"] = {
            "success": success,
            "stdout": (out1 + "\n" + out2).strip()[-2000:],
            "stderr": (err1 + "\n" + err2).strip()[-1000:],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if success:
            reload_ml_artifact()
    except Exception as e:
        _training_status["stage"] = "오류 발생"
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
    lookback_days: int = Query(60, ge=7, le=730, description="학습 데이터 기간 (일)"),
    horizon_bars: int = Query(4, ge=1, le=32, description="예측 봉 수: 4=1h, 12=3h, 24=6h"),
    n_trials: int = Query(40, ge=5, le=200, description="Optuna 튜닝 횟수 (많을수록 정확, 느림)"),
    outer_cv_splits: int = Query(5, ge=3, le=10, description="Walk-Forward CV 폴드 수"),
    timeout_sec: int = Query(600, ge=60, le=7200, description="최대 학습 시간(초)"),
    train_regressor: bool = Query(True, description="회귀 모델(수익률 예측) 함께 학습"),
    quality_gate: float = Query(0.50, ge=0.45, le=0.70, description="모델 승격 최소 방향성 정확도"),
):
    """ML 예측 모델 백그라운드 학습 시작. /ml/train-status 로 진행률 확인."""
    if _training_status["running"]:
        return {
            "status": "already_running",
            "started_at": _training_status["started_at"],
            "progress": _training_status["progress"],
            "stage": _training_status["stage"],
        }
    background_tasks.add_task(
        _run_training,
        lookback_days, horizon_bars, n_trials,
        outer_cv_splits, timeout_sec, train_regressor, quality_gate,
    )
    return {
        "status": "started",
        "params": {
            "lookback_days": lookback_days,
            "horizon_bars": horizon_bars,
            "n_trials": n_trials,
            "outer_cv_splits": outer_cv_splits,
            "timeout_sec": timeout_sec,
            "train_regressor": train_regressor,
            "quality_gate": quality_gate,
        },
        "message": f"{lookback_days}일 데이터 / Optuna {n_trials}회 튜닝으로 학습 시작. /ml/train-status 에서 진행률 확인.",
    }


@router.get("/ml/train-status")
def get_training_status():
    """ML 학습 진행 상태 확인 (폴링용)."""
    return {
        "running": _training_status["running"],
        "started_at": _training_status["started_at"],
        "progress": _training_status["progress"],       # 0~100
        "stage": _training_status["stage"],              # 현재 단계명
        "params": _training_status["params"],
        "logs": _training_status["logs"][-20:],          # 최근 20줄
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

# ── 백테스트 ──────────────────────────────────────────────────────────────────

@router.get("/backtest/strategy")
def run_backtest_strategy(
    lookback_days: int = Query(14, ge=1, le=365, description="백테스트 기간 (일)"),
    horizon_hours: int = Query(6, ge=1, le=168, description="신호 호라이즌 (시간, 정보용)"),
    up_prob_threshold: float = Query(0.6, ge=0.0, le=1.0, description="롱 진입 최소 상승 확률"),
    down_prob_threshold: float = Query(0.6, ge=0.0, le=1.0, description="숏 진입 최소 하락 확률"),
    up_pct: float = Query(1.0, description="미사용 (호환성 유지)"),
    down_pct: float = Query(1.0, description="미사용 (호환성 유지)"),
    fee_bps: int = Query(4, ge=0, le=50, description="거래 수수료 (basis points)"),
    slippage_bps: int = Query(2, ge=0, le=50, description="슬리피지 (basis points)"),
    timeframe: str = Query(TIMEFRAME),
):
    """
    과거 신호(actual_return 채워진 것)를 이용한 전략 백테스트.
    수익률 시뮬레이션: 롱/숏 신호 기반, 수수료+슬리피지 차감.
    """
    import math

    rows = fetch_all(
        """
        SELECT ts, signal, actual_return, ml_prob_up, ml_prob_down
        FROM features.signals
        WHERE exchange = %s AND symbol = %s AND timeframe = %s
          AND actual_return IS NOT NULL
          AND ts >= NOW() - INTERVAL %s
        ORDER BY ts ASC
        """,
        [EXCHANGE, SYMBOL, timeframe, f"{lookback_days} days"],
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="백테스트에 사용할 실제 결과 데이터가 없습니다. 신호 실현값이 아직 계산되지 않았습니다.",
        )

    cost = (fee_bps + slippage_bps) / 10000 * 2  # 왕복 비용

    trades = []
    for row in rows:
        sig = row["signal"]
        prob_up = float(row.get("ml_prob_up") or 0.0)
        prob_down = float(row.get("ml_prob_down") or 0.0)
        actual_return = float(row["actual_return"])

        if sig in ("long", "strong-long") and prob_up >= up_prob_threshold:
            trades.append({"ts": row["ts"].isoformat(), "pnl": actual_return - cost})
        elif sig in ("short", "strong-short") and prob_down >= down_prob_threshold:
            trades.append({"ts": row["ts"].isoformat(), "pnl": -actual_return - cost})

    if not trades:
        raise HTTPException(
            status_code=404,
            detail=f"확률 임계값({up_prob_threshold}/{down_prob_threshold})을 충족하는 신호가 없습니다. 임계값을 낮추거나 기간을 늘려보세요.",
        )

    returns = [t["pnl"] for t in trades]
    n = len(returns)

    # 누적 PnL + MDD 계산
    cum_pnl = 0.0
    peak = 0.0
    mdd = 0.0
    mdd_start_idx = 0
    max_mdd_dur = 0
    pnl_series = []

    for i, t in enumerate(trades):
        cum_pnl += t["pnl"]
        if cum_pnl > peak:
            peak = cum_pnl
            mdd_start_idx = i
        else:
            dur = i - mdd_start_idx
            if dur > max_mdd_dur:
                max_mdd_dur = dur
        dd = peak - cum_pnl
        if dd > mdd:
            mdd = dd
        pnl_series.append({"ts": t["ts"], "cum_pnl": round(cum_pnl, 6)})

    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / n
    std_r = math.sqrt(variance) if variance > 0 else 1e-9

    neg_returns = [r for r in returns if r < 0]
    downside_var = sum(r ** 2 for r in neg_returns) / len(neg_returns) if neg_returns else 0.0
    std_neg = math.sqrt(downside_var) if downside_var > 0 else 1e-9

    # 연간화 인수: 거래 빈도 기반
    trades_per_year = max(n / max(lookback_days, 1) * 365, 1)
    ann = math.sqrt(trades_per_year)

    sharpe = mean_r / std_r * ann
    sortino = mean_r / std_neg * ann
    calmar = cum_pnl / mdd if mdd > 1e-9 else (float("inf") if cum_pnl > 0 else 0.0)

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    win_rate = len(wins) / n
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-9 else 999.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1e-9
    payoff_ratio = avg_win / avg_loss if avg_loss > 1e-9 else 0.0

    return {
        "summary": {
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "calmar": round(calmar, 3),
            "profit_factor": round(profit_factor, 3),
            "win_rate": round(win_rate, 4),
            "payoff_ratio": round(payoff_ratio, 3),
            "cumulative_pnl": round(cum_pnl, 6),
            "mdd": round(-mdd, 6),
            "mdd_duration_bars": max_mdd_dur,
            "n_trades": n,
        },
        "pnl_series": pnl_series,
        "timeframe": timeframe,
        "horizon_hours": horizon_hours,
    }


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
            -- 수익률 KPI (Primary KPI)
            AVG(
              CASE WHEN pred_direction = 1 THEN actual_return
                   WHEN pred_direction = -1 THEN -actual_return
                   ELSE NULL END
            ) as avg_return_per_trade,
            SUM(
              CASE WHEN (pred_direction = 1 AND actual_return > 0.003)
                     OR (pred_direction = -1 AND actual_return < -0.003)
                   THEN 1 ELSE 0 END
            ) as profit_trades,
            SUM(CASE WHEN pred_direction != 0 THEN 1 ELSE 0 END) as directional_total,
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
    profit_trades = int(row["profit_trades"] or 0)
    directional_total = int(row["directional_total"] or 0)

    return {
        "period_days": days,
        "horizon_bars": horizon_bars,
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total > 0 else None,
        "long_accuracy": round(long_correct / long_total, 4) if long_total > 0 else None,
        "short_accuracy": round(short_correct / short_total, 4) if short_total > 0 else None,
        "mae": round(float(row["mae"]), 6) if row["mae"] else None,
        # 수익률 KPI (Primary KPI)
        "avg_return_per_trade": round(float(row["avg_return_per_trade"]), 6) if row["avg_return_per_trade"] else None,
        "win_rate": round(profit_trades / directional_total, 4) if directional_total > 0 else None,
        "profit_trades": profit_trades,
        "directional_total": directional_total,
        "from_ts": row["from_ts"].isoformat() if row["from_ts"] else None,
        "to_ts": row["to_ts"].isoformat() if row["to_ts"] else None,
    }
