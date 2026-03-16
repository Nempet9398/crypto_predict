"""
Data Quality Monitoring DAG — runs every 30 minutes.

Checks:
  1. check_freshness:    raw.eth_ohlcv max(ts) must be within 30 min of NOW()
  2. check_feature_lag: features.eth_features max(ts) must be within 2h of raw max(ts)
  3. check_gap_count:   count missing 15m slots in the last 24h (warn if > 3)
  4. log_quality_summary: structured log of all checks + water_marks status

All tasks are non-blocking: even if a check fails it only logs a warning,
so other DAGs can continue. No SLA breach stops the pipeline.
"""
import os
from datetime import datetime, timedelta, timezone

import psycopg2
from airflow import DAG
from airflow.operators.python import PythonOperator

UTC = timezone.utc

FRESHNESS_WARN_MINUTES = 30     # raw data must be fresher than this
FEATURE_LAG_WARN_HOURS = 2      # features must not lag raw by more than this
GAP_WARN_THRESHOLD = 3          # warn if more than N gaps in last 24h


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
    )


def check_freshness(**context):
    """Check that raw OHLCV data is recent enough."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(ts) AS max_ts,
                       COUNT(*) AS total_rows,
                       NOW() AT TIME ZONE 'UTC' AS now_utc
                FROM raw.eth_ohlcv
                WHERE exchange = %s AND symbol = %s AND timeframe = '15m'
            """, (
                os.getenv("EXCHANGE", "binance"),
                os.getenv("SYMBOL", "ETH/USDT"),
            ))
            row = cur.fetchone()
    finally:
        conn.close()

    max_ts, total_rows, now_utc = row
    if max_ts is None:
        print("[data_quality] WARN: raw.eth_ohlcv is EMPTY — no data ingested yet.")
        context["ti"].xcom_push(key="freshness_ok", value=False)
        context["ti"].xcom_push(key="raw_max_ts", value=None)
        return

    lag_minutes = (now_utc - max_ts).total_seconds() / 60
    freshness_ok = lag_minutes <= FRESHNESS_WARN_MINUTES

    status = "OK" if freshness_ok else "WARN"
    print(
        f"[data_quality] [{status}] raw data freshness: "
        f"max_ts={max_ts.isoformat()}, lag={lag_minutes:.1f}min, "
        f"total_rows={total_rows:,}, threshold={FRESHNESS_WARN_MINUTES}min"
    )
    if not freshness_ok:
        print(f"[data_quality] WARN: Raw data is stale! Last candle was {lag_minutes:.1f} min ago.")

    context["ti"].xcom_push(key="freshness_ok", value=freshness_ok)
    context["ti"].xcom_push(key="raw_max_ts", value=max_ts.isoformat() if max_ts else None)
    context["ti"].xcom_push(key="raw_lag_minutes", value=round(lag_minutes, 1))


def check_feature_lag(**context):
    """Check that features are not lagging too far behind raw data."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    (SELECT MAX(ts) FROM raw.eth_ohlcv
                     WHERE exchange = %s AND symbol = %s AND timeframe = '15m') AS raw_max,
                    (SELECT MAX(ts) FROM features.eth_features
                     WHERE exchange = %s AND symbol = %s) AS feat_max,
                    (SELECT last_ts FROM pipeline.water_marks
                     WHERE pipeline_name = 'feature_pipeline') AS hwm_ts
            """, (
                os.getenv("EXCHANGE", "binance"),
                os.getenv("SYMBOL", "ETH/USDT"),
                os.getenv("EXCHANGE", "binance"),
                os.getenv("SYMBOL", "ETH/USDT"),
            ))
            row = cur.fetchone()
    finally:
        conn.close()

    raw_max, feat_max, hwm_ts = row

    if raw_max is None:
        print("[data_quality] WARN: No raw data found.")
        context["ti"].xcom_push(key="feature_lag_ok", value=False)
        return

    if feat_max is None:
        print("[data_quality] WARN: features.eth_features is EMPTY — run technical_indicators.py first.")
        context["ti"].xcom_push(key="feature_lag_ok", value=False)
        return

    lag_seconds = (raw_max - feat_max).total_seconds()
    lag_hours = lag_seconds / 3600
    lag_ok = lag_hours <= FEATURE_LAG_WARN_HOURS

    status = "OK" if lag_ok else "WARN"
    print(
        f"[data_quality] [{status}] feature lag: "
        f"raw_max={raw_max.isoformat()}, feat_max={feat_max.isoformat()}, "
        f"lag={lag_hours:.2f}h, hwm={hwm_ts}, threshold={FEATURE_LAG_WARN_HOURS}h"
    )
    if not lag_ok:
        print(f"[data_quality] WARN: Feature pipeline is {lag_hours:.1f}h behind raw data!")

    context["ti"].xcom_push(key="feature_lag_ok", value=lag_ok)
    context["ti"].xcom_push(key="feature_lag_hours", value=round(lag_hours, 2))


def check_gap_count(**context):
    """Count missing 15m timestamps in the last 24 hours."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH window_bounds AS (
                    SELECT
                        DATE_TRUNC('hour', NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours' AS start_ts,
                        DATE_TRUNC('hour', NOW() AT TIME ZONE 'UTC') AS end_ts
                ),
                expected AS (
                    SELECT generate_series(start_ts, end_ts, INTERVAL '15 minutes') AS ts
                    FROM window_bounds
                ),
                present AS (
                    SELECT ts FROM raw.eth_ohlcv
                    WHERE exchange = %s AND symbol = %s AND timeframe = '15m'
                      AND ts >= (SELECT start_ts FROM window_bounds)
                      AND ts <= (SELECT end_ts FROM window_bounds)
                )
                SELECT COUNT(*) AS gap_count
                FROM expected e
                LEFT JOIN present p ON e.ts = p.ts
                WHERE p.ts IS NULL
            """, (
                os.getenv("EXCHANGE", "binance"),
                os.getenv("SYMBOL", "ETH/USDT"),
            ))
            gap_count = cur.fetchone()[0]
    finally:
        conn.close()

    gap_ok = gap_count <= GAP_WARN_THRESHOLD
    status = "OK" if gap_ok else "WARN"
    print(
        f"[data_quality] [{status}] gap check (last 24h): "
        f"missing_slots={gap_count}, threshold={GAP_WARN_THRESHOLD}"
    )
    if not gap_ok:
        print(f"[data_quality] WARN: {gap_count} missing 15m candles in last 24h. "
              f"ingestion_dag should backfill automatically.")

    context["ti"].xcom_push(key="gap_ok", value=gap_ok)
    context["ti"].xcom_push(key="gap_count", value=int(gap_count))


def log_quality_summary(**context):
    """Aggregate results from all quality checks and log a structured summary."""
    ti = context["ti"]

    freshness_ok = ti.xcom_pull(task_ids="check_freshness", key="freshness_ok")
    raw_max_ts = ti.xcom_pull(task_ids="check_freshness", key="raw_max_ts")
    raw_lag_min = ti.xcom_pull(task_ids="check_freshness", key="raw_lag_minutes")

    feature_lag_ok = ti.xcom_pull(task_ids="check_feature_lag", key="feature_lag_ok")
    feature_lag_h = ti.xcom_pull(task_ids="check_feature_lag", key="feature_lag_hours")

    gap_ok = ti.xcom_pull(task_ids="check_gap_count", key="gap_ok")
    gap_count = ti.xcom_pull(task_ids="check_gap_count", key="gap_count")

    all_ok = all(x is not False for x in [freshness_ok, feature_lag_ok, gap_ok])
    overall = "HEALTHY" if all_ok else "DEGRADED"

    print("=" * 60)
    print(f"[data_quality] SUMMARY — {datetime.now(tz=UTC).isoformat()}")
    print(f"  Overall status : {overall}")
    print(f"  Freshness      : {'OK' if freshness_ok else 'WARN'} "
          f"(raw_max={raw_max_ts}, lag={raw_lag_min}min)")
    print(f"  Feature lag    : {'OK' if feature_lag_ok else 'WARN'} "
          f"(lag={feature_lag_h}h)")
    print(f"  Gap count (24h): {'OK' if gap_ok else 'WARN'} "
          f"(gaps={gap_count})")
    print("=" * 60)


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    }

    with DAG(
        dag_id="data_quality_dag",
        default_args=default_args,
        description="Monitor data freshness, feature lag, and gap count every 30 minutes",
        schedule_interval="*/30 * * * *",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["monitoring", "quality"],
    ) as dag:
        freshness = PythonOperator(
            task_id="check_freshness",
            python_callable=check_freshness,
        )
        feat_lag = PythonOperator(
            task_id="check_feature_lag",
            python_callable=check_feature_lag,
        )
        gaps = PythonOperator(
            task_id="check_gap_count",
            python_callable=check_gap_count,
        )
        summary = PythonOperator(
            task_id="log_quality_summary",
            python_callable=log_quality_summary,
        )

        # All checks run in parallel, then summary aggregates
        [freshness, feat_lag, gaps] >> summary

    return dag


dag = make_dag()
