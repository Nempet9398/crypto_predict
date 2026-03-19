"""
Separate DAG for ML retraining (heavier, runs every 4 hours).
Trains both the classifier and the quantile regression models.
Includes a quality gate: only promotes new regression models if cv_dir_acc > 0.50.
"""
import os
import subprocess
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_DIR = "/opt/airflow/project"
MIN_DIR_ACCURACY = 0.50


def get_env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def run_script(script_rel_path):
    env = os.environ.copy()
    script_path = os.path.join(PROJECT_DIR, script_rel_path)
    subprocess.run(["python", script_path], check=True, env=env)


def train_ml_classifier_task():
    """Train XGBoost/LightGBM classifier (direction prediction)."""
    try:
        run_script("ml/train_ml_predictor.py")
    except subprocess.CalledProcessError:
        pass  # Non-fatal


def train_ml_regressor_task():
    """Train XGBoost/LightGBM quantile regression (return prediction at 1h/3h/6h)."""
    try:
        run_script("ml/train_ml_regressor.py")
    except subprocess.CalledProcessError:
        pass  # Non-fatal


def validate_model_quality(**context):
    """
    Check newly trained (inactive) regressor models against quality gate.
    Pushes results to XCom for the promote task.
    """
    import psycopg2

    conn = psycopg2.connect(
        host=get_env("DB_HOST"),
        port=int(get_env("DB_PORT", "5432")),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD"),
        dbname=get_env("DB_NAME"),
    )
    try:
        sql = """
            SELECT model_id, model_type,
                   (metrics->>'cv_dir_acc')::FLOAT AS dir_acc
            FROM features.ml_model_registry
            WHERE is_active = FALSE
              AND trained_at >= NOW() - INTERVAL '30 minutes'
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()

    passed = []
    failed = []
    for model_id, model_type, dir_acc in (rows or []):
        if dir_acc is not None and dir_acc > MIN_DIR_ACCURACY:
            passed.append({"model_id": model_id, "model_type": model_type, "dir_acc": dir_acc})
            print(f"[quality_gate] PASS model_id={model_id} {model_type} dir_acc={dir_acc:.3f}")
        else:
            failed.append({"model_id": model_id, "model_type": model_type, "dir_acc": dir_acc})
            print(f"[quality_gate] FAIL model_id={model_id} {model_type} dir_acc={dir_acc}")

    context["ti"].xcom_push(key="passed_models", value=passed)
    print(f"[quality_gate] {len(passed)} passed, {len(failed)} failed")


def promote_if_quality_ok(**context):
    """
    Promote models that passed the quality gate.
    Failed models keep existing active model untouched.
    """
    import psycopg2

    passed = context["ti"].xcom_pull(key="passed_models", task_ids="validate_model_quality") or []
    if not passed:
        print("[promote] No models passed quality gate. Keeping existing active models.")
        return

    conn = psycopg2.connect(
        host=get_env("DB_HOST"),
        port=int(get_env("DB_PORT", "5432")),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD"),
        dbname=get_env("DB_NAME"),
    )
    try:
        for item in passed:
            model_id = item["model_id"]
            model_type = item["model_type"]
            dir_acc = item["dir_acc"]
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE features.ml_model_registry SET is_active = FALSE WHERE model_type = %s",
                    (model_type,)
                )
                cur.execute(
                    "UPDATE features.ml_model_registry SET is_active = TRUE WHERE model_id = %s",
                    (model_id,)
                )
            conn.commit()
            print(f"[promote] Promoted model_id={model_id} ({model_type}) dir_acc={dir_acc:.3f}")
    finally:
        conn.close()


def make_dag():
    default_args = {
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    }

    with DAG(
        dag_id="ml_retrain_dag",
        default_args=default_args,
        description="Retrain ML classifier + quantile regressors every 4 hours with quality gate",
        schedule_interval="0 */4 * * *",
        start_date=datetime(2023, 1, 1),
        catchup=False,
        tags=["ml", "retrain"],
    ) as dag:
        train_classifier = PythonOperator(
            task_id="train_ml_classifier",
            python_callable=train_ml_classifier_task,
        )
        train_regressor = PythonOperator(
            task_id="train_ml_regressor",
            python_callable=train_ml_regressor_task,
        )
        validate = PythonOperator(
            task_id="validate_model_quality",
            python_callable=validate_model_quality,
            provide_context=True,
        )
        promote = PythonOperator(
            task_id="promote_if_quality_ok",
            python_callable=promote_if_quality_ok,
            provide_context=True,
        )

        # Chain: [classifier, regressor] → validate quality → promote winners
        [train_classifier, train_regressor] >> validate >> promote

    return dag


dag = make_dag()
