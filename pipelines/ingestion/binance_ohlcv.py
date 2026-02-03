import os
from datetime import datetime, timezone

import ccxt
import psycopg2
import psycopg2.extras


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def fetch_last_timestamp(conn, exchange, symbol, timeframe):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(ts) FROM raw.eth_ohlcv
            WHERE exchange = %s AND symbol = %s AND timeframe = %s
            """,
            (exchange, symbol, timeframe),
        )
        row = cur.fetchone()
        return row[0]


def main():
    exchange_name = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")
    timeframe = get_env("TIMEFRAME", "1h")
    db_host = get_env("DB_HOST")
    db_port = int(get_env("DB_PORT", "5432"))
    db_user = get_env("DB_USER")
    db_pass = get_env("DB_PASSWORD")
    db_name = get_env("DB_NAME")

    exchange = getattr(ccxt, exchange_name)({"enableRateLimit": True})

    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_pass,
        dbname=db_name,
    )

    last_ts = fetch_last_timestamp(conn, exchange_name, symbol, timeframe)
    since_ms = None
    if last_ts is not None:
        since_ms = int(last_ts.timestamp() * 1000) + 1

    candles = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
    if not candles:
        conn.close()
        return

    rows = []
    for ts_ms, open_p, high_p, low_p, close_p, volume in candles:
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        rows.append(
            (
                exchange_name,
                symbol,
                timeframe,
                ts,
                open_p,
                high_p,
                low_p,
                close_p,
                volume,
                datetime.now(timezone.utc),
            )
        )

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
    conn.close()


if __name__ == "__main__":
    main()
