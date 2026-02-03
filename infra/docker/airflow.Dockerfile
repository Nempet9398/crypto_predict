FROM apache/airflow:2.7.3-python3.11

USER airflow

RUN pip install --no-cache-dir \
    ccxt==4.2.99 \
    pandas==2.2.1 \
    numpy==1.26.4 \
    psycopg2-binary==2.9.9 \
    scikit-learn==1.4.1.post1 \
    joblib==1.3.2
