from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from app.core.data_sync import get_data_status, sync_raw_ohlcv_once
from app.core.ohlcv import timeframe_seconds

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("data-updater")
UTC = timezone.utc

_last_retrain_at: datetime | None = None


def _load_ml_trained_at() -> datetime | None:
    import json
    meta_path = os.path.join(
        os.getenv("MODEL_REGISTRY_DIR", "/app/model_registry"),
        "eth_predictor_meta.json",
    )
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        ts = meta.get("trained_at")
        if ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        pass
    return None


def _run_ml_train() -> bool:
    ml_dir = os.path.join(os.path.dirname(__file__), "../../../ml")
    lookback = os.getenv("DEFAULT_LOOKBACK_DAYS", "60")
    horizon = os.getenv("DEFAULT_HORIZON_BARS", "4")
    try:
        result = subprocess.run(
            [sys.executable, "train_ml_predictor.py",
             f"--lookback-days={lookback}",
             f"--horizon-bars={horizon}"],
            capture_output=True, text=True,
            cwd=os.path.abspath(ml_dir),
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("ML auto-retrain complete")
            return True
        else:
            logger.warning("ML retrain failed: %s", result.stderr[-500:])
            return False
    except Exception as exc:
        logger.warning("ML retrain error: %s", exc)
        return False


def _maybe_retrain() -> None:
    global _last_retrain_at

    if os.getenv("AUTO_RETRAIN_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return

    model_ttl_minutes = int(os.getenv("MODEL_TTL_MINUTES", "240"))
    now = datetime.now(tz=UTC)

    # 마지막 재학습 시점 결정 (메모리 > 파일)
    trained_at = _last_retrain_at or _load_ml_trained_at()

    if trained_at is None:
        logger.info("ML model not found — starting initial training")
        if _run_ml_train():
            _last_retrain_at = now
        return

    if now - trained_at >= timedelta(minutes=model_ttl_minutes):
        logger.info("ML model TTL expired (%dm) — retraining", model_ttl_minutes)
        if _run_ml_train():
            _last_retrain_at = now


def run_forever() -> None:
    interval = int(os.getenv("BACKFILL_INTERVAL_SECONDS", "60"))

    while True:
        try:
            result = sync_raw_ohlcv_once()
            logger.info(
                "sync: inserted=%s ranges_filled=%s latest_filled=%s",
                result.inserted_rows,
                result.ranges_filled,
                result.latest_filled,
            )
            _maybe_retrain()
        except Exception as exc:
            logger.exception("sync cycle failed: %s", exc)

        time.sleep(interval)


if __name__ == "__main__":
    run_forever()
