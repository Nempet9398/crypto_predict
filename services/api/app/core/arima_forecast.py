from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ARIMA_MODEL_PATH = Path(os.getenv("ARIMA_MODEL_PATH", "/app/model_registry/eth_arima_h6.pkl"))


@dataclass(frozen=True)
class ArimaPayload:
    order: tuple[int, int, int]
    horizon: int
    results: Any


_CACHED: ArimaPayload | None = None


def load_payload(force_reload: bool = False) -> ArimaPayload | None:
    global _CACHED
    if _CACHED is not None and not force_reload:
        return _CACHED
    if not ARIMA_MODEL_PATH.exists():
        return None
    try:
        from statsmodels.tsa.arima.model import ARIMAResults
    except Exception:
        return None
    results = ARIMAResults.load(str(ARIMA_MODEL_PATH))
    order = tuple(int(x) for x in results.model.order)
    # horizon is not stored in the results; default to 6 (can be overridden by query)
    _CACHED = ArimaPayload(order=order, horizon=6, results=results)
    return _CACHED


def forecast_next(closes: np.ndarray, horizon: int | None = None) -> np.ndarray | None:
    payload = load_payload()
    if payload is None:
        return None
    if closes.shape[0] < 10:
        return None
    horizon = int(horizon or payload.horizon)

    results = payload.results
    # Try to update with latest data if supported.
    try:
        updated = results.apply(closes, refit=False)
        pred = updated.forecast(steps=horizon)
    except Exception:
        try:
            pred = results.forecast(steps=horizon)
        except Exception:
            return None

    return np.asarray(pred, dtype=np.float32)
