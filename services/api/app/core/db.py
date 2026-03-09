import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


def get_db_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
    )


@contextmanager
def db_conn_cursor(dict_cursor: bool = True):
    conn = get_db_conn()
    try:
        factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        with conn.cursor(cursor_factory=factory) as cur:
            yield conn, cur
    finally:
        conn.close()


def fetch_one(query, params=None):
    with db_conn_cursor(dict_cursor=True) as (_conn, cur):
        cur.execute(query, params)
        return cur.fetchone()


def fetch_all(query, params=None):
    with db_conn_cursor(dict_cursor=True) as (_conn, cur):
        cur.execute(query, params)
        return cur.fetchall()


def execute(query, params=None):
    with db_conn_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(query, params)
        conn.commit()


def execute_values(query, rows):
    if not rows:
        return
    with db_conn_cursor(dict_cursor=False) as (conn, cur):
        psycopg2.extras.execute_values(cur, query, rows)
        conn.commit()
