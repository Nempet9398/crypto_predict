-- Migration 002: pipeline water_marks + prediction actualization columns
-- Purpose: Enable High Water Mark incremental processing + backfill actual returns on predictions

CREATE SCHEMA IF NOT EXISTS pipeline;

-- High Water Mark table: tracks last processed timestamp per pipeline stage
-- Enables incremental processing (only process new rows since last run)
CREATE TABLE IF NOT EXISTS pipeline.water_marks (
    pipeline_name  TEXT        PRIMARY KEY,
    last_ts        TIMESTAMPTZ NOT NULL,
    rows_processed INT         NOT NULL DEFAULT 0,
    run_count      INT         NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE pipeline.water_marks IS
    'Tracks last successfully processed timestamp for each pipeline stage. '
    'Used for incremental (High Water Mark) processing to avoid full table scans.';

-- Add actual result columns to ensemble_signals
-- These are filled in by prediction_actualize_dag after the horizon has passed
ALTER TABLE features.ensemble_signals
    ADD COLUMN IF NOT EXISTS actual_return    NUMERIC,
    ADD COLUMN IF NOT EXISTS actual_direction SMALLINT,
    ADD COLUMN IF NOT EXISTS actualized_at    TIMESTAMPTZ;

COMMENT ON COLUMN features.ensemble_signals.actual_return    IS 'Actual return realized after signal_ts + horizon. NULL until actualized.';
COMMENT ON COLUMN features.ensemble_signals.actual_direction IS '+1 (>0.3%), -1 (<-0.3%), 0 otherwise. NULL until actualized.';
COMMENT ON COLUMN features.ensemble_signals.actualized_at   IS 'Timestamp when actual_return was filled in by actualize pipeline.';

-- Add ML regression target columns to eth_features
-- target_return_1h/3h/6h: forward returns for ML regression training
ALTER TABLE features.eth_features
    ADD COLUMN IF NOT EXISTS target_return_1h  NUMERIC,
    ADD COLUMN IF NOT EXISTS target_return_3h  NUMERIC,
    ADD COLUMN IF NOT EXISTS target_return_6h  NUMERIC;

COMMENT ON COLUMN features.eth_features.target_return_1h IS '(close(t+4)  - close(t)) / close(t), 1h forward return for ML regression.';
COMMENT ON COLUMN features.eth_features.target_return_3h IS '(close(t+12) - close(t)) / close(t), 3h forward return for ML regression.';
COMMENT ON COLUMN features.eth_features.target_return_6h IS '(close(t+24) - close(t)) / close(t), 6h forward return for ML regression.';

-- Add ML regressor model type support to ml_model_registry
-- model_type examples: 'xgb_regressor_1h', 'lgb_regressor_3h', 'xgb_classifier', etc.
-- No schema change needed — model_type TEXT already covers this.

-- Index for actualize query performance (un-actualized rows in features.signals)
CREATE INDEX IF NOT EXISTS idx_signals_unactualized
    ON features.signals (exchange, symbol, ts)
    WHERE actual_return IS NULL;

-- Index for water_marks fast lookup
CREATE INDEX IF NOT EXISTS idx_water_marks_updated
    ON pipeline.water_marks (updated_at);
