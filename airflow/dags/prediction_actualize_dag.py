"""
Prediction Actualize DAG — runs hourly.

Problem: features.signals stores trading signals but actual_return stays NULL forever.
         Without actualization, we can't measure prediction accuracy or run realistic backtests.

Solution: For each past signal where:
  - actual_return IS NULL
  - ts + max_horizon < NOW() (enough time has passed)

Look up the actual price at ts + horizon and compute:
  - actual_return    = (close_at_horizon - close_at_signal) / close_at_signal
  - actual_direction = +1 (>0.3%), -1 (<-0.3%), 0 otherwise
  - actualized_at    = NOW()
"""
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from airflow import DAG
from airflow.operators.python import PythonOperator

UTC = timezone.utc

ACTUALIZE_BUFFER_HOURS = 1
DIRECTION_THRESHOLD = 0.003  # 0.3%

# Default horizon: 1h (4 x 15m bars)
DEFAULT_HORIZON_HOURS = 1

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
        cutoff_ts = datetime.now(tz=UTC) - timedelta(hours=DEFAULT_HORIZON_HOURS + ACTUALIZE_BUFFER_HOURS)

        # Fetch un-actualized signals from features.signals (composite PK: exchange, symbol, ts, timeframe)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT exchange, symbol, ts, timeframe
                FROM features.signals
                WHERE actual_return IS NULL
                  AND ts < %s
                ORDER BY ts ASC
                LIMIT %s
            """, (cutoff_ts, BATCH_SIZE))
            pending = cur.fetchall()

        if not pending:
            print("[actualize] No pending signals to actualize.")
            context["ti"].xcom_push(key="actualized_count", value=0)
            return

        print(f"[actualize] Found {len(pending)} signals to actualize.")

        signal_data = []
        needed_ts = set()
        for exchange, symbol, signal_ts, timeframe in pending:
            actual_ts = signal_ts + timedelta(hours=DEFAULT_HORIZON_HOURS)
            needed_ts.add((exchange, symbol, signal_ts))
            needed_ts.add((exchange, symbol, actual_ts))
            signal_data.append({
                "exchange": exchange,
                "symbol": symbol,
                "signal_ts": signal_ts,
                "timeframe": timeframe,
                "actual_ts": actual_ts,
            })

        # Batch fetch all needed close prices
        exchange_sym_pairs = list({(r["exchange"], r["symbol"]) for r in signal_data})
        all_ts = [ts for (_, _, ts) in needed_ts]

        prices: dict = {}
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
                sig["exchange"],
                sig["symbol"],
                sig["signal_ts"],
                sig["timeframe"],
            ))

        if updates:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    UPDATE features.signals AS s
                    SET actual_return    = data.actual_return,
                        actual_direction = data.actual_direction,
                        actualized_at    = data.actualized_at
                    FROM (VALUES %s) AS data(actual_return, actual_direction, actualized_at,
                                            exchange, symbol, ts, timeframe)
                    WHERE s.exchange  = data.exchange
                      AND s.symbol    = data.symbol
                      AND s.ts        = data.ts
                      AND s.timeframe = data.timeframe
                    """,
                    updates,
                    template="(%s::NUMERIC, %s::SMALLINT, %s::TIMESTAMPTZ, %s, %s, %s::TIMESTAMPTZ, %s)",
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
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE actual_return IS NOT NULL) AS actualized_total,
                    COUNT(*) FILTER (WHERE actual_return IS NULL) AS pending_total,
                    AVG(ABS(actual_return)) FILTER (WHERE actual_return IS NOT NULL) AS mean_abs_return,
                    AVG(
                        CASE
                          WHEN (signal IN ('long','strong-long')  AND actual_direction = 1)  THEN 1.0
                          WHEN (signal IN ('short','strong-short') AND actual_direction = -1) THEN 1.0
                          WHEN (signal = 'neutral' AND actual_direction = 0)                  THEN 1.0
                          ELSE 0.0
                        END
                    ) FILTER (WHERE actual_return IS NOT NULL) AS dir_accuracy
                FROM features.signals
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
            provide_context=True,
        )
        stats = PythonOperator(
            task_id="log_actualization_stats",
            python_callable=log_actualization_stats,
            provide_context=True,
        )

        actualize >> stats

    return dag


dag = make_dag()
