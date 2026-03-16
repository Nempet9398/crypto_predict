"""
Prediction Actualize DAG — runs hourly.

Problem: features.ensemble_signals stores trading signals but actual_return stays NULL forever.
         Without actualization, we can't measure prediction accuracy or run realistic backtests.

Solution: For each past signal where:
  - actual_return IS NULL
  - signal_ts + max_horizon < NOW() (enough time has passed)

Look up the actual price at signal_ts + horizon and compute:
  - actual_return    = (close_at_horizon - close_at_signal) / close_at_signal
  - actual_direction = +1 (>0.3%), -1 (<-0.3%), 0 otherwise
  - actualized_at    = NOW()

After actualization, ensemble_signals becomes a self-contained backtest dataset:
  - signal vs actual_direction → directional accuracy
  - predicted_return (from ML regressor) vs actual_return → regression MAE/IC
"""
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from airflow import DAG
from airflow.operators.python import PythonOperator

UTC = timezone.utc

# How far past the horizon before we try to actualize
# (extra buffer so the candle is definitely in the DB)
ACTUALIZE_BUFFER_HOURS = 1

# Direction threshold (matches target_direction logic)
DIRECTION_THRESHOLD = 0.003  # 0.3%

# Horizon mapping: timeframe column value → hours
HORIZON_HOURS = {
    "1h": 1,
    "3h": 3,
    "6h": 6,
    "4h": 4,
}
DEFAULT_HORIZON_HOURS = 1

# Max rows to process per run (avoid long-running tasks)
BATCH_SIZE = 500


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
    )


def actualize_predictions(**context):
    """Fill actual_return and actual_direction for signals whose horizon has passed."""
    conn = _get_conn()
    try:
        # Max horizon we care about (6h + buffer = 7h)
        max_horizon_h = max(HORIZON_HOURS.values()) + ACTUALIZE_BUFFER_HOURS
        cutoff_ts = datetime.now(tz=UTC) - timedelta(hours=max_horizon_h)

        # Fetch un-actualized signals that are old enough
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, signal_ts, timeframe, exchange, symbol
                FROM features.ensemble_signals
                WHERE actual_return IS NULL
                  AND signal_ts < %s
                ORDER BY signal_ts ASC
                LIMIT %s
            """, (cutoff_ts, BATCH_SIZE))
            pending = cur.fetchall()

        if not pending:
            print("[actualize] No pending predictions to actualize.")
            context["ti"].xcom_push(key="actualized_count", value=0)
            return

        print(f"[actualize] Found {len(pending)} signals to actualize.")

        # Collect all unique (exchange, symbol, ts) we need to look up
        # We need: entry price at signal_ts and actual price at signal_ts + horizon
        needed_ts = set()
        signal_data = []
        for row_id, signal_ts, timeframe, exchange, symbol in pending:
            horizon_h = HORIZON_HOURS.get(timeframe, DEFAULT_HORIZON_HOURS)
            actual_ts = signal_ts + timedelta(hours=horizon_h)
            needed_ts.add((exchange, symbol, signal_ts))
            needed_ts.add((exchange, symbol, actual_ts))
            signal_data.append({
                "id": row_id,
                "signal_ts": signal_ts,
                "timeframe": timeframe,
                "exchange": exchange,
                "symbol": symbol,
                "horizon_h": horizon_h,
                "actual_ts": actual_ts,
            })

        # Build lookup of (exchange, symbol, ts) → close price
        # Batch fetch all needed timestamps in one query
        exchange_sym_pairs = list({(r["exchange"], r["symbol"]) for r in signal_data})
        all_ts = [ts for (_, _, ts) in needed_ts]

        prices: dict[tuple, float] = {}
        if all_ts:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT exchange, symbol, ts, close::FLOAT
                    FROM raw.eth_ohlcv
                    WHERE (exchange, symbol) = ANY(%s)
                      AND ts = ANY(%s)
                """, (
                    [(e, s) for e, s in exchange_sym_pairs],
                    all_ts,
                ))
                for exch, sym, ts, close in cur.fetchall():
                    prices[(exch, sym, ts)] = close

        # Compute actual returns
        updates = []
        skipped = 0
        for sig in signal_data:
            entry_key = (sig["exchange"], sig["symbol"], sig["signal_ts"])
            actual_key = (sig["exchange"], sig["symbol"], sig["actual_ts"])

            entry_close = prices.get(entry_key)
            actual_close = prices.get(actual_key)

            if entry_close is None or actual_close is None or entry_close == 0:
                skipped += 1
                continue

            actual_return = (actual_close - entry_close) / entry_close
            if actual_return > DIRECTION_THRESHOLD:
                actual_direction = 1
            elif actual_return < -DIRECTION_THRESHOLD:
                actual_direction = -1
            else:
                actual_direction = 0

            updates.append((
                float(actual_return),
                int(actual_direction),
                datetime.now(tz=UTC),
                sig["id"],
            ))

        if updates:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    UPDATE features.ensemble_signals AS es
                    SET actual_return    = data.actual_return,
                        actual_direction = data.actual_direction,
                        actualized_at    = data.actualized_at
                    FROM (VALUES %s) AS data(actual_return, actual_direction, actualized_at, id)
                    WHERE es.id = data.id
                    """,
                    updates,
                    template="(%s::NUMERIC, %s::SMALLINT, %s::TIMESTAMPTZ, %s::BIGINT)",
                )
            conn.commit()

        print(
            f"[actualize] Done: actualized={len(updates)}, "
            f"skipped_no_price={skipped}, pending_total={len(pending)}"
        )
        context["ti"].xcom_push(key="actualized_count", value=len(updates))
        context["ti"].xcom_push(key="skipped_count", value=skipped)

    finally:
        conn.close()


def log_actualization_stats(**context):
    """Log summary of actualization + current accuracy stats."""
    ti = context["ti"]
    actualized = ti.xcom_pull(task_ids="actualize_predictions", key="actualized_count") or 0
    skipped = ti.xcom_pull(task_ids="actualize_predictions", key="skipped_count") or 0

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Overall accuracy stats
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE actual_return IS NOT NULL) AS actualized_total,
                    COUNT(*) FILTER (WHERE actual_return IS NULL) AS pending_total,
                    AVG(ABS(actual_return)) FILTER (WHERE actual_return IS NOT NULL) AS mean_abs_return,
                    AVG(
                        CASE WHEN signal = actual_direction::TEXT THEN 1.0 ELSE 0.0 END
                    ) FILTER (WHERE actual_return IS NOT NULL) AS dir_accuracy
                FROM features.ensemble_signals
            """)
            row = cur.fetchone()
    finally:
        conn.close()

    actualized_total, pending_total, mean_abs_ret, dir_accuracy = row or (0, 0, None, None)

    print("=" * 60)
    print(f"[actualize] Stats — {datetime.now(tz=UTC).isoformat()}")
    print(f"  This run  : actualized={actualized}, skipped={skipped}")
    print(f"  All-time  : actualized={actualized_total}, pending={pending_total}")
    if dir_accuracy is not None:
        print(f"  Dir accuracy (all-time): {dir_accuracy:.3f} ({dir_accuracy*100:.1f}%)")
    if mean_abs_ret is not None:
        print(f"  Mean |actual_return|: {float(mean_abs_ret)*100:.3f}%")
    print("=" * 60)


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    }

    with DAG(
        dag_id="prediction_actualize_dag",
        default_args=default_args,
        description=(
            "Hourly: fill actual_return / actual_direction for signals whose horizon has passed. "
            "Enables automated backtest data accumulation."
        ),
        schedule_interval="@hourly",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["ml", "actualize", "backtest"],
    ) as dag:
        actualize = PythonOperator(
            task_id="actualize_predictions",
            python_callable=actualize_predictions,
        )
        stats = PythonOperator(
            task_id="log_actualization_stats",
            python_callable=log_actualization_stats,
        )

        actualize >> stats

    return dag


dag = make_dag()
