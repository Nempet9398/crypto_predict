import os
from pathlib import Path

import pandas as pd
import psycopg2
from joblib import dump
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import LinearRegression


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def load_features(conn):
    query = """
        SELECT ts, close, returns_1h, sma_5, sma_10, ema_10, volatility_10
        FROM features.eth_features
        WHERE exchange = %s AND symbol = %s
        ORDER BY ts
    """
    df = pd.read_sql(query, conn, params=("binance", "ETH/USDT"))
    return df


def main():
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

    df = load_features(conn)
    conn.close()

    if df.empty or len(df) < 5:
        model = DummyRegressor(strategy="constant", constant=0.0)
        model.fit([[0.0]], [0.0])
    else:
        df = df.sort_values("ts")
        df["target"] = df["close"].shift(-1)
        df = df.dropna()
        X = df[["returns_1h", "sma_5", "sma_10", "ema_10", "volatility_10"]]
        y = df["target"]
        model = LinearRegression()
        model.fit(X, y)

    model_dir = Path(__file__).resolve().parent / "model_registry"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "eth_price_model.joblib"
    dump(model, model_path)


if __name__ == "__main__":
    main()
