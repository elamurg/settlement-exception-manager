"""Guard: money and quantities are stored as NUMERIC, never as floating point.

Runs against a real PostgreSQL and is skipped unless DATABASE_URL is set. Locally,
after `docker compose up`:

    export DATABASE_URL=postgresql://settlement:settlement-dev@localhost:5432/settlement
"""

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import TupleRow

FLOAT_TYPES = ["double precision", "real"]

FIND_FLOAT_COLUMNS = """
    SELECT table_schema, table_name, column_name, data_type
    FROM information_schema.columns
    WHERE data_type = ANY(%s)
      AND table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name, column_name
"""


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[TupleRow]]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; start Postgres with docker compose to run this")
    with psycopg.connect(url) as connection:
        yield connection
        connection.rollback()


def float_columns(conn: psycopg.Connection[TupleRow]) -> list[tuple[Any, ...]]:
    return conn.execute(FIND_FLOAT_COLUMNS, [FLOAT_TYPES]).fetchall()


def test_schema_has_no_float_columns(conn: psycopg.Connection[TupleRow]) -> None:
    assert float_columns(conn) == []


def test_guard_detects_float_columns(conn: psycopg.Connection[TupleRow]) -> None:
    conn.execute("CREATE TABLE guard_check (amount DOUBLE PRECISION, quantity REAL, price NUMERIC)")

    found = float_columns(conn)

    assert found == [
        ("public", "guard_check", "amount", "double precision"),
        ("public", "guard_check", "quantity", "real"),
    ]
