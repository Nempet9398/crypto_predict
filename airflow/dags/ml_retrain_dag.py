"""
Separate DAG for ML classifier retraining (heavier, runs every 4 hours).
This decouples the expensive ML training from the hourly feature pipeline.
"""
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


def train_ml_task():
    run_script("ml/train_ml_classifier.py")


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    }

    with DAG(
        dag_id="ml_retrain_dag",
        default_args=default_args,
        description="Retrain XGBoost/LightGBM classifier every 4 hours",
        schedule_interval="0 */4 * * *",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["ml", "retrain"],
    ) as dag:
        train_ml = PythonOperator(
            task_id="train_ml_classifier",
            python_callable=train_ml_task,
        )
        train_ml

    return dag


dag = make_dag()
