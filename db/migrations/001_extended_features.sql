-- Migration 001: Extended features table with technical indicators, ML tables, and ensemble signals

-- 1. Extend features.eth_features with new indicator columns
ALTER TABLE features.eth_features
  ADD COLUMN IF NOT EXISTS rsi_14         NUMERIC,
  ADD COLUMN IF NOT EXISTS macd_line      NUMERIC,
  ADD COLUMN IF NOT EXISTS macd_signal    NUMERIC,
  ADD COLUMN IF NOT EXISTS macd_hist      NUMERIC,
  ADD COLUMN IF NOT EXISTS bb_upper       NUMERIC,
  ADD COLUMN IF NOT EXISTS bb_middle      NUMERIC,
  ADD COLUMN IF NOT EXISTS bb_lower       NUMERIC,
  ADD COLUMN IF NOT EXISTS bb_width       NUMERIC,
  ADD COLUMN IF NOT EXISTS bb_pct         NUMERIC,
  ADD COLUMN IF NOT EXISTS atr_14         NUMERIC,
  ADD COLUMN IF NOT EXISTS atr_pct        NUMERIC,
  ADD COLUMN IF NOT EXISTS obv            NUMERIC,
  ADD COLUMN IF NOT EXISTS vwap           NUMERIC,
  ADD COLUMN IF NOT EXISTS stoch_k        NUMERIC,
  ADD COLUMN IF NOT EXISTS stoch_d        NUMERIC,
  ADD COLUMN IF NOT EXISTS vol_regime     TEXT,
  ADD COLUMN IF NOT EXISTS signal_1h      SMALLINT,
  ADD COLUMN IF NOT EXISTS signal_4h      SMALLINT,
  ADD COLUMN IF NOT EXISTS target_direction SMALLINT;

-- 2. ML model registry
CREATE TABLE IF NOT EXISTS features.ml_model_registry (
  model_id        TEXT PRIMARY KEY,
  model_type      TEXT NOT NULL,
  trained_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  feature_cols    JSONB NOT NULL,
  hyperparams     JSONB,
  metrics         JSONB,
  is_active       BOOLEAN NOT NULL DEFAULT FALSE,
  artifact_path   TEXT NOT NULL
);

-- 3. Ensemble signals table
CREATE TABLE IF NOT EXISTS features.ensemble_signals (
  exchange          TEXT NOT NULL,
  symbol            TEXT NOT NULL,
  ts                TIMESTAMPTZ NOT NULL,
  timeframe         TEXT NOT NULL DEFAULT '15m',
  arima_direction   SMALLINT,
  arima_prob_up     NUMERIC,
  arima_prob_down   NUMERIC,
  ml_direction      SMALLINT,
  ml_prob_up        NUMERIC,
  ml_prob_down      NUMERIC,
  mtf_1h            SMALLINT,
  mtf_4h            SMALLINT,
  vol_regime        TEXT,
  ensemble_signal   TEXT,
  score             NUMERIC,
  confidence        NUMERIC,
  atr_14            NUMERIC,
  current_close     NUMERIC,
  tp_price          NUMERIC,
  sl_price          NUMERIC,
  rr_ratio          NUMERIC,
  position_size_pct NUMERIC,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, ts, timeframe)
);
