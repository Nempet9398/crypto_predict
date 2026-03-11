import os
import subprocess
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_DIR = "/opt/airflow/project"


def run_script(script_rel_path, extra_args=None):
    env = os.environ.copy()
    script_path = os.path.join(PROJECT_DIR, script_rel_path)
    cmd = ["python", script_path]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd, check=True, env=env)


def ingest_task():
    run_script("pipelines/ingestion/binance_ohlcv.py")


def resample_task():
    run_script("pipelines/processing/resample.py")


def feature_task():
    run_script("pipelines/features/technical_indicators.py")


def train_arima_task():
    run_script("ml/train_arima.py")


def train_ml_task():
    """Train XGBoost/LightGBM classifier. Skipped gracefully if packages not installed."""
    try:
        run_script("ml/train_ml_classifier.py")
    except subprocess.CalledProcessError:
        pass  # Non-fatal: ensemble falls back to ARIMA-only if ML unavailable


def ensemble_signal_task():
    """Pre-compute ensemble signals for the last 6 hours into features.ensemble_signals."""
    run_script("ml/compute_ensemble_signals.py", extra_args=["--hours", "6"])


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    }

    with DAG(
        dag_id="feature_pipeline_dag",
        default_args=default_args,
        description="Process OHLCV, generate extended features, train ML + ARIMA, compute ensemble signals",
        schedule_interval="@hourly",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["features", "ml", "ensemble"],
    ) as dag:
        ingest = PythonOperator(task_id="ingest_ohlcv", python_callable=ingest_task)
        resample = PythonOperator(task_id="resample_1h", python_callable=resample_task)
        features = PythonOperator(task_id="generate_features", python_callable=feature_task)
        train_ml = PythonOperator(task_id="train_ml_classifier", python_callable=train_ml_task)
        train_arima = PythonOperator(task_id="train_arima_model", python_callable=train_arima_task)
        ensemble = PythonOperator(task_id="compute_ensemble_signals", python_callable=ensemble_signal_task)

        # ML retraining is expensive — runs every hour but is skipped gracefully if needed
        # Chain: ingest → resample → features → [train_ml, train_arima] → ensemble
        ingest >> resample >> features >> [train_ml, train_arima] >> ensemble

    return dag


dag = make_dag()
