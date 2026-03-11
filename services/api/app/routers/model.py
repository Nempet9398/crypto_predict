from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.arima_service import (
    backtest_strategy,
    compute_signal,
    forecast_with_latest_model,
    list_models,
    model_status,
    parse_order,
    parse_periods,
    parse_seasonal_order,
    train_arima,
)
from app.core.ensemble_service import compute_ensemble_signal

router = APIRouter(tags=["model"])
UTC = timezone.utc


class TrainRequest(BaseModel):
    lookback_days: int = 7
    order: str | None = None
    auto_order: bool | None = None
    order_metric: str | None = None
    max_p: int = 3
    max_d: int = 2
    max_q: int = 3
    model_family: str | None = None
    seasonal_periods: str | None = None
    seasonal_order: str | None = None
    transform_mode: str | None = None
    backtest_hours: int | None = None
    conf_alpha: float | None = None
    set_active: bool = True
    symbol: str | None = None
    exchange: str | None = None
    timeframe: str | None = None
    start: str | None = None
    end: str | None = None
    horizon_hours: int | None = None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_tz_iso(ts: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    return ts.astimezone(tz).isoformat()


@router.post("/train")
def train(req: TrainRequest):
    try:
        order = parse_order(req.order)
        seasonal_order = parse_seasonal_order(req.seasonal_order)
        seasonal_periods = parse_periods(req.seasonal_periods)
        start = _parse_iso(req.start)
        end = _parse_iso(req.end)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        meta = train_arima(
            lookback_days=req.lookback_days,
            order=order,
            symbol=req.symbol,
            exchange=req.exchange,
            timeframe=req.timeframe,
            start=start,
            end=end,
            horizon_hours=req.horizon_hours,
            auto_order=req.auto_order,
            order_metric=req.order_metric,
            max_p=req.max_p,
            max_d=req.max_d,
            max_q=req.max_q,
            model_family=req.model_family,
            seasonal_periods=seasonal_periods,
            seasonal_order_pdq=seasonal_order,
            transform_mode=req.transform_mode,
            backtest_hours=req.backtest_hours,
            conf_alpha=req.conf_alpha,
            set_active=req.set_active,
            train_reason="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"학습 실패: {exc}") from exc

    return {
        "ok": True,
        "message": "ARIMA 학습 완료",
        "metadata": meta,
    }


@router.get("/forecast")
def forecast(
    horizon_hours: int = Query(6, ge=1, le=336),
    include_history_tail: int = Query(0, ge=0, le=500),
    model_id: str | None = Query(None),
    timeframe: str | None = Query(None),
    tz: str = Query("Asia/Seoul"),
):
    try:
        out = forecast_with_latest_model(
            horizon_hours=horizon_hours,
            include_history_tail=include_history_tail,
            timeframe=timeframe,
            model_id=model_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"예측 실패: {exc}") from exc

    try:
        points = [
            {
                "ts": _to_tz_iso(p["ts"], tz),
                "pred_close": p["pred_close"],
                "pred_low": p["pred_low"],
                "pred_high": p["pred_high"],
            }
            for p in out["points"]
        ]
        history_tail = [{"ts": _to_tz_iso(p["ts"], tz), "close": p["close"]} for p in out["history_tail"]]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}") from exc

    return {
        "model_id": out.get("model_id"),
        "exchange": out["exchange"],
        "symbol": out["symbol"],
        "timeframe": out["timeframe"],
        "horizon_hours": out["horizon_hours"],
        "horizon_steps": out.get("horizon_steps"),
        "conf_alpha": out["conf_alpha"],
        "tz": tz,
        "history_tail": history_tail,
        "points": points,
    }


@router.get("/models")
def models(limit: int = Query(50, ge=1, le=300)):
    try:
        return list_models(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"모델 목록 조회 실패: {exc}") from exc


@router.get("/signals")
def signals(
    horizon_hours: int = Query(6, ge=1, le=168),
    up_pct: float = Query(1.0, gt=0.0, le=50.0),
    down_pct: float = Query(1.0, gt=0.0, le=50.0),
    up_prob_threshold: float = Query(0.6, ge=0.0, le=1.0),
    down_prob_threshold: float = Query(0.6, ge=0.0, le=1.0),
    probability_method: str = Query("normal"),
    timeframe: str | None = Query(None),
    tz: str = Query("Asia/Seoul"),
):
    try:
        result = compute_signal(
            horizon_hours=horizon_hours,
            up_pct=up_pct,
            down_pct=down_pct,
            method=probability_method,
            up_prob_threshold=up_prob_threshold,
            down_prob_threshold=down_prob_threshold,
            timeframe=timeframe,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"시그널 계산 실패: {exc}") from exc

    try:
        return {
            **result,
            "timestamp": _to_tz_iso(result["timestamp"], tz),
            "tz": tz,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}") from exc


@router.get("/backtest/strategy")
def strategy_backtest(
    lookback_days: int = Query(14, ge=2, le=365),
    horizon_hours: int = Query(6, ge=1, le=72),
    up_prob_threshold: float = Query(0.6, ge=0.0, le=1.0),
    down_prob_threshold: float = Query(0.6, ge=0.0, le=1.0),
    up_pct: float = Query(1.0, gt=0.0, le=50.0),
    down_pct: float = Query(1.0, gt=0.0, le=50.0),
    fee_bps: float = Query(4.0, ge=0.0, le=500.0),
    slippage_bps: float = Query(2.0, ge=0.0, le=500.0),
    probability_method: str = Query("normal"),
    timeframe: str | None = Query(None),
    tz: str = Query("Asia/Seoul"),
):
    try:
        result = backtest_strategy(
            lookback_days=lookback_days,
            horizon_hours=horizon_hours,
            up_prob_threshold=up_prob_threshold,
            down_prob_threshold=down_prob_threshold,
            up_pct=up_pct,
            down_pct=down_pct,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            probability_method=probability_method,
            timeframe=timeframe,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"백테스트 실패: {exc}") from exc

    try:
        trades = [
            {
                **trade,
                "entry_ts": _to_tz_iso(trade["entry_ts"], tz),
                "exit_ts": _to_tz_iso(trade["exit_ts"], tz),
            }
            for trade in result["trades"]
        ]
        pnl_series = [{**point, "ts": _to_tz_iso(point["ts"], tz)} for point in result["pnl_series"]]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}") from exc

    return {
        "tz": tz,
        "lookback_days": lookback_days,
        "horizon_hours": horizon_hours,
        "up_prob_threshold": up_prob_threshold,
        "down_prob_threshold": down_prob_threshold,
        "up_pct": up_pct,
        "down_pct": down_pct,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "timeframe": result.get("timeframe"),
        "horizon_steps": result.get("horizon_steps"),
        "trades": trades,
        "pnl_series": pnl_series,
        "summary": result["summary"],
    }


@router.get("/model/status")
def get_model_status():
    st = model_status()
    return st


@router.get("/signals/ensemble")
def ensemble_signals(
    horizon_hours: int = Query(6, ge=1, le=168),
    atr_tp_multiple: float = Query(2.0, gt=0.0, le=10.0),
    atr_sl_multiple: float = Query(1.0, gt=0.0, le=10.0),
    require_mtf_agreement: bool = Query(True),
    min_ml_prob: float = Query(0.45, ge=0.0, le=1.0),
    min_arima_prob: float = Query(0.50, ge=0.0, le=1.0),
    max_kelly_fraction: float = Query(0.25, gt=0.0, le=1.0),
    timeframe: str | None = Query(None),
):
    """
    Compute ensemble trading signal combining ARIMA + ML + multi-timeframe alignment.

    Returns directional signal with ATR-based TP/SL and position sizing suggestion.
    """
    exchange = __import__("os").getenv("EXCHANGE", "binance")
    symbol = __import__("os").getenv("SYMBOL", "ETH/USDT")
    tf = timeframe or __import__("os").getenv("TIMEFRAME", "15m")

    try:
        result = compute_ensemble_signal(
            exchange=exchange,
            symbol=symbol,
            timeframe=tf,
            horizon_hours=horizon_hours,
            atr_tp_multiple=atr_tp_multiple,
            atr_sl_multiple=atr_sl_multiple,
            require_mtf_agreement=require_mtf_agreement,
            min_ml_prob=min_ml_prob,
            min_arima_prob=min_arima_prob,
            max_kelly_fraction=max_kelly_fraction,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"앙상블 시그널 계산 실패: {exc}") from exc

    return result
