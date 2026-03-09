from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from app.core.data_sync import get_data_status

router = APIRouter(prefix="/data", tags=["data"])


def _default_exchange() -> str:
    return os.getenv("EXCHANGE", "binance")


def _default_symbol() -> str:
    return os.getenv("SYMBOL", "ETH/USDT")


def _default_timeframe() -> str:
    return os.getenv("TIMEFRAME", "15m")


def _to_tz_iso(ts, tz_name: str):
    if ts is None:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz_name}") from exc
    return ts.astimezone(tz).isoformat()


@router.get("/status")
def data_status(
    tz: str = Query("Asia/Seoul"),
    exchange: str | None = Query(None),
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
):
    exchange = exchange or _default_exchange()
    symbol = symbol or _default_symbol()
    timeframe = timeframe or _default_timeframe()
    st = get_data_status(exchange=exchange, symbol=symbol, timeframe=timeframe)
    return {
        **st,
        "tz": tz,
        "min_ts": _to_tz_iso(st["min_ts"], tz),
        "max_ts": _to_tz_iso(st["max_ts"], tz),
        "last_updated": _to_tz_iso(st["last_updated"], tz),
    }
