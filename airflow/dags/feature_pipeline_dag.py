import os
import subprocess
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_DIR = "/opt/airflow/project"


def run_script(script_rel_path):
    env = os.environ.copy()
    script_path = os.path.join(PROJECT_DIR, script_rel_path)
    subprocess.run(["python", script_path], check=True, env=env)


def ingest_task():
    run_script("pipelines/ingestion/binance_ohlcv.py")


def resample_task():
    run_script("pipelines/processing/resample.py")


def feature_task():
    run_script("pipelines/features/technical_indicators.py")


def train_task():
    run_script("ml/train.py")


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    }

    with DAG(
        dag_id="feature_pipeline_dag",
        default_args=default_args,
        description="Process OHLCV, generate features, train model",
        schedule_interval="@hourly",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["features"],
    ) as dag:
        ingest = PythonOperator(
            task_id="ingest_ohlcv",
            python_callable=ingest_task,
        )
        resample = PythonOperator(
            task_id="resample_1h",
            python_callable=resample_task,
        )
        features = PythonOperator(
            task_id="generate_features",
            python_callable=feature_task,
        )
        train = PythonOperator(
            task_id="train_model",
            python_callable=train_task,
        )

        ingest >> resample >> features >> train

    return dag


dag = make_dag()
