from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import ccxt

from app.core.db import db_conn_cursor, execute_values, fetch_one

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class SyncResult:
    inserted_rows: int
    ranges_filled: int
    latest_filled: bool


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def default_exchange() -> str:
    return _env("EXCHANGE", "binance")


def default_symbol() -> str:
    return _env("SYMBOL", "ETH/USDT")


def default_timeframe() -> str:
    return _env("TIMEFRAME", "15m")


def _timeframe_ms(timeframe: str) -> int:
    return int(ccxt.Exchange.parse_timeframe(timeframe) * 1000)


def _timeframe_seconds(timeframe: str) -> int:
    return int(ccxt.Exchange.parse_timeframe(timeframe))


def _to_timeframe_boundary_utc(dt: datetime, timeframe: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    epoch = int(dt.astimezone(UTC).timestamp())
    step = _timeframe_seconds(timeframe)
    floored = epoch - (epoch % step)
    return datetime.fromtimestamp(floored, tz=UTC)


def _now_boundary_utc(timeframe: str) -> datetime:
    return _to_timeframe_boundary_utc(datetime.now(tz=UTC), timeframe)


def _get_exchange_client(exchange_name: str):
    try:
        klass = getattr(ccxt, exchange_name)
    except AttributeError as exc:
        raise RuntimeError(f"Unsupported exchange: {exchange_name}") from exc
    return klass({"enableRateLimit": True})


def _bounds(exchange: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT
          MIN(ts) AS min_ts,
          MAX(ts) AS max_ts,
          COUNT(*) AS row_count,
          MAX(ingested_at) AS last_updated
        FROM raw.eth_ohlcv
        WHERE exchange = %s AND symbol = %s AND timeframe = %s
        """,
        (exchange, symbol, timeframe),
    )


def _interval_literal(timeframe: str) -> str:
    seconds = _timeframe_seconds(timeframe)
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute"
    return f"{seconds} second"


def _missing_timestamps(exchange: str, symbol: str, timeframe: str) -> list[datetime]:
    step = _interval_literal(timeframe)
    with db_conn_cursor(dict_cursor=False) as (_conn, cur):
        cur.execute(
            f"""
            WITH bounds AS (
              SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts
              FROM raw.eth_ohlcv
              WHERE exchange = %s AND symbol = %s AND timeframe = %s
            ), expected AS (
              SELECT generate_series(min_ts, max_ts, interval '{step}') AS ts
              FROM bounds
              WHERE min_ts IS NOT NULL
            )
            SELECT e.ts
            FROM expected e
            LEFT JOIN raw.eth_ohlcv r
              ON r.exchange = %s AND r.symbol = %s AND r.timeframe = %s AND r.ts = e.ts
            WHERE r.ts IS NULL
            ORDER BY e.ts
            """,
            (exchange, symbol, timeframe, exchange, symbol, timeframe),
        )
        rows = cur.fetchall()
    return [r[0] for r in rows]


def _group_consecutive(missing: list[datetime], timeframe: str) -> list[tuple[datetime, datetime]]:
    if not missing:
        return []
    step = timedelta(seconds=_timeframe_seconds(timeframe))
    grouped: list[tuple[datetime, datetime]] = []
    start = missing[0]
    prev = missing[0]
    for ts in missing[1:]:
        if (ts - prev) == step:
            prev = ts
            continue
        grouped.append((start, prev))
        start, prev = ts, ts
    grouped.append((start, prev))
    return grouped


def _fetch_range_rows(
    exchange_name: str,
    symbol: str,
    timeframe: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[Any, ...]]:
    if end_ts < start_ts:
        return []

    ex = _get_exchange_client(exchange_name)
    tf_ms = _timeframe_ms(timeframe)
    since_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)

    out: list[tuple[Any, ...]] = []
    seen = set()
    safety = 0
    max_retries = int(os.getenv("CCXT_MAX_RETRIES", "5"))
    backoff_base = float(os.getenv("CCXT_BACKOFF_BASE_SECONDS", "0.8"))

    while since_ms <= end_ms and safety < 10000:
        safety += 1
        candles = None
        last_error: Exception | None = None
        for retry in range(max_retries):
            try:
                candles = ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                sleep_seconds = backoff_base * (2**retry)
                logger.warning(
                    "ccxt fetch failed (exchange=%s symbol=%s timeframe=%s since_ms=%s retry=%s/%s): %s",
                    exchange_name,
                    symbol,
                    timeframe,
                    since_ms,
                    retry + 1,
                    max_retries,
                    exc,
                )
                time.sleep(sleep_seconds)

        if last_error is not None:
            raise RuntimeError(f"ccxt fetch failed after retries: {last_error}") from last_error

        if not candles:
            break

        last_ms = candles[-1][0]
        for ts_ms, op, hi, lo, cl, vol in candles:
            if ts_ms < since_ms or ts_ms > end_ms:
                continue
            if ts_ms in seen:
                continue
            seen.add(ts_ms)
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            out.append(
                (
                    exchange_name,
                    symbol,
                    timeframe,
                    ts,
                    op,
                    hi,
                    lo,
                    cl,
                    vol,
                    datetime.now(tz=UTC),
                )
            )

        next_since = last_ms + tf_ms
        if next_since <= since_ms:
            break
        since_ms = next_since
        time.sleep(ex.rateLimit / 1000 if getattr(ex, "rateLimit", 0) else 0)

    return out


def _upsert(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    execute_values(
        """
        INSERT INTO raw.eth_ohlcv (
          exchange, symbol, timeframe, ts, open, high, low, close, volume, ingested_at
        ) VALUES %s
        ON CONFLICT (exchange, symbol, timeframe, ts)
        DO UPDATE SET
          open = EXCLUDED.open,
          high = EXCLUDED.high,
          low = EXCLUDED.low,
          close = EXCLUDED.close,
          volume = EXCLUDED.volume,
          ingested_at = EXCLUDED.ingested_at
        """,
        rows,
    )
    return len(rows)


def get_data_status(exchange: str | None = None, symbol: str | None = None, timeframe: str | None = None) -> dict[str, Any]:
    exchange = exchange or default_exchange()
    symbol = symbol or default_symbol()
    timeframe = timeframe or default_timeframe()

    bounds = _bounds(exchange, symbol, timeframe) or {}
    min_ts = bounds.get("min_ts")
    max_ts = bounds.get("max_ts")
    row_count = int(bounds.get("row_count") or 0)
    last_updated = bounds.get("last_updated")

    gap_count = 0
    if min_ts is not None and max_ts is not None:
        missing = _missing_timestamps(exchange, symbol, timeframe)
        gap_count = len(missing)

    return {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "min_ts": min_ts,
        "max_ts": max_ts,
        "row_count": row_count,
        "gap_count": gap_count,
        "has_gaps": gap_count > 0,
        "last_updated": last_updated,
    }


def sync_raw_ohlcv_once(
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    initial_backfill_days: int | None = None,
) -> SyncResult:
    exchange = exchange or default_exchange()
    symbol = symbol or default_symbol()
    timeframe = timeframe or default_timeframe()
    if initial_backfill_days is None:
        # BACKFILL_FROM_DATE 우선, 없으면 INITIAL_BACKFILL_DAYS, 기본값 2023-01-01
        from_date_str = os.getenv("BACKFILL_FROM_DATE", "")
        if from_date_str:
            try:
                from datetime import timezone as _tz
                _dt = datetime.strptime(from_date_str.strip(), "%Y-%m-%d").replace(tzinfo=_tz.utc)
                initial_backfill_days = int((datetime.now(tz=_tz.utc) - _dt).days)
            except ValueError:
                initial_backfill_days = int(os.getenv("INITIAL_BACKFILL_DAYS", "0")) or 365 * 3  # 기본 3년
        else:
            env_days = int(os.getenv("INITIAL_BACKFILL_DAYS", "0"))
            initial_backfill_days = env_days if env_days > 0 else 365 * 3  # 기본 3년
    else:
        initial_backfill_days = int(initial_backfill_days)

    status = get_data_status(exchange, symbol, timeframe)
    now_boundary = _now_boundary_utc(timeframe)
    step_seconds = _timeframe_seconds(timeframe)

    inserted = 0
    ranges_filled = 0
    latest_filled = False

    if status["row_count"] == 0:
        start = now_boundary - timedelta(days=initial_backfill_days)
        rows = _fetch_range_rows(exchange, symbol, timeframe, start, now_boundary)
        inserted += _upsert(rows)
        ranges_filled += 1 if rows else 0
        latest_filled = bool(rows)
        return SyncResult(inserted_rows=inserted, ranges_filled=ranges_filled, latest_filled=latest_filled)

    missing = _missing_timestamps(exchange, symbol, timeframe)
    ranges = _group_consecutive(missing, timeframe)
    for start_ts, end_ts in ranges:
        rows = _fetch_range_rows(
            exchange,
            symbol,
            timeframe,
            _to_timeframe_boundary_utc(start_ts, timeframe),
            _to_timeframe_boundary_utc(end_ts, timeframe),
        )
        inserted += _upsert(rows)
        ranges_filled += 1 if rows else 0

    max_ts = status["max_ts"]
    if max_ts is not None:
        start_latest = _to_timeframe_boundary_utc(max_ts + timedelta(seconds=step_seconds), timeframe)
        if start_latest <= now_boundary:
            rows = _fetch_range_rows(exchange, symbol, timeframe, start_latest, now_boundary)
            inserted += _upsert(rows)
            latest_filled = bool(rows)

    return SyncResult(inserted_rows=inserted, ranges_filled=ranges_filled, latest_filled=latest_filled)
