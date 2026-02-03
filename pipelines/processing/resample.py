import os
from datetime import timezone

import pandas as pd
import psycopg2
import psycopg2.extras


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def main():
    exchange_name = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")
    db_host = get_env("DB_HOST")
    db_port = int(get_env("DB_PORT", "5432"))
    db_user = get_env("DB_USER")
    db_pass = get_env("DB_PASSWORD")
    db_name = get_env("DB_NAME")

    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_pass,
        dbname=db_name,
    )

    query = """
        SELECT ts, open, high, low, close, volume
        FROM raw.eth_ohlcv
        WHERE exchange = %s AND symbol = %s AND timeframe = '1h'
        ORDER BY ts
    """

    df = pd.read_sql(query, conn, params=(exchange_name, symbol))
    if df.empty:
        conn.close()
        return

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")

    resampled = df.resample("1H").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    resampled = resampled.dropna().reset_index()

    rows = []
    for _, row in resampled.iterrows():
        rows.append(
            (
                exchange_name,
                symbol,
                row["ts"].to_pydatetime().replace(tzinfo=timezone.utc),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
        )

    insert_sql = """
        INSERT INTO processed.eth_ohlcv_1h (
            exchange, symbol, ts, open, high, low, close, volume
        )
        VALUES %s
        ON CONFLICT (exchange, symbol, ts)
        DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            processed_at = NOW()
    """

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, insert_sql, rows)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
