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


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    }

    with DAG(
        dag_id="ingestion_dag",
        default_args=default_args,
        description="Ingest ETH/USDT 15m OHLCV from Binance with gap fill",
        schedule_interval="*/5 * * * *",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["ingestion"],
    ) as dag:
        ingest = PythonOperator(
            task_id="ingest_ohlcv",
            python_callable=ingest_task,
        )

        ingest

    return dag


dag = make_dag()
