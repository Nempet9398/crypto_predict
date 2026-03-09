from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app.core.arima_service import (
    DEFAULT_HORIZON_HOURS,
    DEFAULT_LOOKBACK_DAYS,
    load_metadata,
    metadata_data_max_ts,
    parse_order,
    train_arima,
)
from app.core.data_sync import get_data_status, sync_raw_ohlcv_once
from app.core.ohlcv import timeframe_seconds

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("data-updater")
UTC = timezone.utc


def _maybe_retrain_after_sync() -> None:
    if os.getenv("AUTO_RETRAIN_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return

    meta = load_metadata()
    status = get_data_status()
    max_ts = status.get("max_ts")
    if max_ts is None:
        return

    trained_end = metadata_data_max_ts(meta)
    retrain_mode = os.getenv("AUTO_RETRAIN_MODE", "hybrid").lower()
    model_ttl_minutes = int(os.getenv("MODEL_TTL_MINUTES", "180"))
    min_new_points = int(os.getenv("RETRAIN_MIN_NEW_POINTS", "1"))
    if retrain_mode not in {"ttl", "delta", "hybrid"}:
        retrain_mode = "hybrid"

    ttl_expired = False
    if meta is not None:
        trained_at = datetime.fromisoformat(meta["trained_at"].replace("Z", "+00:00"))
        if trained_at.tzinfo is None:
            trained_at = trained_at.replace(tzinfo=UTC)
        ttl_expired = datetime.now(tz=UTC) - trained_at >= timedelta(minutes=model_ttl_minutes)
    else:
        ttl_expired = True

    step_seconds = timeframe_seconds(str(status.get("timeframe") or "15m"))
    new_points = 0
    if trained_end is not None and max_ts > trained_end:
        new_points = int((max_ts - trained_end).total_seconds() // step_seconds)
    has_enough_new = new_points >= min_new_points

    should_retrain = False
    if retrain_mode == "ttl":
        should_retrain = ttl_expired
    elif retrain_mode == "delta":
        should_retrain = has_enough_new
    else:
        should_retrain = ttl_expired and has_enough_new

    if not should_retrain:
        return

    order = parse_order(os.getenv("ARIMA_DEFAULT_ORDER", "2,1,2"))
    lookback_days = int(os.getenv("DEFAULT_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)))
    horizon_hours = int(os.getenv("DEFAULT_HORIZON_HOURS", str(DEFAULT_HORIZON_HOURS)))

    try:
        meta_out = train_arima(
            lookback_days=lookback_days,
            order=order,
            horizon_hours=horizon_hours,
            auto_order=os.getenv("AUTO_ORDER_DEFAULT", "true").lower() in {"1", "true", "yes", "on"},
            model_family=os.getenv("MODEL_FAMILY_DEFAULT", "auto"),
            transform_mode=os.getenv("TRANSFORM_MODE_DEFAULT", "auto"),
            train_reason="auto",
        )
        logger.info("auto-train complete: order=%s points=%s train_end=%s", meta_out["order"], meta_out["data_points"], meta_out["train_end"])
    except Exception as exc:
        logger.warning("auto-train skipped/failed: %s", exc)


def run_forever() -> None:
    interval = int(os.getenv("BACKFILL_INTERVAL_SECONDS", "300"))

    while True:
        try:
            result = sync_raw_ohlcv_once()
            logger.info(
                "sync complete: inserted_rows=%s ranges_filled=%s latest_filled=%s",
                result.inserted_rows,
                result.ranges_filled,
                result.latest_filled,
            )
            _maybe_retrain_after_sync()
        except Exception as exc:
            logger.exception("sync cycle failed: %s", exc)

        time.sleep(interval)


if __name__ == "__main__":
    run_forever()
