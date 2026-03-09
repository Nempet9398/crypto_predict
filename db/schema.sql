CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS processed;
CREATE SCHEMA IF NOT EXISTS features;

CREATE TABLE IF NOT EXISTS raw.eth_ohlcv (
  exchange TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  open NUMERIC NOT NULL,
  high NUMERIC NOT NULL,
  low NUMERIC NOT NULL,
  close NUMERIC NOT NULL,
  volume NUMERIC NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, timeframe, ts)
);
 
CREATE TABLE IF NOT EXISTS processed.eth_ohlcv_1h (
  exchange TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  open NUMERIC NOT NULL,
  high NUMERIC NOT NULL,
  low NUMERIC NOT NULL,
  close NUMERIC NOT NULL,
  volume NUMERIC NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, ts)
);

CREATE TABLE IF NOT EXISTS features.eth_features (
  exchange TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  close NUMERIC NOT NULL,
  returns_1h NUMERIC NOT NULL,
  sma_5 NUMERIC NOT NULL,
  sma_10 NUMERIC NOT NULL,
  ema_10 NUMERIC NOT NULL,
  volatility_10 NUMERIC NOT NULL,
  feature_created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (exchange, symbol, ts)
);
