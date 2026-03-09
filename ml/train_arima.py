from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2


def load_env_file(path: str = ".env") -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("\"'")
    return out


def connect_db(env: dict):
    host = env.get("DB_HOST", "localhost")
    if host == "postgres":
        host = "localhost"
    return psycopg2.connect(
        host=host,
        port=int(env.get("DB_PORT", "5432")),
        user=env.get("DB_USER"),
        password=env.get("DB_PASSWORD"),
        dbname=env.get("DB_NAME"),
    )


def load_raw_1h(conn, exchange: str, symbol: str) -> pd.DataFrame:
    query = """
        SELECT ts, close
        FROM raw.eth_ohlcv
        WHERE exchange = %s AND symbol = %s AND timeframe = '1h'
        ORDER BY ts
    """
    return pd.read_sql(query, conn, params=(exchange, symbol))


def main():
    parser = argparse.ArgumentParser(description="Train a simple ARIMA model on 1h closes.")
    parser.add_argument("--exchange", default=os.getenv("EXCHANGE", "binance"))
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "ETH/USDT"))
    parser.add_argument("--order", default="2,1,2", help="ARIMA order p,d,q (e.g. 2,1,2)")
    parser.add_argument("--model_out", default="ml/model_registry/eth_arima_h6.pkl")
    args = parser.parse_args()

    try:
        from statsmodels.tsa.arima.model import ARIMA
    except Exception as e:
        raise SystemExit(f"statsmodels is required. Install with: pip install statsmodels\nImport error: {e}")

    p, d, q = (int(x) for x in args.order.split(","))

    env = {**load_env_file(".env"), **os.environ}
    conn = connect_db(env)
    df = load_raw_1h(conn, args.exchange, args.symbol)
    conn.close()

    if df.empty or len(df) < 50:
        raise SystemExit(f"Not enough data in raw.eth_ohlcv (rows={len(df)}).")

    series = df["close"].astype(float).to_numpy()
    model = ARIMA(series, order=(p, d, q))
    results = model.fit()

    out_path = Path(args.model_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results.save(str(out_path))
    print("saved:", str(out_path))


if __name__ == "__main__":
    main()
