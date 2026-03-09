import os
from datetime import datetime, timedelta, timezone

import ccxt
import psycopg2
import psycopg2.extras

UTC = timezone.utc
TIMEFRAME = "15m"


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def timeframe_seconds() -> int:
    return int(ccxt.Exchange.parse_timeframe(TIMEFRAME))


def floor_to_timeframe(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    seconds = timeframe_seconds()
    epoch = int(ts.astimezone(UTC).timestamp())
    floored = epoch - (epoch % seconds)
    return datetime.fromtimestamp(floored, tz=UTC)


def fetch_bounds(conn, exchange, symbol):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MIN(ts), MAX(ts), COUNT(*)
            FROM raw.eth_ohlcv
            WHERE exchange = %s AND symbol = %s AND timeframe = %s
            """,
            (exchange, symbol, TIMEFRAME),
        )
        row = cur.fetchone()
    return row[0], row[1], int(row[2] or 0)


def fetch_missing_timestamps(conn, exchange, symbol):
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH bounds AS (
              SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts
              FROM raw.eth_ohlcv
              WHERE exchange = %s AND symbol = %s AND timeframe = %s
            ), expected AS (
              SELECT generate_series(min_ts, max_ts, interval '15 minutes') AS ts
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
            (exchange, symbol, TIMEFRAME, exchange, symbol, TIMEFRAME),
        )
        rows = cur.fetchall()
    return [row[0] for row in rows]


def group_consecutive(missing):
    if not missing:
        return []
    step = timedelta(seconds=timeframe_seconds())
    grouped = []
    start = missing[0]
    prev = missing[0]
    for ts in missing[1:]:
        if ts - prev == step:
            prev = ts
            continue
        grouped.append((start, prev))
        start = ts
        prev = ts
    grouped.append((start, prev))
    return grouped


def fetch_range(exchange_client, exchange_name, symbol, start_ts, end_ts):
    if end_ts < start_ts:
        return []
    tf_ms = timeframe_seconds() * 1000
    since_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)
    seen = set()
    rows = []
    safety = 0

    while since_ms <= end_ms and safety < 10000:
        safety += 1
        candles = exchange_client.fetch_ohlcv(symbol, TIMEFRAME, since=since_ms, limit=1000)
        if not candles:
            break
        last_ms = candles[-1][0]
        for ts_ms, open_p, high_p, low_p, close_p, volume in candles:
            if ts_ms < since_ms or ts_ms > end_ms or ts_ms in seen:
                continue
            seen.add(ts_ms)
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            rows.append(
                (
                    exchange_name,
                    symbol,
                    TIMEFRAME,
                    ts,
                    open_p,
                    high_p,
                    low_p,
                    close_p,
                    volume,
                    datetime.now(tz=UTC),
                )
            )
        next_since = last_ms + tf_ms
        if next_since <= since_ms:
            break
        since_ms = next_since
    return rows


def upsert_rows(conn, rows):
    if not rows:
        return 0
    insert_sql = """
        INSERT INTO raw.eth_ohlcv (
            exchange, symbol, timeframe, ts, open, high, low, close, volume, ingested_at
        )
        VALUES %s
        ON CONFLICT (exchange, symbol, timeframe, ts)
        DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            ingested_at = EXCLUDED.ingested_at
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, insert_sql, rows)
    conn.commit()
    return len(rows)


def main():
    exchange_name = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")
    db_host = get_env("DB_HOST")
    db_port = int(get_env("DB_PORT", "5432"))
    db_user = get_env("DB_USER")
    db_pass = get_env("DB_PASSWORD")
    db_name = get_env("DB_NAME")
    initial_backfill_days = int(get_env("INITIAL_BACKFILL_DAYS", "30"))

    exchange = getattr(ccxt, exchange_name)({"enableRateLimit": True})
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_pass,
        dbname=db_name,
    )

    min_ts, max_ts, row_count = fetch_bounds(conn, exchange_name, symbol)
    now_slot = floor_to_timeframe(datetime.now(tz=UTC))

    if row_count == 0:
        start_ts = now_slot - timedelta(days=initial_backfill_days)
        rows = fetch_range(exchange, exchange_name, symbol, start_ts, now_slot)
        upsert_rows(conn, rows)
        conn.close()
        return

    missing = fetch_missing_timestamps(conn, exchange_name, symbol)
    for gap_start, gap_end in group_consecutive(missing):
        rows = fetch_range(exchange, exchange_name, symbol, floor_to_timeframe(gap_start), floor_to_timeframe(gap_end))
        upsert_rows(conn, rows)

    if max_ts is not None:
        next_ts = floor_to_timeframe(max_ts + timedelta(seconds=timeframe_seconds()))
        if next_ts <= now_slot:
            rows = fetch_range(exchange, exchange_name, symbol, next_ts, now_slot)
            upsert_rows(conn, rows)

    conn.close()


if __name__ == "__main__":
    main()
