"""
Extended technical indicator computation pipeline.
Reads 15m OHLCV from raw.eth_ohlcv and computes:

[기존]
  - RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14)
  - OBV, VWAP(24h rolling), Stochastic %K/%D(14,3)
  - Volatility regime (low/medium/high)
  - Multi-timeframe signals (1h, 4h via pandas resample)
  - ML classification target (4-bar forward direction)
  - ML regression targets: forward returns at 1h/3h/6h horizons

[추가 - 정교화]
  원칙 1: 값보다 이벤트를
    - macd_golden_cross, macd_dead_cross (MACD 돌파 이벤트)
    - stoch_golden_cross, stoch_dead_cross (Stoch K/D 돌파 이벤트)
    - rsi_cross_30_up, rsi_cross_70_down (RSI 임계선 돌파 이벤트)
    - volume_above_avg (거래량 이벤트)
    - ma_regular_arrangement (EMA 정배열 이벤트)

  원칙 2: 방향성을 피처로
    - rsi_prev, macd_hist_prev, atr_pct_prev, bb_pct_prev (전봉 값 → 방향성)

  원칙 3: 시장 국면 정교화
    - ADX(14): 추세 강도 지표
    - market_regime_detail: 5단계 (mania/bullish/sideways/bearish/panic)
    - shock_detected: 급락 + 거래량 폭증 감지

Incremental mode (--incremental):
  Uses pipeline.water_marks HWM to process only new rows.
"""
import argparse
import os
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# Rolling warmup bars for vol_regime (30-day rolling quantile)
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
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_ts FROM pipeline.water_marks WHERE pipeline_name = %s",
            (pipeline_name,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def set_water_mark(conn, pipeline_name: str, last_ts, rows_processed: int):
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


# ── 기존 지표 함수들 ────────────────────────────────────────────────────────────

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
    low_thresh = atr_pct.rolling(window, min_periods=100).quantile(0.33)
    high_thresh = atr_pct.rolling(window, min_periods=100).quantile(0.67)
    regime = pd.Series("medium", index=atr_pct.index)
    regime[atr_pct < low_thresh] = "low"
    regime[atr_pct > high_thresh] = "high"
    return regime


def compute_mtf_signal(close_15m: pd.Series, resample_rule: str) -> pd.Series:
    ohlc = close_15m.resample(resample_rule).last().dropna()
    ema20 = ohlc.ewm(span=20, adjust=False).mean()
    ema50 = ohlc.ewm(span=50, adjust=False).mean()
    ratio = (ema20 - ema50) / (ema50 + 1e-9)
    signal = pd.Series(0, index=ohlc.index, dtype="int8")
    signal[ratio > 0.001] = 1
    signal[ratio < -0.001] = -1
    return signal.reindex(close_15m.index, method="ffill").fillna(0).astype("int8")


def compute_target_direction(close: pd.Series, n_bars: int = 4,
                              threshold: float = 0.003) -> pd.Series:
    fwd_return = close.shift(-n_bars) / close - 1
    target = pd.Series(0, index=close.index, dtype="int8")
    target[fwd_return > threshold] = 1
    target[fwd_return < -threshold] = -1
    target[fwd_return.isna()] = np.nan
    return target


def compute_target_returns(close: pd.Series) -> dict:
    horizons = {"1h": 4, "3h": 12, "6h": 24}
    return {
        f"target_return_{h}": (close.shift(-n) / close) - 1.0
        for h, n in horizons.items()
    }


# ── 신규 지표 함수들 ────────────────────────────────────────────────────────────

def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 14) -> pd.Series:
    """
    ADX(14): 추세 강도 지표 (0~100).
    25 이상 → 추세 강함, 50 이상 → 추세 극강, 25 미만 → 추세 없음(횡보).
    MarketRegime 5단계 분류에 사용.
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    dm_plus = (high - prev_high).clip(lower=0)
    dm_minus = (prev_low - low).clip(lower=0)
    # 양방향 모두 양수인 경우 큰 쪽만 살림
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0.0)
    dm_minus = dm_minus.where(dm_minus > dm_plus.shift(0).where(dm_plus > dm_minus, 0.0) + 1e-9, 0.0)
    # 간략화 버전 (Wilder EMA)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    di_plus = 100 * dm_plus.ewm(alpha=1 / period, adjust=False).mean() / (atr_s + 1e-9)
    di_minus = 100 * dm_minus.ewm(alpha=1 / period, adjust=False).mean() / (atr_s + 1e-9)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-9)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx.clip(0, 100)


def compute_event_features(
    rsi: pd.Series,
    macd_line: pd.Series,
    macd_signal_line: pd.Series,
    stoch_k: pd.Series,
    stoch_d: pd.Series,
    volume: pd.Series,
    volume_sma20: pd.Series,
    ema_20: pd.Series,
    ema_50: pd.Series,
    ema_200: pd.Series,
) -> dict[str, pd.Series]:
    """
    원칙 1: 값보다 이벤트를
    임계점 돌파 순간을 0/1 이진 피처로 변환.
    연속 수치보다 "무언가 일어났는가"가 트레이딩 신호로 더 의미 있음.
    """
    # MACD 크로스 (MACD가 Signal을 돌파하는 순간)
    macd_above = (macd_line > macd_signal_line).astype(int)
    macd_golden_cross = ((macd_above == 1) & (macd_above.shift(1) == 0)).astype(int)
    macd_dead_cross   = ((macd_above == 0) & (macd_above.shift(1) == 1)).astype(int)

    # Stochastic 크로스 (K가 D를 돌파하는 순간)
    stoch_above = (stoch_k > stoch_d).astype(int)
    stoch_golden_cross = ((stoch_above == 1) & (stoch_above.shift(1) == 0)).astype(int)
    stoch_dead_cross   = ((stoch_above == 0) & (stoch_above.shift(1) == 1)).astype(int)

    # RSI 임계선 돌파 이벤트
    rsi_cross_30_up   = ((rsi > 30) & (rsi.shift(1) <= 30)).astype(int)   # 과매도 탈출
    rsi_cross_70_down = ((rsi < 70) & (rsi.shift(1) >= 70)).astype(int)   # 과매수 이탈

    # 거래량 이벤트 (거래량 > 20일 평균)
    volume_above_avg = (volume > volume_sma20).astype(int)

    # EMA 정배열 이벤트 (EMA20 > EMA50 > EMA200)
    ma_regular_arrangement = (
        (ema_20 > ema_50) & (ema_50 > ema_200)
    ).astype(int)

    return {
        "macd_golden_cross":       macd_golden_cross,
        "macd_dead_cross":         macd_dead_cross,
        "stoch_golden_cross":      stoch_golden_cross,
        "stoch_dead_cross":        stoch_dead_cross,
        "rsi_cross_30_up":         rsi_cross_30_up,
        "rsi_cross_70_down":       rsi_cross_70_down,
        "volume_above_avg":        volume_above_avg,
        "ma_regular_arrangement":  ma_regular_arrangement,
    }


def compute_direction_features(
    rsi: pd.Series,
    macd_hist: pd.Series,
    atr_pct: pd.Series,
    bb_pct: pd.Series,
) -> dict[str, pd.Series]:
    """
    원칙 2: 방향성을 피처로
    지표의 이전값을 저장하여 모델이 방향성(모멘텀)을 학습하도록 함.
    "오늘 RSI 40"보다 "어제 45→오늘 40 (하락 중)"이 더 의미 있음.
    """
    return {
        "rsi_prev":       rsi.shift(1),
        "macd_hist_prev": macd_hist.shift(1),
        "atr_pct_prev":   atr_pct.shift(1),
        "bb_pct_prev":    bb_pct.shift(1),
    }


def compute_market_regime_detail(
    rsi: pd.Series,
    adx: pd.Series,
    ema_20: pd.Series,
    ema_50: pd.Series,
    ema_200: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    vol_regime: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """
    원칙 3: 시장 국면 정교화 (5단계)

    같은 RSI 30이라도 강세장과 공포장에서 의미가 완전히 다름.
    MA 정배열, ADX 추세 강도, RSI 위치를 종합해 5단계로 분류.

    광기장 (mania):   MA 정배열 + ADX 극강(>50) + RSI 과열(>70)
    강세장 (bullish): MA 정배열 + ADX 강세(>25)
    횡보장 (sideways): 추세 불명확 (ADX < 25)
    하락장 (bearish): MA 역배열 + ADX 강세(>25)
    공포장 (panic):   MA 역배열 + ADX 극강(>50) + RSI 침체(<30)

    + 쇼크 감지: 급락(-3% 이상) + 거래량 폭증(200% 이상) → 즉시 panic
    + 신뢰도: 지표 일치도에 따라 신뢰도 낮으면 이전 국면 유지

    Returns: (market_regime_detail, shock_detected)
    """
    ma_bullish = (ema_20 > ema_50) & (ema_50 > ema_200)
    ma_bearish = (ema_20 < ema_50) & (ema_50 < ema_200)
    adx_strong = adx > 25
    adx_extreme = adx > 50
    rsi_hot = rsi > 70
    rsi_cold = rsi < 30

    # 쇼크 감지: 1봉 급락 + 거래량 폭증
    bar_return = close.pct_change()
    vol_sma20 = volume.rolling(20, min_periods=5).mean()
    shock = (bar_return < -0.03) & (volume > vol_sma20 * 2.0)
    shock_detected = shock.astype(int)

    # 신뢰도 점수: 지표들의 방향 일치도
    # ma_bullish, adx_strong, rsi 위치 세 가지 일치 시 높음
    bullish_signals = ma_bullish.astype(int) + adx_strong.astype(int) + (rsi > 50).astype(int)
    bearish_signals = ma_bearish.astype(int) + adx_strong.astype(int) + (rsi < 50).astype(int)
    high_confidence = (bullish_signals >= 2) | (bearish_signals >= 2)

    # 국면 분류
    regime = pd.Series("sideways", index=rsi.index, dtype=object)

    # 낮은 신뢰도일 때는 기본 횡보장 유지 → 후처리에서 ffill
    regime[ma_bullish & adx_strong] = "bullish"
    regime[ma_bullish & adx_extreme & rsi_hot] = "mania"
    regime[ma_bearish & adx_strong] = "bearish"
    regime[ma_bearish & adx_extreme & rsi_cold] = "panic"

    # 쇼크 감지 시 즉시 panic 전환 (신뢰도 무관)
    regime[shock] = "panic"

    # 신뢰도 낮은 구간은 이전 국면 유지 (forward fill)
    # 단, 쇼크는 신뢰도 무관하게 즉시 반영
    regime_smooth = regime.copy()
    low_conf_mask = ~high_confidence & ~shock
    regime_smooth[low_conf_mask] = np.nan
    regime_smooth = regime_smooth.ffill().fillna("sideways")

    return regime_smooth, shock_detected


def compute_round_number_features(close: pd.Series) -> dict[str, pd.Series]:
    """
    심리적 지지/저항선 피처 (Round Number Psychology)

    암호화폐 트레이더들은 100, 500, 1000 단위 가격을 심리적 기준으로 삼음.
    같은 기술지표라도 2000달러 부근에서 나오는 신호와
    2050달러 부근에서 나오는 신호는 의미가 다름.

    [연속형 피처]
    - dist_to_round_100  : 가격이 100단위 기준에서 얼마나 떨어져 있는가 (0~0.5)
    - dist_to_round_500  : 500단위 기준 거리 (0~0.5)
    - dist_to_round_1000 : 1000단위 기준 거리 (0~0.5)

    [이벤트 피처 - 원칙 1 계열]
    - near_round_100  : 100단위 ±1% 이내 진입 (0/1)
    - near_round_500  : 500단위 ±1% 이내 진입 (0/1)
    - near_round_1000 : 1000단위 ±1.5% 이내 진입 (0/1)  ← 더 강한 심리선
    - broke_round_100_up   : 이번 봉에서 100단위를 상향 돌파 (0/1)
    - broke_round_100_down : 이번 봉에서 100단위를 하향 이탈 (0/1)
    """
    # ── 연속형: 최근접 round level까지의 거리 (0=딱 붙음, 0.5=중간) ──────────
    mod_100 = close % 100
    dist_to_round_100 = pd.concat([mod_100, 100 - mod_100], axis=1).min(axis=1) / 100

    mod_500 = close % 500
    dist_to_round_500 = pd.concat([mod_500, 500 - mod_500], axis=1).min(axis=1) / 500

    mod_1000 = close % 1000
    dist_to_round_1000 = pd.concat([mod_1000, 1000 - mod_1000], axis=1).min(axis=1) / 1000

    # ── 이벤트형: 심리선 ±N% 이내 근접 ─────────────────────────────────────────
    # 100단위 ±1% (예: ETH 2000 기준 ±20달러 = 1980~2020)
    near_round_100 = (dist_to_round_100 < 0.01).astype(int)

    # 500단위 ±1% (예: ETH 2000 기준 ±20달러)
    near_round_500 = (dist_to_round_500 < 0.01).astype(int)

    # 1000단위 ±1.5% (예: ETH 2000 기준 ±30달러, 더 중요한 심리선)
    near_round_1000 = (dist_to_round_1000 < 0.015).astype(int)

    # ── 이벤트형: 100단위 돌파/이탈 순간 ───────────────────────────────────────
    # 현재 봉의 100단위 위치 (몇 번째 100단위 구간인가)
    level_100 = (close // 100).astype(int)
    broke_round_100_up   = ((level_100 > level_100.shift(1)) & (level_100.shift(1).notna())).astype(int)
    broke_round_100_down = ((level_100 < level_100.shift(1)) & (level_100.shift(1).notna())).astype(int)

    return {
        "dist_to_round_100":    dist_to_round_100,
        "dist_to_round_500":    dist_to_round_500,
        "dist_to_round_1000":   dist_to_round_1000,
        "near_round_100":       near_round_100,
        "near_round_500":       near_round_500,
        "near_round_1000":      near_round_1000,
        "broke_round_100_up":   broke_round_100_up,
        "broke_round_100_down": broke_round_100_down,
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

    # ── 레거시 피처 (하위 호환) ──────────────────────────────────────────────────
    df["returns_1h"] = close.pct_change().fillna(0.0)
    df["returns_1bar"] = close.pct_change(1).fillna(0.0)
    df["returns_4bar"] = close.pct_change(4).fillna(0.0)
    df["returns_16bar"] = close.pct_change(16).fillna(0.0)
    df["sma_5"] = close.rolling(5).mean().bfill()
    df["sma_10"] = close.rolling(10).mean().bfill()
    df["sma_20"] = close.rolling(20).mean().bfill()
    df["ema_10"] = close.ewm(span=10, adjust=False).mean()
    df["ema_20"] = close.ewm(span=20, adjust=False).mean()
    df["ema_50"] = close.ewm(span=50, adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()
    df["volatility_10"] = df["returns_1h"].rolling(10).std().fillna(0.0)
    df["volume_sma_20"] = volume.rolling(20, min_periods=5).mean()
    df["volume_ratio"] = (volume / (df["volume_sma_20"] + 1e-9)).clip(0, 10)

    # ── 기본 지표 ────────────────────────────────────────────────────────────────
    df["rsi_14"] = compute_rsi(close)
    df["macd_line"], df["macd_signal"], df["macd_hist"] = compute_macd(close)
    df["bb_upper"], df["bb_middle"], df["bb_lower"], df["bb_width"], df["bb_pct"] = compute_bollinger(close)
    df["atr_14"] = compute_atr(high, low, close)
    df["atr_pct"] = df["atr_14"] / (close + 1e-9)
    df["obv"] = compute_obv(close, volume)
    df["vwap"] = compute_vwap(high, low, close, volume)
    df["stoch_k"], df["stoch_d"] = compute_stochastic(high, low, close)

    # ── 신규: ADX ────────────────────────────────────────────────────────────────
    df["adx"] = compute_adx(high, low, close)

    # ── 신규: 방향성 피처 (원칙 2) ───────────────────────────────────────────────
    dir_feats = compute_direction_features(
        rsi=df["rsi_14"],
        macd_hist=df["macd_hist"],
        atr_pct=df["atr_pct"],
        bb_pct=df["bb_pct"],
    )
    for col, series in dir_feats.items():
        df[col] = series

    # ── 신규: 이벤트 피처 (원칙 1) ───────────────────────────────────────────────
    event_feats = compute_event_features(
        rsi=df["rsi_14"],
        macd_line=df["macd_line"],
        macd_signal_line=df["macd_signal"],
        stoch_k=df["stoch_k"],
        stoch_d=df["stoch_d"],
        volume=volume,
        volume_sma20=df["volume_sma_20"],
        ema_20=df["ema_20"],
        ema_50=df["ema_50"],
        ema_200=df["ema_200"],
    )
    for col, series in event_feats.items():
        df[col] = series

    # ── 기존: 변동성 레짐 (3단계) ────────────────────────────────────────────────
    df["vol_regime"] = compute_vol_regime(df["atr_pct"])

    # ── 신규: 시장 국면 5단계 (원칙 3) ───────────────────────────────────────────
    df["market_regime_detail"], df["shock_detected"] = compute_market_regime_detail(
        rsi=df["rsi_14"],
        adx=df["adx"],
        ema_20=df["ema_20"],
        ema_50=df["ema_50"],
        ema_200=df["ema_200"],
        close=close,
        volume=volume,
        vol_regime=df["vol_regime"],
    )

    # ── 신규: 심리적 지지/저항선 피처 ────────────────────────────────────────────
    round_feats = compute_round_number_features(close)
    for col, series in round_feats.items():
        df[col] = series

    # ── MTF 신호 ─────────────────────────────────────────────────────────────────
    df["signal_1h"] = compute_mtf_signal(close, "1h")
    df["signal_4h"] = compute_mtf_signal(close, "4h")

    # ── ML 타겟 ──────────────────────────────────────────────────────────────────
    df["target_direction"] = compute_target_direction(close)
    for col, series in compute_target_returns(close).items():
        df[col] = series

    df = df.reset_index()

    # Incremental 모드: HWM 이후 행만 upsert
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
            # 레거시
            float(row["returns_1h"]),
            float(row["sma_5"]),
            float(row["sma_10"]),
            float(row["ema_10"]),
            float(row["volatility_10"]),
            # 기본 지표
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
            # 회귀 타겟
            _f("target_return_1h"),
            _f("target_return_3h"),
            _f("target_return_6h"),
            # 신규: returns bars
            _f("returns_1bar"),
            _f("returns_4bar"),
            _f("returns_16bar"),
            # 신규: ema
            _f("ema_20"),
            _f("ema_50"),
            _f("ema_200"),
            _f("sma_20"),
            _f("volume_sma_20"),
            _f("volume_ratio"),
            # 신규: ADX
            _f("adx"),
            # 신규: 방향성 피처 (원칙 2)
            _f("rsi_prev"),
            _f("macd_hist_prev"),
            _f("atr_pct_prev"),
            _f("bb_pct_prev"),
            # 신규: 이벤트 피처 (원칙 1)
            _i("macd_golden_cross"),
            _i("macd_dead_cross"),
            _i("stoch_golden_cross"),
            _i("stoch_dead_cross"),
            _i("rsi_cross_30_up"),
            _i("rsi_cross_70_down"),
            _i("volume_above_avg"),
            _i("ma_regular_arrangement"),
            # 신규: 5단계 시장 국면 (원칙 3)
            _s("market_regime_detail"),
            _i("shock_detected"),
            # 신규: 심리적 지지/저항선
            _f("dist_to_round_100"),
            _f("dist_to_round_500"),
            _f("dist_to_round_1000"),
            _i("near_round_100"),
            _i("near_round_500"),
            _i("near_round_1000"),
            _i("broke_round_100_up"),
            _i("broke_round_100_down"),
        ))

    insert_sql = """
        INSERT INTO features.eth_features (
            exchange, symbol, ts, close,
            returns_1h, sma_5, sma_10, ema_10, volatility_10,
            rsi_14, macd_line, macd_signal, macd_hist,
            bb_upper, bb_middle, bb_lower, bb_width, bb_pct,
            atr_14, atr_pct, obv, vwap, stoch_k, stoch_d,
            vol_regime, signal_1h, signal_4h, target_direction,
            target_return_1h, target_return_3h, target_return_6h,
            returns_1bar, returns_4bar, returns_16bar,
            ema_20, ema_50, ema_200, sma_20, volume_sma_20, volume_ratio,
            adx,
            rsi_prev, macd_hist_prev, atr_pct_prev, bb_pct_prev,
            macd_golden_cross, macd_dead_cross,
            stoch_golden_cross, stoch_dead_cross,
            rsi_cross_30_up, rsi_cross_70_down,
            volume_above_avg, ma_regular_arrangement,
            market_regime_detail, shock_detected,
            dist_to_round_100, dist_to_round_500, dist_to_round_1000,
            near_round_100, near_round_500, near_round_1000,
            broke_round_100_up, broke_round_100_down
        )
        VALUES %s
        ON CONFLICT (exchange, symbol, ts)
        DO UPDATE SET
            close                  = EXCLUDED.close,
            returns_1h             = EXCLUDED.returns_1h,
            sma_5                  = EXCLUDED.sma_5,
            sma_10                 = EXCLUDED.sma_10,
            ema_10                 = EXCLUDED.ema_10,
            volatility_10          = EXCLUDED.volatility_10,
            rsi_14                 = EXCLUDED.rsi_14,
            macd_line              = EXCLUDED.macd_line,
            macd_signal            = EXCLUDED.macd_signal,
            macd_hist              = EXCLUDED.macd_hist,
            bb_upper               = EXCLUDED.bb_upper,
            bb_middle              = EXCLUDED.bb_middle,
            bb_lower               = EXCLUDED.bb_lower,
            bb_width               = EXCLUDED.bb_width,
            bb_pct                 = EXCLUDED.bb_pct,
            atr_14                 = EXCLUDED.atr_14,
            atr_pct                = EXCLUDED.atr_pct,
            obv                    = EXCLUDED.obv,
            vwap                   = EXCLUDED.vwap,
            stoch_k                = EXCLUDED.stoch_k,
            stoch_d                = EXCLUDED.stoch_d,
            vol_regime             = EXCLUDED.vol_regime,
            signal_1h              = EXCLUDED.signal_1h,
            signal_4h              = EXCLUDED.signal_4h,
            target_direction       = EXCLUDED.target_direction,
            target_return_1h       = EXCLUDED.target_return_1h,
            target_return_3h       = EXCLUDED.target_return_3h,
            target_return_6h       = EXCLUDED.target_return_6h,
            returns_1bar           = EXCLUDED.returns_1bar,
            returns_4bar           = EXCLUDED.returns_4bar,
            returns_16bar          = EXCLUDED.returns_16bar,
            ema_20                 = EXCLUDED.ema_20,
            ema_50                 = EXCLUDED.ema_50,
            ema_200                = EXCLUDED.ema_200,
            sma_20                 = EXCLUDED.sma_20,
            volume_sma_20          = EXCLUDED.volume_sma_20,
            volume_ratio           = EXCLUDED.volume_ratio,
            adx                    = EXCLUDED.adx,
            rsi_prev               = EXCLUDED.rsi_prev,
            macd_hist_prev         = EXCLUDED.macd_hist_prev,
            atr_pct_prev           = EXCLUDED.atr_pct_prev,
            bb_pct_prev            = EXCLUDED.bb_pct_prev,
            macd_golden_cross      = EXCLUDED.macd_golden_cross,
            macd_dead_cross        = EXCLUDED.macd_dead_cross,
            stoch_golden_cross     = EXCLUDED.stoch_golden_cross,
            stoch_dead_cross       = EXCLUDED.stoch_dead_cross,
            rsi_cross_30_up        = EXCLUDED.rsi_cross_30_up,
            rsi_cross_70_down      = EXCLUDED.rsi_cross_70_down,
            volume_above_avg       = EXCLUDED.volume_above_avg,
            ma_regular_arrangement = EXCLUDED.ma_regular_arrangement,
            market_regime_detail   = EXCLUDED.market_regime_detail,
            shock_detected         = EXCLUDED.shock_detected,
            dist_to_round_100      = EXCLUDED.dist_to_round_100,
            dist_to_round_500      = EXCLUDED.dist_to_round_500,
            dist_to_round_1000     = EXCLUDED.dist_to_round_1000,
            near_round_100         = EXCLUDED.near_round_100,
            near_round_500         = EXCLUDED.near_round_500,
            near_round_1000        = EXCLUDED.near_round_1000,
            broke_round_100_up     = EXCLUDED.broke_round_100_up,
            broke_round_100_down   = EXCLUDED.broke_round_100_down,
            feature_created_at     = NOW()
    """

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, insert_sql, rows)
    conn.commit()

    max_ts = df["ts"].max()
    set_water_mark(conn, "feature_pipeline", max_ts, len(rows))
    conn.close()
    print(f"[technical_indicators] Upserted {len(rows)} rows | {len(rows[0])} features | HWM → {max_ts}")


if __name__ == "__main__":
    main()
