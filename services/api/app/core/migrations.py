"""
DB 자동 마이그레이션 — API 서버 시작 시 실행.
테이블/컬럼 누락 시 추가 (idempotent).
"""
import logging

from app.core.db import db_conn_cursor

logger = logging.getLogger(__name__)

_MIGRATIONS = [
    # features.eth_features 새 컬럼들
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS open NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS high NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS low NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS volume NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS returns_1bar NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS returns_4bar NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS returns_16bar NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS sma_20 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS ema_20 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS ema_50 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS ema_200 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS volume_sma_20 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS volume_ratio NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS volatility_10 NUMERIC",
    # 기존 호환
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_14 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_line NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_signal NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_hist NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_upper NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_middle NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_lower NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_width NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_pct NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS atr_14 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS atr_pct NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS obv NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS vwap NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS stoch_k NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS stoch_d NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS vol_regime TEXT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS signal_1h SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS signal_4h SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS target_direction SMALLINT",
    # features.signals 테이블 (새 스키마, ARIMA 없음)
    """
    CREATE TABLE IF NOT EXISTS features.signals (
        exchange          TEXT        NOT NULL,
        symbol            TEXT        NOT NULL,
        ts                TIMESTAMPTZ NOT NULL,
        timeframe         TEXT        NOT NULL DEFAULT '15m',
        signal            TEXT        NOT NULL,
        score             NUMERIC,
        confidence        NUMERIC,
        tech_score        NUMERIC,
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
    )
    """,
    # features.ml_predictions 테이블
    """
    CREATE TABLE IF NOT EXISTS features.ml_predictions (
        exchange          TEXT        NOT NULL,
        symbol            TEXT        NOT NULL,
        ts                TIMESTAMPTZ NOT NULL,
        horizon_bars      INTEGER     NOT NULL,
        pred_return       NUMERIC,
        pred_close        NUMERIC,
        pred_direction    SMALLINT,
        pred_confidence   NUMERIC,
        actual_return     NUMERIC,
        actual_close      NUMERIC,
        actual_direction  SMALLINT,
        model_id          TEXT,
        computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (exchange, symbol, ts, horizon_bars)
    )
    """,
    # 인덱스
    "CREATE INDEX IF NOT EXISTS idx_signals_ts ON features.signals (exchange, symbol, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ml_preds_ts ON features.ml_predictions (exchange, symbol, ts DESC)",
    # features.ensemble_config: UI-configurable ensemble weights
    """
    CREATE TABLE IF NOT EXISTS features.ensemble_config (
        key         TEXT        PRIMARY KEY,
        value       NUMERIC     NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Seed default ensemble config values (idempotent)
    "INSERT INTO features.ensemble_config (key, value) VALUES ('w_tech', 0.35) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('w_ml_regressor', 0.30) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('w_ml_classifier', 0.15) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('w_mtf_1h', 0.13) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('w_mtf_4h', 0.07) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('threshold_normal', 0.08) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('threshold_high_vol', 0.12) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('min_ml_prob_normal', 0.45) ON CONFLICT (key) DO NOTHING",
    "INSERT INTO features.ensemble_config (key, value) VALUES ('min_ml_prob_high_vol', 0.52) ON CONFLICT (key) DO NOTHING",
    # ML model registry
    """
    CREATE TABLE IF NOT EXISTS features.ml_model_registry (
        model_id        TEXT PRIMARY KEY,
        model_type      TEXT NOT NULL,
        trained_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        feature_cols    JSONB NOT NULL,
        hyperparams     JSONB,
        metrics         JSONB,
        is_active       BOOLEAN NOT NULL DEFAULT FALSE,
        artifact_path   TEXT NOT NULL
    )
    """,
    # target_return 컬럼 (회귀 모델용)
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS target_return_1h NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS target_return_3h NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS target_return_6h NUMERIC",
    # Migration 004: 피처 정교화 v2 (원칙 1,2,3)
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS adx NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_prev NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_hist_prev NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS atr_pct_prev NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_pct_prev NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_golden_cross SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_dead_cross SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS stoch_golden_cross SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS stoch_dead_cross SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_cross_30_up SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_cross_70_down SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS volume_above_avg SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS ma_regular_arrangement SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS market_regime_detail TEXT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS shock_detected SMALLINT",
    # Migration 005: 심리적 지지/저항선 피처
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS dist_to_round_100 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS dist_to_round_500 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS dist_to_round_1000 NUMERIC",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS near_round_100 SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS near_round_500 SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS near_round_1000 SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS broke_round_100_up SMALLINT",
    "ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS broke_round_100_down SMALLINT",
]


def run_migrations():
    try:
        with db_conn_cursor(dict_cursor=False) as (conn, cur):
            for sql in _MIGRATIONS:
                try:
                    cur.execute(sql)
                except Exception as exc:
                    logger.warning("Migration step skipped: %s", exc)
            conn.commit()
        logger.info("DB migrations applied successfully")
    except Exception as exc:
        logger.error("DB migration failed: %s", exc)
