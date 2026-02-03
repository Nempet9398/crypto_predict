import os
from datetime import timezone

import numpy as np
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
        SELECT ts, close
        FROM processed.eth_ohlcv_1h
        WHERE exchange = %s AND symbol = %s
        ORDER BY ts
    """
    df = pd.read_sql(query, conn, params=(exchange_name, symbol))
    if df.empty:
        conn.close()
        return

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    df["returns_1h"] = df["close"].pct_change().fillna(0.0)
    df["sma_5"] = df["close"].rolling(5).mean().bfill()
    df["sma_10"] = df["close"].rolling(10).mean().bfill()
    df["ema_10"] = df["close"].ewm(span=10, adjust=False).mean()
    df["volatility_10"] = df["returns_1h"].rolling(10).std().fillna(0.0)

    df = df.reset_index()

    rows = []
    for _, row in df.iterrows():
        rows.append(
            (
                exchange_name,
                symbol,
                row["ts"].to_pydatetime().replace(tzinfo=timezone.utc),
                float(row["close"]),
                float(row["returns_1h"]),
                float(row["sma_5"]),
                float(row["sma_10"]),
                float(row["ema_10"]),
                float(row["volatility_10"]),
            )
        )

    insert_sql = """
        INSERT INTO features.eth_features (
            exchange, symbol, ts, close, returns_1h, sma_5, sma_10, ema_10, volatility_10
        )
        VALUES %s
        ON CONFLICT (exchange, symbol, ts)
        DO UPDATE SET
            close = EXCLUDED.close,
            returns_1h = EXCLUDED.returns_1h,
            sma_5 = EXCLUDED.sma_5,
            sma_10 = EXCLUDED.sma_10,
            ema_10 = EXCLUDED.ema_10,
            volatility_10 = EXCLUDED.volatility_10,
            feature_created_at = NOW()
    """

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, insert_sql, rows)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
