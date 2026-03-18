-- Migration 003: ensemble_config table for UI-configurable ensemble weights
-- Allows real-time adjustment of signal weights via PUT /ensemble/config API

CREATE TABLE IF NOT EXISTS features.ensemble_config (
    key         TEXT        PRIMARY KEY,
    value       NUMERIC     NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE features.ensemble_config IS
    'UI-configurable ensemble weights and thresholds. '
    'Loaded by signal_service.py at runtime. '
    'Updated via PUT /ensemble/config API endpoint.';

-- Seed default values
INSERT INTO features.ensemble_config (key, value) VALUES
    ('w_tech',              0.35),
    ('w_ml_regressor',      0.30),
    ('w_ml_classifier',     0.15),
    ('w_mtf_1h',            0.13),
    ('w_mtf_4h',            0.07),
    ('threshold_normal',    0.08),
    ('threshold_high_vol',  0.12),
    ('min_ml_prob_normal',  0.45),
    ('min_ml_prob_high_vol',0.52)
ON CONFLICT (key) DO NOTHING;

COMMENT ON COLUMN features.ensemble_config.key IS
    'Config key: w_tech | w_ml_regressor | w_ml_classifier | '
    'w_mtf_1h | w_mtf_4h | threshold_normal | threshold_high_vol | '
    'min_ml_prob_normal | min_ml_prob_high_vol';
COMMENT ON COLUMN features.ensemble_config.value IS 'Numeric value for the config key';
COMMENT ON COLUMN features.ensemble_config.updated_at IS 'Last updated timestamp';
