"""
Extended technical indicator computation pipeline.
Reads 15m OHLCV from raw.eth_ohlcv and computes:
  - RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14)
  - OBV, VWAP(24h rolling), Stochastic %K/%D(14,3)
  - Volatility regime (low/medium/high)
  - Multi-timeframe signals (1h, 4h via pandas resample)
  - ML classification target (4-bar forward direction)
  - ML regression targets: forward returns at 1h/3h/6h horizons

Incremental mode (--incremental):
  Uses pipeline.water_marks HWM to process only new rows.
  Queries ts >= (last_ts - WARMUP_BARS*15min) for rolling indicator warmup,
  then UPSERTs only rows with ts > last_ts.
"""
import argparse
import os
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# Rolling warmup bars needed for vol_regime (30-day rolling quantile)
WARMUP_BARS = 2880  # 30 days * 96 bars/day at 15m


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def get_db_conn():
    return psycopg2.connect(
        host=get_env("DB_HOST"),
        port=int(get_env("DB_PORT", "5432")),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD"),
        dbname=get_env("DB_NAME"),
    )


def get_water_mark(conn, pipeline_name: str):
    """Return last_ts from pipeline.water_marks, or None if not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_ts FROM pipeline.water_marks WHERE pipeline_name = %s",
            (pipeline_name,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def set_water_mark(conn, pipeline_name: str, last_ts, rows_processed: int):
    """Upsert pipeline.water_marks with new last_ts."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO pipeline.water_marks (pipeline_name, last_ts, rows_processed, run_count, updated_at)
            VALUES (%s, %s, %s, 1, NOW())
            ON CONFLICT (pipeline_name)
            DO UPDATE SET
                last_ts        = EXCLUDED.last_ts,
                rows_processed = pipeline.water_marks.rows_processed + EXCLUDED.rows_processed,
                run_count      = pipeline.water_marks.run_count + 1,
                updated_at     = NOW()
        """, (pipeline_name, last_ts, rows_processed))
    conn.commit()


# ── Pure pandas/numpy indicator functions ──────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def compute_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


def compute_bollinger(close: pd.Series, period=20, num_std=2):
    bb_middle = close.rolling(period).mean()
    bb_std = close.rolling(period).std()
    bb_upper = bb_middle + num_std * bb_std
    bb_lower = bb_middle - num_std * bb_std
    bb_width = (bb_upper - bb_lower) / (bb_middle + 1e-9)
    bb_pct = (close - bb_lower) / (bb_upper - bb_lower + 1e-9)
    return bb_upper, bb_middle, bb_lower, bb_width, bb_pct


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def compute_vwap(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, window: int = 96) -> pd.Series:
    # 96 bars = 24h at 15m
    tp = (high + low + close) / 3
    return (tp * volume).rolling(window).sum() / (volume.rolling(window).sum() + 1e-9)


def compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                       k_period=14, d_period=3):
    low_k = low.rolling(k_period).min()
    high_k = high.rolling(k_period).max()
    stoch_k = 100 * (close - low_k) / (high_k - low_k + 1e-9)
    stoch_d = stoch_k.rolling(d_period).mean()
    return stoch_k, stoch_d


def compute_vol_regime(atr_pct: pd.Series, window: int = 2880) -> pd.Series:
    # 2880 = 30 days * 96 bars/day at 15m
    low_thresh = atr_pct.rolling(window, min_periods=100).quantile(0.33)
    high_thresh = atr_pct.rolling(window, min_periods=100).quantile(0.67)
    regime = pd.Series("medium", index=atr_pct.index)
    regime[atr_pct < low_thresh] = "low"
    regime[atr_pct > high_thresh] = "high"
    return regime


def compute_mtf_signal(close_15m: pd.Series, resample_rule: str) -> pd.Series:
    """Resample to higher TF, compute EMA20 vs EMA50, return +1/-1/0 signal aligned to 15m index."""
    ohlc = close_15m.resample(resample_rule).last().dropna()
    ema20 = ohlc.ewm(span=20, adjust=False).mean()
    ema50 = ohlc.ewm(span=50, adjust=False).mean()
    ratio = (ema20 - ema50) / (ema50 + 1e-9)
    signal = pd.Series(0, index=ohlc.index, dtype="int8")
    signal[ratio > 0.001] = 1
    signal[ratio < -0.001] = -1
    # Forward-fill back to 15m index (no lookahead: use last completed bar)
    return signal.reindex(close_15m.index, method="ffill").fillna(0).astype("int8")


def compute_target_direction(close: pd.Series, n_bars: int = 4,
                              threshold: float = 0.003) -> pd.Series:
    """
    Classification target: direction of price N bars ahead.
    +1 if forward_return > threshold, -1 if < -threshold, else 0.
    Last N rows will be NaN (unknown future).
    """
    fwd_return = close.shift(-n_bars) / close - 1
    target = pd.Series(0, index=close.index, dtype="int8")
    target[fwd_return > threshold] = 1
    target[fwd_return < -threshold] = -1
    target[fwd_return.isna()] = np.nan
    return target


def compute_target_returns(close: pd.Series) -> dict:
    """
    Regression targets: forward returns at 1h/3h/6h horizons (based on 15m bars).
    Returns dict of {col_name: pd.Series}.
    """
    horizons = {"1h": 4, "3h": 12, "6h": 24}
    return {
        f"target_return_{h}": (close.shift(-n) / close) - 1.0
        for h, n in horizons.items()
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--incremental", action="store_true",
                        help="Use High Water Mark to process only new rows")
    args = parser.parse_args()

    exchange_name = get_env("EXCHANGE", "binance")
    symbol = get_env("SYMBOL", "ETH/USDT")

    conn = get_db_conn()

    # Determine query range
    watermark_ts = None
    if args.incremental:
        watermark_ts = get_water_mark(conn, "feature_pipeline")
        if watermark_ts:
            # Fetch warmup buffer so rolling indicators are accurate
            from_ts = watermark_ts - timedelta(minutes=15 * WARMUP_BARS)
            print(f"[technical_indicators] Incremental mode: from {from_ts} (HWM={watermark_ts})")
            query = """
                SELECT ts, open, high, low, close, volume
                FROM raw.eth_ohlcv
                WHERE exchange = %s AND symbol = %s AND timeframe = '15m'
                  AND ts >= %s
                ORDER BY ts
            """
            df = pd.read_sql(query, conn, params=(exchange_name, symbol, from_ts))
        else:
            print("[technical_indicators] Incremental mode: no HWM found, running full load")
            query = """
                SELECT ts, open, high, low, close, volume
                FROM raw.eth_ohlcv
                WHERE exchange = %s AND symbol = %s AND timeframe = '15m'
                ORDER BY ts
            """
            df = pd.read_sql(query, conn, params=(exchange_name, symbol))
    else:
        print("[technical_indicators] Full mode: processing all rows")
        query = """
            SELECT ts, open, high, low, close, volume
            FROM raw.eth_ohlcv
            WHERE exchange = %s AND symbol = %s AND timeframe = '15m'
            ORDER BY ts
        """
        df = pd.read_sql(query, conn, params=(exchange_name, symbol))

    if df.empty:
        conn.close()
        return

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ── Legacy features (keep backward compat) ───────────────────────────────
    df["returns_1h"] = close.pct_change().fillna(0.0)
    df["sma_5"] = close.rolling(5).mean().bfill()
    df["sma_10"] = close.rolling(10).mean().bfill()
    df["ema_10"] = close.ewm(span=10, adjust=False).mean()
    df["volatility_10"] = df["returns_1h"].rolling(10).std().fillna(0.0)

    # ── New indicators ────────────────────────────────────────────────────────
    df["rsi_14"] = compute_rsi(close)
    df["macd_line"], df["macd_signal"], df["macd_hist"] = compute_macd(close)
    df["bb_upper"], df["bb_middle"], df["bb_lower"], df["bb_width"], df["bb_pct"] = compute_bollinger(close)
    df["atr_14"] = compute_atr(high, low, close)
    df["atr_pct"] = df["atr_14"] / (close + 1e-9)
    df["obv"] = compute_obv(close, volume)
    df["vwap"] = compute_vwap(high, low, close, volume)
    df["stoch_k"], df["stoch_d"] = compute_stochastic(high, low, close)

    # ── Volatility regime ─────────────────────────────────────────────────────
    df["vol_regime"] = compute_vol_regime(df["atr_pct"])

    # ── Multi-timeframe signals ───────────────────────────────────────────────
    df["signal_1h"] = compute_mtf_signal(close, "1h")
    df["signal_4h"] = compute_mtf_signal(close, "4h")

    # ── ML classification target ──────────────────────────────────────────────
    df["target_direction"] = compute_target_direction(close)

    # ── ML regression targets (forward returns) ───────────────────────────────
    for col, series in compute_target_returns(close).items():
        df[col] = series

    df = df.reset_index()

    # In incremental mode, only upsert rows newer than the HWM
    if args.incremental and watermark_ts is not None:
        watermark_ts_utc = watermark_ts.replace(tzinfo=timezone.utc) if watermark_ts.tzinfo is None else watermark_ts
        df = df[df["ts"] > watermark_ts_utc]
        print(f"[technical_indicators] Incremental: {len(df)} new rows to upsert")

    if df.empty:
        print("[technical_indicators] No new rows to process")
        conn.close()
        return

    rows = []
    for _, row in df.iterrows():
        def _f(col):
            v = row.get(col)
            return None if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)

        def _i(col):
            v = row.get(col)
            return None if (v is None or (isinstance(v, float) and np.isnan(v))) else int(v)

        def _s(col):
            v = row.get(col)
            return None if pd.isna(v) else str(v)

        rows.append((
            exchange_name,
            symbol,
            row["ts"].to_pydatetime().replace(tzinfo=timezone.utc),
            float(row["close"]),
            float(row["returns_1h"]),
            float(row["sma_5"]),
            float(row["sma_10"]),
            float(row["ema_10"]),
            float(row["volatility_10"]),
            # extended indicators
            _f("rsi_14"),
            _f("macd_line"),
            _f("macd_signal"),
            _f("macd_hist"),
            _f("bb_upper"),
            _f("bb_middle"),
            _f("bb_lower"),
            _f("bb_width"),
            _f("bb_pct"),
            _f("atr_14"),
            _f("atr_pct"),
            _f("obv"),
            _f("vwap"),
            _f("stoch_k"),
            _f("stoch_d"),
            _s("vol_regime"),
            _i("signal_1h"),
            _i("signal_4h"),
            _i("target_direction"),
            # regression targets
            _f("target_return_1h"),
            _f("target_return_3h"),
            _f("target_return_6h"),
        ))

    insert_sql = """
        INSERT INTO features.eth_features (
            exchange, symbol, ts, close,
            returns_1h, sma_5, sma_10, ema_10, volatility_10,
            rsi_14, macd_line, macd_signal, macd_hist,
            bb_upper, bb_middle, bb_lower, bb_width, bb_pct,
            atr_14, atr_pct, obv, vwap, stoch_k, stoch_d,
            vol_regime, signal_1h, signal_4h, target_direction,
            target_return_1h, target_return_3h, target_return_6h
        )
        VALUES %s
        ON CONFLICT (exchange, symbol, ts)
        DO UPDATE SET
            close           = EXCLUDED.close,
            returns_1h      = EXCLUDED.returns_1h,
            sma_5           = EXCLUDED.sma_5,
            sma_10          = EXCLUDED.sma_10,
            ema_10          = EXCLUDED.ema_10,
            volatility_10   = EXCLUDED.volatility_10,
            rsi_14          = EXCLUDED.rsi_14,
            macd_line       = EXCLUDED.macd_line,
            macd_signal     = EXCLUDED.macd_signal,
            macd_hist       = EXCLUDED.macd_hist,
            bb_upper        = EXCLUDED.bb_upper,
            bb_middle       = EXCLUDED.bb_middle,
            bb_lower        = EXCLUDED.bb_lower,
            bb_width        = EXCLUDED.bb_width,
            bb_pct          = EXCLUDED.bb_pct,
            atr_14          = EXCLUDED.atr_14,
            atr_pct         = EXCLUDED.atr_pct,
            obv             = EXCLUDED.obv,
            vwap            = EXCLUDED.vwap,
            stoch_k         = EXCLUDED.stoch_k,
            stoch_d         = EXCLUDED.stoch_d,
            vol_regime      = EXCLUDED.vol_regime,
            signal_1h       = EXCLUDED.signal_1h,
            signal_4h       = EXCLUDED.signal_4h,
            target_direction = EXCLUDED.target_direction,
            target_return_1h = EXCLUDED.target_return_1h,
            target_return_3h = EXCLUDED.target_return_3h,
            target_return_6h = EXCLUDED.target_return_6h,
            feature_created_at = NOW()
    """

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, insert_sql, rows)
    conn.commit()

    # Update High Water Mark
    max_ts = df["ts"].max()
    set_water_mark(conn, "feature_pipeline", max_ts, len(rows))
    conn.close()
    print(f"[technical_indicators] Upserted {len(rows)} rows for {symbol} (HWM → {max_ts})")


if __name__ == "__main__":
    main()
