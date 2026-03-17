CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS features;
CREATE SCHEMA IF NOT EXISTS pipeline;

-- Raw 15m OHLCV from Binance
CREATE TABLE IF NOT EXISTS raw.eth_ohlcv (
  exchange  TEXT        NOT NULL,
  symbol    TEXT        NOT NULL,
  timeframe TEXT        NOT NULL,
  ts        TIMESTAMPTZ NOT NULL,
  open      NUMERIC     NOT NULL,
  high      NUMERIC     NOT NULL,
  low       NUMERIC     NOT NULL,
  close     NUMERIC     NOT NULL,
  volume    NUMERIC     NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_eth_ohlcv_ts ON raw.eth_ohlcv (exchange, symbol, timeframe, ts DESC);

-- Technical indicators computed at 15m granularity
CREATE TABLE IF NOT EXISTS features.eth_features (
  exchange        TEXT        NOT NULL,
  symbol          TEXT        NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,
  close           NUMERIC     NOT NULL,
  open            NUMERIC,
  high            NUMERIC,
  low             NUMERIC,
  volume          NUMERIC,
  -- Returns (returns_1h = per-bar pct change, alias used by feature pipeline)
  returns_1h      NUMERIC,
  returns_1bar    NUMERIC,
  returns_4bar    NUMERIC,
  returns_16bar   NUMERIC,
  -- Moving averages
  sma_5           NUMERIC,
  sma_10          NUMERIC,
  sma_20          NUMERIC,
  ema_10          NUMERIC,
  ema_20          NUMERIC,
  ema_50          NUMERIC,
  ema_200         NUMERIC,
  volatility_10   NUMERIC,
  -- Momentum
  rsi_14          NUMERIC,
  macd_line       NUMERIC,
  macd_signal     NUMERIC,
  macd_hist       NUMERIC,
  stoch_k         NUMERIC,
  stoch_d         NUMERIC,
  -- Volatility
  bb_upper        NUMERIC,
  bb_middle       NUMERIC,
  bb_lower        NUMERIC,
  bb_width        NUMERIC,
  bb_pct          NUMERIC,
  atr_14          NUMERIC,
  atr_pct         NUMERIC,
  -- Volume
  obv             NUMERIC,
  vwap            NUMERIC,
  volume_sma_20   NUMERIC,
  volume_ratio    NUMERIC,
  -- Regime & MTF
  vol_regime      TEXT,
  signal_1h       SMALLINT,
  signal_4h       SMALLINT,
  -- ML target
  target_direction SMALLINT,
  feature_created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, ts)
);

CREATE INDEX IF NOT EXISTS idx_eth_features_ts ON features.eth_features (exchange, symbol, ts DESC);

-- Trading signals (ARIMA 제거, 기술지표+ML 기반)
CREATE TABLE IF NOT EXISTS features.signals (
  exchange          TEXT        NOT NULL,
  symbol            TEXT        NOT NULL,
  ts                TIMESTAMPTZ NOT NULL,
  timeframe         TEXT        NOT NULL DEFAULT '15m',
  signal            TEXT        NOT NULL, -- 'long' | 'short' | 'no-trade'
  score             NUMERIC,
  confidence        NUMERIC,
  tech_score        NUMERIC,              -- 기술지표 합성 스코어
  ml_direction      SMALLINT,
  ml_prob_up        NUMERIC,
  ml_prob_down      NUMERIC,
  mtf_1h            SMALLINT,
  mtf_4h            SMALLINT,
  vol_regime        TEXT,
  atr_14            NUMERIC,
  current_close     NUMERIC,
  tp_price          NUMERIC,
  sl_price          NUMERIC,
  rr_ratio          NUMERIC,
  position_size_pct NUMERIC,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, ts, timeframe)
);

-- ML predictions: 예측값 + 실제값 (정확도 추적용)
CREATE TABLE IF NOT EXISTS features.ml_predictions (
  exchange          TEXT        NOT NULL,
  symbol            TEXT        NOT NULL,
  ts                TIMESTAMPTZ NOT NULL, -- 예측 시점
  horizon_bars      INTEGER     NOT NULL, -- 몇 봉 후 예측 (4=1h, 16=4h, 32=8h)
  pred_return       NUMERIC,              -- 예측 수익률
  pred_close        NUMERIC,              -- 예측 종가
  pred_direction    SMALLINT,             -- 예측 방향 (1=up, -1=down, 0=neutral)
  pred_confidence   NUMERIC,              -- 예측 신뢰도 0~1
  actual_return     NUMERIC,              -- 실제 수익률 (나중에 채워짐)
  actual_close      NUMERIC,              -- 실제 종가
  actual_direction  SMALLINT,             -- 실제 방향
  model_id          TEXT,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, ts, horizon_bars)
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_ts ON features.ml_predictions (exchange, symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_actual ON features.ml_predictions (exchange, symbol, actual_direction) WHERE actual_direction IS NULL;

-- Pipeline high-water marks (feature pipeline 증분 처리용)
CREATE TABLE IF NOT EXISTS pipeline.water_marks (
  pipeline_name   TEXT        PRIMARY KEY,
  last_ts         TIMESTAMPTZ NOT NULL,
  rows_processed  INTEGER     NOT NULL DEFAULT 0,
  run_count       INTEGER     NOT NULL DEFAULT 0,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
