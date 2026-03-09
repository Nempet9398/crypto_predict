from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.db import fetch_all

SUPPORTED_TIMEFRAMES = ("15m", "30m", "1h", "6h", "24h")
TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "24h": 24 * 60 * 60,
}


def ensure_supported_timeframe(timeframe: str) -> str:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        allowed = ", ".join(SUPPORTED_TIMEFRAMES)
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Allowed: {allowed}")
    return timeframe


def timeframe_seconds(timeframe: str) -> int:
    ensure_supported_timeframe(timeframe)
    return TIMEFRAME_SECONDS[timeframe]


def _direct_query(
    exchange: str,
    symbol: str,
    start: datetime | None,
    end: datetime | None,
    before: datetime | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    where = ["exchange = %s", "symbol = %s", "timeframe = '15m'"]
    params: list[Any] = [exchange, symbol]
    if start is not None:
        where.append("ts >= %s")
        params.append(start)
    if end is not None:
        where.append("ts <= %s")
        params.append(end)
    if before is not None:
        where.append("ts < %s")
        params.append(before)

    if limit is None:
        return fetch_all(
            f"""
            SELECT ts, open, high, low, close, volume
            FROM raw.eth_ohlcv
            WHERE {' AND '.join(where)}
            ORDER BY ts
            """,
            tuple(params),
        )

    return fetch_all(
        f"""
        SELECT ts, open, high, low, close, volume
        FROM (
          SELECT ts, open, high, low, close, volume
          FROM raw.eth_ohlcv
          WHERE {' AND '.join(where)}
          ORDER BY ts DESC
          LIMIT %s
        ) t
        ORDER BY ts
        """,
        tuple([*params, limit]),
    )


def _resample_query(
    exchange: str,
    symbol: str,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    before: datetime | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    bucket = timeframe_seconds(timeframe)
    where = ["exchange = %s", "symbol = %s", "timeframe = '15m'"]
    params: list[Any] = [exchange, symbol]

    if start is not None:
        where.append("ts >= %s")
        params.append(start)
    if end is not None:
        where.append("ts <= %s")
        params.append(end)
    if before is not None:
        where.append("ts < %s")
        params.append(before)

    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT %s"

    query = f"""
        WITH base AS (
          SELECT
            ts,
            open,
            high,
            low,
            close,
            volume,
            (TIMESTAMPTZ 'epoch' + floor(extract(epoch FROM ts) / %s) * %s * interval '1 second') AS bucket_ts
          FROM raw.eth_ohlcv
          WHERE {' AND '.join(where)}
        ),
        grouped AS (
          SELECT
            bucket_ts AS ts,
            (array_agg(open ORDER BY ts ASC))[1] AS open,
            MAX(high) AS high,
            MIN(low) AS low,
            (array_agg(close ORDER BY ts DESC))[1] AS close,
            SUM(volume) AS volume
          FROM base
          GROUP BY bucket_ts
        )
        SELECT ts, open, high, low, close, volume
        FROM (
          SELECT ts, open, high, low, close, volume
          FROM grouped
          ORDER BY ts DESC
          {limit_sql}
        ) t
        ORDER BY ts
    """

    query_params: list[Any] = [bucket, bucket, *params]
    if limit is not None:
        query_params.append(limit)
    return fetch_all(query, tuple(query_params))


def query_ohlcv(
    exchange: str,
    symbol: str,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    before: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    ensure_supported_timeframe(timeframe)
    if timeframe == "15m":
        return _direct_query(exchange, symbol, start, end, before, limit)
    return _resample_query(exchange, symbol, timeframe, start, end, before, limit)


def query_close_series(
    exchange: str,
    symbol: str,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    before: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows = query_ohlcv(exchange=exchange, symbol=symbol, timeframe=timeframe, start=start, end=end, before=before, limit=limit)
    return [{"ts": row["ts"], "close": row["close"]} for row in rows]
