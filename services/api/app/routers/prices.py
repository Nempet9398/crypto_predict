from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from app.core.ohlcv import ensure_supported_timeframe, query_ohlcv

router = APIRouter(prefix="/prices", tags=["prices"])
UTC = timezone.utc


def _default_exchange() -> str:
    return os.getenv("EXCHANGE", "binance")


def _default_symbol() -> str:
    return os.getenv("SYMBOL", "ETH/USDT")


def _default_timeframe() -> str:
    return os.getenv("TIMEFRAME", "15m")


def _parse_iso(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_tz_iso(ts: datetime, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz_name}") from exc
    return ts.astimezone(tz).isoformat()


@router.get("/history")
def price_history(
    start: str | None = Query(None, description="ISO datetime"),
    end: str | None = Query(None, description="ISO datetime"),
    before: str | None = Query(None, description="Load candles older than this ISO datetime"),
    limit: int = Query(200, ge=1, le=100000),
    tz: str = Query("Asia/Seoul"),
    exchange: str | None = Query(None),
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
):
    exchange = exchange or _default_exchange()
    symbol = symbol or _default_symbol()
    timeframe = timeframe or _default_timeframe()
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    before_dt = _parse_iso(before)
    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start must be <= end")
    if before_dt is not None and start_dt is not None and before_dt <= start_dt:
        raise HTTPException(status_code=400, detail="before must be > start when both are provided")

    try:
        ensure_supported_timeframe(timeframe)
        rows = query_ohlcv(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            start=start_dt,
            end=end_dt,
            before=before_dt,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    points = [
        {
            "ts": _to_tz_iso(r["ts"], tz),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        }
        for r in rows
    ]

    return {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "tz": tz,
        "start": start_dt.isoformat() if start_dt else None,
        "end": end_dt.isoformat() if end_dt else None,
        "before": before_dt.isoformat() if before_dt else None,
        "limit": limit,
        "points": points,
    }


@router.get("/latest")
def latest_prices(
    timeframe: str | None = Query(None),
    limit: int = Query(2, ge=1, le=300),
    tz: str = Query("Asia/Seoul"),
    exchange: str | None = Query(None),
    symbol: str | None = Query(None),
):
    exchange = exchange or _default_exchange()
    symbol = symbol or _default_symbol()
    timeframe = timeframe or _default_timeframe()

    try:
        ensure_supported_timeframe(timeframe)
        rows = query_ohlcv(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            start=None,
            end=None,
            before=None,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    points = [
        {
            "ts": _to_tz_iso(r["ts"], tz),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        }
        for r in rows
    ]

    return {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "tz": tz,
        "limit": limit,
        "points": points,
    }
