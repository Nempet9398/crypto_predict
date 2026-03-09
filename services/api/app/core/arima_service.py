from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import norm
from statsmodels.tsa.arima.model import ARIMA, ARIMAResults
from statsmodels.tsa.statespace.sarimax import SARIMAX, SARIMAXResults

from app.core.ohlcv import ensure_supported_timeframe, query_close_series, timeframe_seconds

UTC = timezone.utc
MODEL_REGISTRY_DIR = Path(os.getenv("MODEL_REGISTRY_DIR", "/app/model_registry"))
VERSIONS_DIR = MODEL_REGISTRY_DIR / "versions"
ACTIVE_POINTER_PATH = MODEL_REGISTRY_DIR / "active_model.json"
ACTIVE_MODEL_PATH = Path(os.getenv("ARIMA_MODEL_PATH", "/app/model_registry/eth_arima.pkl"))
ACTIVE_META_PATH = Path(os.getenv("ARIMA_META_PATH", "/app/model_registry/eth_arima_meta.json"))

DEFAULT_ORDER = tuple(int(x) for x in os.getenv("ARIMA_DEFAULT_ORDER", "2,1,2").split(","))
DEFAULT_LOOKBACK_DAYS = int(os.getenv("DEFAULT_LOOKBACK_DAYS", "7"))
DEFAULT_HORIZON_HOURS = int(os.getenv("DEFAULT_HORIZON_HOURS", "6"))
DEFAULT_AUTO_ORDER = os.getenv("AUTO_ORDER_DEFAULT", "true").lower() in {"1", "true", "yes", "on"}
DEFAULT_ORDER_METRIC = os.getenv("ORDER_METRIC_DEFAULT", "aic").lower()
DEFAULT_CONF_ALPHA = float(os.getenv("FORECAST_CONF_ALPHA", "0.05"))
DEFAULT_BACKTEST_HOURS = int(os.getenv("BACKTEST_HOURS_DEFAULT", "24"))
DEFAULT_MODEL_FAMILY = os.getenv("MODEL_FAMILY_DEFAULT", "auto")
DEFAULT_TRANSFORM_MODE = os.getenv("TRANSFORM_MODE_DEFAULT", "auto")
DEFAULT_SEASONAL_PERIODS = [int(x.strip()) for x in os.getenv("SEASONAL_PERIODS_DEFAULT", "24,168").split(",") if x.strip()]
DEFAULT_SEASONAL_ORDER = tuple(int(x.strip()) for x in os.getenv("SEASONAL_ORDER_DEFAULT", "1,0,1").split(","))

_cached_results: Any | None = None
_cached_model_mtime: float | None = None
_cached_meta: dict[str, Any] | None = None
_cached_meta_mtime: float | None = None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _serialize_ts(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat() if ts is not None else None


def parse_order(order: str | None) -> tuple[int, int, int]:
    if not order:
        return DEFAULT_ORDER
    parts = [p.strip() for p in order.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("order must be 'p,d,q' (e.g. 2,1,2)")
    return tuple(int(x) for x in parts)


def parse_seasonal_order(order: str | None) -> tuple[int, int, int]:
    if not order:
        return DEFAULT_SEASONAL_ORDER
    parts = [p.strip() for p in order.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("seasonal_order must be 'P,D,Q' (e.g. 1,0,1)")
    return tuple(int(x) for x in parts)


def parse_periods(periods: str | None) -> list[int]:
    if not periods:
        return list(DEFAULT_SEASONAL_PERIODS)
    out = [int(x.strip()) for x in periods.split(",") if x.strip()]
    return [x for x in out if x > 1]


def _query_close_series(
    exchange: str,
    symbol: str,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    ensure_supported_timeframe(timeframe)
    return query_close_series(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        limit=limit,
    )


def get_latest_close(exchange: str, symbol: str, timeframe: str) -> tuple[datetime, float]:
    rows = _query_close_series(exchange, symbol, timeframe, start=None, end=None, limit=1)
    if not rows:
        raise ValueError("가격 데이터가 없습니다.")
    return rows[-1]["ts"], float(rows[-1]["close"])


def _candidate_transforms(mode: str, series: np.ndarray) -> list[str]:
    mode = (mode or "auto").lower()
    if mode != "auto":
        if mode not in {"none", "log1p", "diff", "log1p_diff"}:
            raise ValueError("transform_mode must be one of: auto, none, log1p, diff, log1p_diff")
        return [mode]

    candidates = ["none"]
    if np.min(series) > -1.0:
        candidates.append("log1p")
    if len(series) >= 64:
        candidates.append("diff")
    if np.min(series) > -1.0 and len(series) >= 64:
        candidates.append("log1p_diff")
    return candidates


def _apply_transform(series: np.ndarray, mode: str) -> tuple[np.ndarray, dict[str, Any]]:
    mode = mode.lower()
    if mode == "none":
        return series.astype(np.float64), {"mode": "none"}
    if mode == "log1p":
        if np.min(series) <= -1.0:
            raise ValueError("log1p transform requires close > -1")
        return np.log1p(series.astype(np.float64)), {"mode": "log1p"}
    if mode == "diff":
        if len(series) < 3:
            raise ValueError("diff transform requires at least 3 points")
        return np.diff(series.astype(np.float64)), {"mode": "diff", "last_value": float(series[-1])}
    if mode == "log1p_diff":
        if len(series) < 3:
            raise ValueError("log1p_diff transform requires at least 3 points")
        if np.min(series) <= -1.0:
            raise ValueError("log1p_diff requires close > -1")
        logged = np.log1p(series.astype(np.float64))
        return np.diff(logged), {"mode": "log1p_diff", "last_log": float(logged[-1])}
    raise ValueError(f"Unsupported transform mode: {mode}")


def _inverse_transform_forecast(
    mean: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    mode: str,
    latest_series: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mode = mode.lower()

    if mode == "none":
        return mean, lower, upper
    if mode == "log1p":
        return np.expm1(mean), np.expm1(lower), np.expm1(upper)
    if mode == "diff":
        base = float(latest_series[-1])
        return base + np.cumsum(mean), base + np.cumsum(lower), base + np.cumsum(upper)
    if mode == "log1p_diff":
        base_log = float(np.log1p(latest_series[-1]))
        mean_log = base_log + np.cumsum(mean)
        lower_log = base_log + np.cumsum(lower)
        upper_log = base_log + np.cumsum(upper)
        return np.expm1(mean_log), np.expm1(lower_log), np.expm1(upper_log)
    raise ValueError(f"Unsupported transform mode: {mode}")


def _fit_model(
    model_family: str,
    transformed: np.ndarray,
    order: tuple[int, int, int],
    seasonal_order: tuple[int, int, int, int] | None,
):
    family = model_family.lower()
    if family == "arima":
        return ARIMA(transformed, order=order).fit()
    if family == "sarima":
        if seasonal_order is None:
            raise ValueError("SARIMA requires seasonal_order")
        return SARIMAX(
            transformed,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
    raise ValueError(f"Unsupported model_family: {model_family}")


def _select_best_model(
    close_series: np.ndarray,
    requested_order: tuple[int, int, int] | None,
    auto_order: bool,
    order_metric: str,
    max_p: int,
    max_d: int,
    max_q: int,
    model_family: str,
    seasonal_periods: list[int],
    seasonal_order_pdq: tuple[int, int, int],
    transform_mode: str,
) -> dict[str, Any]:
    metric_key = order_metric.lower()
    if metric_key not in {"aic", "bic"}:
        raise ValueError("order_metric must be one of: aic, bic")

    transforms = _candidate_transforms(transform_mode, close_series)
    if auto_order:
        order_candidates = [(p, d, q) for p in range(max_p + 1) for d in range(max_d + 1) for q in range(max_q + 1) if (p + d + q) > 0]
    else:
        order_candidates = [requested_order or DEFAULT_ORDER]

    family = model_family.lower()
    family_candidates: list[tuple[str, tuple[int, int, int, int] | None]] = []
    if family == "arima":
        family_candidates.append(("arima", None))
    elif family == "sarima":
        if not seasonal_periods:
            raise ValueError("SARIMA selected but no seasonal periods provided")
        family_candidates.extend([("sarima", (seasonal_order_pdq[0], seasonal_order_pdq[1], seasonal_order_pdq[2], s)) for s in seasonal_periods])
    elif family == "auto":
        family_candidates.append(("arima", None))
        family_candidates.extend([("sarima", (seasonal_order_pdq[0], seasonal_order_pdq[1], seasonal_order_pdq[2], s)) for s in seasonal_periods])
    else:
        raise ValueError("model_family must be one of: arima, sarima, auto")

    best: dict[str, Any] | None = None

    for transform in transforms:
        try:
            transformed, transform_state = _apply_transform(close_series, transform)
        except Exception:
            continue

        if len(transformed) < 20:
            continue

        for cand_family, cand_seasonal in family_candidates:
            for order in order_candidates:
                try:
                    fitted = _fit_model(cand_family, transformed, order, cand_seasonal)
                    score = float(getattr(fitted, metric_key))
                    if np.isnan(score) or np.isinf(score):
                        continue
                except Exception:
                    continue

                candidate = {
                    "fitted": fitted,
                    "score": score,
                    "metric": metric_key,
                    "model_family": cand_family,
                    "order": order,
                    "seasonal_order": cand_seasonal,
                    "transform_mode": transform,
                    "transform_state": transform_state,
                }
                if best is None or candidate["score"] < best["score"]:
                    best = candidate

    if best is None:
        raise ValueError("모델 학습 실패: order/transform/seasonal 후보에서 유효한 조합을 찾지 못했습니다.")
    return best


def _forecast_with_model(
    fitted: Any,
    latest_close_series: np.ndarray,
    transform_mode: str,
    horizon_steps: int,
    alpha: float,
) -> dict[str, np.ndarray]:
    transformed_latest, _ = _apply_transform(latest_close_series, transform_mode)
    if len(transformed_latest) < 5:
        raise ValueError("예측 실패: 변환 후 시계열 길이가 부족합니다.")

    try:
        updated = fitted.apply(transformed_latest, refit=False)
        pred_obj = updated.get_forecast(steps=horizon_steps)
    except Exception:
        pred_obj = fitted.get_forecast(steps=horizon_steps)

    mean = np.asarray(pred_obj.predicted_mean, dtype=np.float64)
    conf = np.asarray(pred_obj.conf_int(alpha=alpha), dtype=np.float64)
    if conf.ndim == 1:
        conf = conf.reshape(-1, 2)

    lower = conf[:, 0]
    upper = conf[:, 1]
    mean_inv, lower_inv, upper_inv = _inverse_transform_forecast(mean, lower, upper, transform_mode, latest_close_series)

    return {"mean": mean_inv, "lower": lower_inv, "upper": upper_inv}


def _compute_backtest(
    close_series: np.ndarray,
    model_family: str,
    order: tuple[int, int, int],
    seasonal_order: tuple[int, int, int, int] | None,
    transform_mode: str,
    backtest_hours: int,
    timeframe: str,
    alpha: float,
) -> dict[str, Any]:
    step_seconds = timeframe_seconds(timeframe)
    horizon_steps = max(1, int(math.ceil((backtest_hours * 3600) / step_seconds)))
    horizon = int(max(1, min(horizon_steps, max(1, len(close_series) // 3))))
    split_idx = len(close_series) - horizon
    if split_idx < 50:
        return {"hours": 0, "mae": None, "mape": None}

    train = close_series[:split_idx]
    actual = close_series[split_idx:]

    transformed_train, _ = _apply_transform(train, transform_mode)
    fitted = _fit_model(model_family, transformed_train, order, seasonal_order)

    preds = []
    for step in range(horizon):
        observed = close_series[: split_idx + step]
        forecast = _forecast_with_model(fitted, observed, transform_mode, 1, alpha)
        preds.append(float(forecast["mean"][0]))

    pred_arr = np.asarray(preds, dtype=np.float64)
    act_arr = np.asarray(actual[: len(pred_arr)], dtype=np.float64)

    mae = float(np.mean(np.abs(act_arr - pred_arr)))
    denom = np.where(act_arr == 0, np.nan, act_arr)
    mape = float(np.nanmean(np.abs((act_arr - pred_arr) / denom)) * 100.0)
    if np.isnan(mape):
        mape = None

    return {"hours": int(len(pred_arr)), "mae": mae, "mape": mape}


def _make_model_id(payload: dict[str, Any]) -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:8]
    return f"{stamp}_{digest}"


def _save_versioned_artifacts(fitted: Any, meta: dict[str, Any], set_active: bool) -> dict[str, Any]:
    MODEL_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    fingerprint = {
        "exchange": meta["exchange"],
        "symbol": meta["symbol"],
        "timeframe": meta["timeframe"],
        "train_start": meta["train_start"],
        "train_end": meta["train_end"],
        "order": meta["order"],
        "seasonal_order": meta.get("seasonal_order"),
        "transform_mode": meta["transform_mode"],
        "model_family": meta["model_family"],
    }
    model_id = _make_model_id(fingerprint)

    version_model = VERSIONS_DIR / f"eth_model_{model_id}.pkl"
    version_meta = VERSIONS_DIR / f"eth_model_{model_id}.json"

    fitted.save(str(version_model))
    version_meta.write_text(
        json.dumps(
            {**meta, "model_id": model_id, "model_file": version_model.name, "meta_file": version_meta.name},
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    if set_active:
        ACTIVE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        fitted.save(str(ACTIVE_MODEL_PATH))
        ACTIVE_META_PATH.write_text(version_meta.read_text(encoding="utf-8"), encoding="utf-8")
        ACTIVE_POINTER_PATH.write_text(
            json.dumps(
                {
                    "model_id": model_id,
                    "model_file": version_model.name,
                    "meta_file": version_meta.name,
                    "updated_at": datetime.now(tz=UTC).isoformat(),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    return {"model_id": model_id, "model_path": str(version_model), "meta_path": str(version_meta)}


def _load_active_meta_path() -> Path | None:
    if ACTIVE_POINTER_PATH.exists():
        payload = json.loads(ACTIVE_POINTER_PATH.read_text(encoding="utf-8"))
        meta_file = payload.get("meta_file")
        if meta_file:
            candidate = VERSIONS_DIR / str(meta_file)
            if candidate.exists():
                return candidate
    if ACTIVE_META_PATH.exists():
        return ACTIVE_META_PATH
    return None


def _load_active_model_path() -> Path | None:
    if ACTIVE_POINTER_PATH.exists():
        payload = json.loads(ACTIVE_POINTER_PATH.read_text(encoding="utf-8"))
        model_file = payload.get("model_file")
        if model_file:
            candidate = VERSIONS_DIR / str(model_file)
            if candidate.exists():
                return candidate
    if ACTIVE_MODEL_PATH.exists():
        return ACTIVE_MODEL_PATH
    return None


def _version_meta_path(model_id: str) -> Path:
    return VERSIONS_DIR / f"eth_model_{model_id}.json"


def _version_model_path(model_id: str) -> Path:
    return VERSIONS_DIR / f"eth_model_{model_id}.pkl"


def load_metadata_for_model(model_id: str | None = None) -> dict[str, Any] | None:
    if model_id is None:
        return load_metadata()
    meta_path = _version_meta_path(model_id)
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_results_for_model(model_id: str | None = None, meta: dict[str, Any] | None = None):
    if model_id is None:
        return load_results()

    model_path = _version_model_path(model_id)
    if not model_path.exists():
        return None
    metadata = meta or load_metadata_for_model(model_id)

    try:
        if isinstance(metadata, dict) and metadata.get("model_family") == "sarima":
            return SARIMAXResults.load(str(model_path))
        return ARIMAResults.load(str(model_path))
    except Exception:
        try:
            return ARIMAResults.load(str(model_path))
        except Exception:
            return SARIMAXResults.load(str(model_path))


def load_metadata(force_reload: bool = False) -> dict[str, Any] | None:
    global _cached_meta, _cached_meta_mtime
    meta_path = _load_active_meta_path()
    if meta_path is None:
        return None

    mtime = meta_path.stat().st_mtime
    if _cached_meta is not None and _cached_meta_mtime == mtime and not force_reload:
        return _cached_meta

    _cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    _cached_meta_mtime = mtime
    return _cached_meta


def load_results(force_reload: bool = False):
    global _cached_results, _cached_model_mtime
    model_path = _load_active_model_path()
    if model_path is None:
        return None

    mtime = model_path.stat().st_mtime
    if _cached_results is not None and _cached_model_mtime == mtime and not force_reload:
        return _cached_results

    meta = load_metadata()
    try:
        if isinstance(meta, dict) and meta.get("model_family") == "sarima":
            _cached_results = SARIMAXResults.load(str(model_path))
        else:
            _cached_results = ARIMAResults.load(str(model_path))
    except Exception:
        try:
            _cached_results = ARIMAResults.load(str(model_path))
        except Exception:
            _cached_results = SARIMAXResults.load(str(model_path))

    _cached_model_mtime = mtime
    return _cached_results


def list_versions(limit: int = 20) -> list[dict[str, Any]]:
    if not VERSIONS_DIR.exists():
        return []
    metas = sorted(VERSIONS_DIR.glob("eth_model_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for meta_file in metas[:limit]:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            out.append(
                {
                    "model_id": meta.get("model_id"),
                    "trained_at": meta.get("trained_at"),
                    "model_family": meta.get("model_family"),
                    "order": meta.get("order"),
                    "seasonal_order": meta.get("seasonal_order"),
                    "transform_mode": meta.get("transform_mode"),
                    "score": meta.get("selection_score"),
                }
            )
        except Exception:
            continue
    return out


def list_models(limit: int = 50) -> dict[str, Any]:
    active_id = None
    if ACTIVE_POINTER_PATH.exists():
        try:
            active_id = json.loads(ACTIVE_POINTER_PATH.read_text(encoding="utf-8")).get("model_id")
        except Exception:
            active_id = None

    if not VERSIONS_DIR.exists():
        return {"active_model_id": active_id, "models": []}

    metas = sorted(VERSIONS_DIR.glob("eth_model_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    models: list[dict[str, Any]] = []
    for meta_file in metas[:limit]:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        model_id = meta.get("model_id")
        order = meta.get("order")
        family = str(meta.get("model_family") or "arima").upper()
        backtest = meta.get("backtest") or {}
        models.append(
            {
                "id": model_id,
                "name": f"{family} {order}" if order else family,
                "model_family": meta.get("model_family"),
                "timeframe": meta.get("timeframe"),
                "horizon_hours": meta.get("horizon_hours"),
                "last_trained": meta.get("trained_at"),
                "status": "active" if model_id == active_id else "available",
                "metrics": {
                    "selection_score": meta.get("selection_score"),
                    "selection_metric": meta.get("selection_metric"),
                    "mae": backtest.get("mae"),
                    "mape": backtest.get("mape"),
                },
            }
        )

    return {"active_model_id": active_id, "models": models}


def model_status() -> dict[str, Any]:
    meta = load_metadata()
    active_ptr = json.loads(ACTIVE_POINTER_PATH.read_text(encoding="utf-8")) if ACTIVE_POINTER_PATH.exists() else None
    return {
        "available": _load_active_model_path() is not None and meta is not None,
        "active_model": active_ptr,
        "metadata": meta,
        "versions": list_versions(limit=20),
    }


def train_arima(
    lookback_days: int | None = None,
    order: tuple[int, int, int] | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    horizon_hours: int | None = None,
    auto_order: bool | None = None,
    order_metric: str | None = None,
    max_p: int = 3,
    max_d: int = 2,
    max_q: int = 3,
    model_family: str | None = None,
    seasonal_periods: list[int] | None = None,
    seasonal_order_pdq: tuple[int, int, int] | None = None,
    transform_mode: str | None = None,
    backtest_hours: int | None = None,
    conf_alpha: float | None = None,
    set_active: bool = True,
    train_reason: str = "manual",
) -> dict[str, Any]:
    exchange = exchange or os.getenv("EXCHANGE", "binance")
    symbol = symbol or os.getenv("SYMBOL", "ETH/USDT")
    timeframe = timeframe or os.getenv("TIMEFRAME", "15m")
    ensure_supported_timeframe(timeframe)
    lookback_days = DEFAULT_LOOKBACK_DAYS if lookback_days is None else int(lookback_days)
    horizon_hours = DEFAULT_HORIZON_HOURS if horizon_hours is None else int(horizon_hours)
    auto_order = DEFAULT_AUTO_ORDER if auto_order is None else bool(auto_order)
    order_metric = (order_metric or DEFAULT_ORDER_METRIC).lower()
    model_family = (model_family or DEFAULT_MODEL_FAMILY).lower()
    transform_mode = (transform_mode or DEFAULT_TRANSFORM_MODE).lower()
    conf_alpha = float(DEFAULT_CONF_ALPHA if conf_alpha is None else conf_alpha)
    backtest_hours = int(DEFAULT_BACKTEST_HOURS if backtest_hours is None else backtest_hours)
    seasonal_periods = list(DEFAULT_SEASONAL_PERIODS if seasonal_periods is None else seasonal_periods)
    seasonal_order_pdq = DEFAULT_SEASONAL_ORDER if seasonal_order_pdq is None else seasonal_order_pdq

    if start is None:
        end = end or datetime.now(tz=UTC)
        start = end - timedelta(days=lookback_days)

    rows = _query_close_series(exchange, symbol, timeframe, start, end, limit=None)
    if len(rows) < 80:
        raise ValueError(f"데이터 부족: 학습 구간 데이터가 {len(rows)}개입니다. lookback_days를 줄이거나 데이터를 더 채우세요.")

    closes = np.asarray([float(row["close"]) for row in rows], dtype=np.float64)
    data_min_ts = rows[0]["ts"]
    data_max_ts = rows[-1]["ts"]

    selected = _select_best_model(
        close_series=closes,
        requested_order=order,
        auto_order=auto_order,
        order_metric=order_metric,
        max_p=max_p,
        max_d=max_d,
        max_q=max_q,
        model_family=model_family,
        seasonal_periods=seasonal_periods,
        seasonal_order_pdq=seasonal_order_pdq,
        transform_mode=transform_mode,
    )

    backtest = _compute_backtest(
        close_series=closes,
        model_family=selected["model_family"],
        order=selected["order"],
        seasonal_order=selected["seasonal_order"],
        transform_mode=selected["transform_mode"],
        backtest_hours=backtest_hours,
        timeframe=timeframe,
        alpha=conf_alpha,
    )

    meta = {
        "model": "ARIMA/SARIMA",
        "model_family": selected["model_family"],
        "order": [int(selected["order"][0]), int(selected["order"][1]), int(selected["order"][2])],
        "seasonal_order": [int(selected["seasonal_order"][0]), int(selected["seasonal_order"][1]), int(selected["seasonal_order"][2]), int(selected["seasonal_order"][3])] if selected["seasonal_order"] else None,
        "transform_mode": selected["transform_mode"],
        "selection_metric": selected["metric"],
        "selection_score": float(selected["score"]),
        "auto_order": bool(auto_order),
        "order_search": {"max_p": int(max_p), "max_d": int(max_d), "max_q": int(max_q)},
        "lookback_days": int(lookback_days),
        "horizon_hours": int(horizon_hours),
        "conf_alpha": conf_alpha,
        "trained_at": datetime.now(tz=UTC).isoformat(),
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "train_start": _serialize_ts(data_min_ts),
        "train_end": _serialize_ts(data_max_ts),
        "data_points": int(len(rows)),
        "backtest": backtest,
        "train_reason": train_reason,
    }

    version_info = _save_versioned_artifacts(selected["fitted"], meta, set_active=set_active)
    meta.update(version_info)

    load_metadata(force_reload=True)
    load_results(force_reload=True)
    return meta


def _z_from_conf_level(level: float) -> float:
    alpha = 1.0 - level
    return float(norm.ppf(1.0 - alpha / 2.0))


def _distribution_from_mean_band(mean: float, lower: float, upper: float, conf_level: float = 0.95) -> tuple[float, float]:
    z = _z_from_conf_level(conf_level)
    sigma = max((upper - lower) / (2.0 * z), 1e-9)
    return mean, sigma


def _event_probabilities(
    current_close: float,
    mean_price: float,
    sigma_price: float,
    up_pct: float,
    down_pct: float,
    method: str = "normal",
    simulation_samples: int = 5000,
) -> tuple[float, float]:
    up_target = current_close * (1.0 + up_pct / 100.0)
    down_target = current_close * (1.0 - down_pct / 100.0)

    # Default method: normal approximation using forecast mean and std from confidence band.
    if method == "normal":
        prob_up = float(1.0 - norm.cdf(up_target, loc=mean_price, scale=sigma_price))
        prob_down = float(norm.cdf(down_target, loc=mean_price, scale=sigma_price))
        return max(0.0, min(1.0, prob_up)), max(0.0, min(1.0, prob_down))

    if method == "simulation":
        samples = np.random.normal(loc=mean_price, scale=sigma_price, size=max(1000, simulation_samples))
        prob_up = float(np.mean(samples >= up_target))
        prob_down = float(np.mean(samples <= down_target))
        return max(0.0, min(1.0, prob_up)), max(0.0, min(1.0, prob_down))

    raise ValueError("probability_method must be one of: normal, simulation")


def forecast_with_latest_model(
    horizon_hours: int | None = None,
    include_history_tail: int = 0,
    timeframe: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    meta = load_metadata_for_model(model_id=model_id)
    if meta is None:
        if model_id:
            raise FileNotFoundError(f"model_id '{model_id}' not found")
        raise FileNotFoundError("모델 없음: Train 버튼을 눌러 먼저 학습하세요.")

    fitted = load_results_for_model(model_id=model_id, meta=meta)
    if fitted is None:
        if model_id:
            raise FileNotFoundError(f"model_id '{model_id}' artifact not found")
        raise FileNotFoundError("모델 파일 없음: /train으로 모델을 다시 생성하세요.")

    horizon = int(horizon_hours or meta.get("horizon_hours") or DEFAULT_HORIZON_HOURS)
    alpha = float(meta.get("conf_alpha") or DEFAULT_CONF_ALPHA)

    exchange = meta.get("exchange") or os.getenv("EXCHANGE", "binance")
    symbol = meta.get("symbol") or os.getenv("SYMBOL", "ETH/USDT")
    selected_timeframe = timeframe or meta.get("timeframe") or os.getenv("TIMEFRAME", "15m")
    ensure_supported_timeframe(selected_timeframe)
    step_seconds = timeframe_seconds(selected_timeframe)
    horizon_steps = max(1, int(math.ceil((horizon * 3600) / step_seconds)))

    latest_rows = _query_close_series(exchange, symbol, selected_timeframe, start=None, end=None, limit=1200)
    if len(latest_rows) < 40:
        raise ValueError("데이터 부족: 예측을 위한 최근 데이터가 충분하지 않습니다.")

    latest_closes = np.asarray([float(row["close"]) for row in latest_rows], dtype=np.float64)
    transform_mode = str(meta.get("transform_mode") or "none")

    forecast = _forecast_with_model(
        fitted=fitted,
        latest_close_series=latest_closes,
        transform_mode=transform_mode,
        horizon_steps=horizon_steps,
        alpha=alpha,
    )

    last_ts = latest_rows[-1]["ts"]
    points = []
    for idx, mean_val in enumerate(forecast["mean"].tolist(), start=1):
        points.append(
            {
                "ts": (last_ts + timedelta(seconds=step_seconds * idx)).astimezone(UTC),
                "pred_close": float(mean_val),
                "pred_low": float(forecast["lower"][idx - 1]),
                "pred_high": float(forecast["upper"][idx - 1]),
            }
        )

    tail_n = int(max(0, include_history_tail))
    history_tail = [{"ts": row["ts"].astimezone(UTC), "close": float(row["close"])} for row in latest_rows[-tail_n:]]

    return {
        "model_id": meta.get("model_id"),
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": selected_timeframe,
        "horizon_hours": horizon,
        "horizon_steps": horizon_steps,
        "conf_alpha": alpha,
        "points": points,
        "history_tail": history_tail,
        "model_metadata": meta,
    }


def compute_signal(
    horizon_hours: int,
    up_pct: float,
    down_pct: float,
    method: str = "normal",
    up_prob_threshold: float = 0.60,
    down_prob_threshold: float = 0.60,
    simulation_samples: int = 5000,
    timeframe: str | None = None,
) -> dict[str, Any]:
    meta = load_metadata()
    if meta is None:
        raise FileNotFoundError("모델 없음: Train 버튼을 눌러 먼저 학습하세요.")

    forecast = forecast_with_latest_model(horizon_hours=horizon_hours, include_history_tail=1, timeframe=timeframe)
    if not forecast["points"]:
        raise ValueError("예측 결과가 없습니다.")

    latest = forecast["history_tail"][-1]
    target_point = forecast["points"][-1]
    current_close = float(latest["close"])
    mean_price = float(target_point["pred_close"])
    low_price = float(target_point["pred_low"])
    high_price = float(target_point["pred_high"])

    mean_dist, sigma_dist = _distribution_from_mean_band(mean_price, low_price, high_price, conf_level=0.95)
    prob_up, prob_down = _event_probabilities(
        current_close=current_close,
        mean_price=mean_dist,
        sigma_price=sigma_dist,
        up_pct=up_pct,
        down_pct=down_pct,
        method=method,
        simulation_samples=simulation_samples,
    )

    signal = "no-trade"
    if prob_up >= up_prob_threshold and prob_up > prob_down:
        signal = "long"
    elif prob_down >= down_prob_threshold and prob_down > prob_up:
        signal = "short"

    recommended_tp = current_close * (1.0 + up_pct / 100.0)
    recommended_sl = current_close * (1.0 - down_pct / 100.0)

    backtest = meta.get("backtest") or {}
    mape = backtest.get("mape")
    if mape is None:
        confidence_level = "unknown"
    elif mape < 1.0:
        confidence_level = "high"
    elif mape < 2.5:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    return {
        "timestamp": target_point["ts"],
        "current_close": current_close,
        "horizon_hours": int(horizon_hours),
        "timeframe": forecast["timeframe"],
        "prob_up": prob_up,
        "prob_down": prob_down,
        "signal": signal,
        "recommended_tp": recommended_tp,
        "recommended_sl": recommended_sl,
        "confidence_level": confidence_level,
        "probability_method": method,
    }


def backtest_strategy(
    lookback_days: int,
    horizon_hours: int,
    up_prob_threshold: float,
    down_prob_threshold: float,
    up_pct: float,
    down_pct: float,
    fee_bps: float,
    slippage_bps: float,
    probability_method: str = "normal",
    timeframe: str | None = None,
) -> dict[str, Any]:
    meta = load_metadata()
    if meta is None:
        raise FileNotFoundError("모델 없음: Train 버튼을 눌러 먼저 학습하세요.")

    exchange = meta.get("exchange") or os.getenv("EXCHANGE", "binance")
    symbol = meta.get("symbol") or os.getenv("SYMBOL", "ETH/USDT")
    selected_timeframe = timeframe or meta.get("timeframe") or os.getenv("TIMEFRAME", "15m")
    ensure_supported_timeframe(selected_timeframe)
    step_seconds = timeframe_seconds(selected_timeframe)
    horizon_steps = max(1, int(math.ceil((horizon_hours * 3600) / step_seconds)))

    end = datetime.now(tz=UTC)
    start = end - timedelta(days=max(2, int(lookback_days)))
    rows = _query_close_series(exchange, symbol, selected_timeframe, start=start, end=end, limit=None)
    if len(rows) < max(120, horizon_steps + 40):
        raise ValueError("백테스트 데이터가 부족합니다. lookback_days를 늘리세요.")

    closes = np.asarray([float(row["close"]) for row in rows], dtype=np.float64)
    timestamps = [row["ts"] for row in rows]

    model_family = str(meta.get("model_family") or "arima")
    order = tuple(int(x) for x in (meta.get("order") or DEFAULT_ORDER))
    seasonal = meta.get("seasonal_order")
    seasonal_order = tuple(int(x) for x in seasonal) if seasonal else None
    transform_mode = str(meta.get("transform_mode") or "none")

    costs = 2.0 * (float(fee_bps) + float(slippage_bps)) / 10000.0
    min_train_points = max(80, horizon_steps * 8)

    trades: list[dict[str, Any]] = []
    cumulative = 0.0
    pnl_series: list[dict[str, Any]] = []
    wins = 0

    for idx in range(min_train_points, len(closes) - horizon_steps):
        train = closes[: idx + 1]
        try:
            transformed, _ = _apply_transform(train, transform_mode)
            fitted = _fit_model(model_family, transformed, order, seasonal_order)
            forecast = _forecast_with_model(
                fitted,
                train,
                transform_mode,
                horizon_steps,
                float(meta.get("conf_alpha") or DEFAULT_CONF_ALPHA),
            )
        except Exception:
            continue

        mean_price = float(forecast["mean"][-1])
        low_price = float(forecast["lower"][-1])
        high_price = float(forecast["upper"][-1])
        current_close = float(train[-1])

        mean_dist, sigma_dist = _distribution_from_mean_band(mean_price, low_price, high_price, conf_level=0.95)
        prob_up, prob_down = _event_probabilities(
            current_close=current_close,
            mean_price=mean_dist,
            sigma_price=sigma_dist,
            up_pct=up_pct,
            down_pct=down_pct,
            method=probability_method,
            simulation_samples=2000,
        )

        signal = "no-trade"
        if prob_up >= up_prob_threshold and prob_up > prob_down:
            signal = "long"
        elif prob_down >= down_prob_threshold and prob_down > prob_up:
            signal = "short"

        if signal == "no-trade":
            continue

        entry = current_close
        exit_price = float(closes[idx + horizon_steps])
        raw_ret = exit_price / entry - 1.0
        pnl = raw_ret - costs if signal == "long" else (-raw_ret) - costs
        cumulative += pnl
        if pnl > 0:
            wins += 1

        trade = {
            "entry_ts": timestamps[idx],
            "exit_ts": timestamps[idx + horizon_steps],
            "entry": entry,
            "exit": exit_price,
            "signal": signal,
            "prob_up": prob_up,
            "prob_down": prob_down,
            "pnl": pnl,
            "cum_pnl": cumulative,
        }
        trades.append(trade)
        pnl_series.append({"ts": timestamps[idx + horizon_steps], "cum_pnl": cumulative})

    max_drawdown = 0.0
    peak = -math.inf
    for point in pnl_series:
        value = float(point["cum_pnl"])
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value - peak)

    n = len(trades)
    summary = {
        "n_trades": n,
        "win_rate": (wins / n) if n else 0.0,
        "avg_pnl": (sum(float(t["pnl"]) for t in trades) / n) if n else 0.0,
        "cumulative_pnl": cumulative,
        "mdd": float(max_drawdown),
    }

    return {
        "timeframe": selected_timeframe,
        "horizon_steps": horizon_steps,
        "trades": trades,
        "pnl_series": pnl_series,
        "summary": summary,
    }


def metadata_data_max_ts(meta: dict[str, Any] | None) -> datetime | None:
    if not meta:
        return None
    return _parse_iso(meta.get("train_end"))
