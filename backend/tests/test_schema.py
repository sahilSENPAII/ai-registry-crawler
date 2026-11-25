import dataclasses
import os

import pytest
from psycopg import errors

from app import db
from app.config import config

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (e.g. postgresql://registry:registry@localhost:5433/registry_test); these tests TRUNCATE every table",
)


@pytest.fixture(autouse=True)
def test_db(monkeypatch):
    monkeypatch.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DATABASE_URL))
    db.init_schema()


def test_all_tables_exist():
    with db.connect() as conn:
        rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()
    assert set(db.TABLES) <= {r["table_name"] for r in rows}


def test_init_schema_is_idempotent():
    db.init_schema()
    db.init_schema()


def test_bad_enum_raises_check_violation():
    with db.connect() as conn, pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO ai_registry (id, resource_arn, category) VALUES ('x', 'arn:x', 'robot')")


def test_duplicate_span_raises_unique_violation():
    insert = "INSERT INTO agent_traces (agent_name, trace_id, span_id, started_at) VALUES ('a', 't1', 's1', now())"
    with db.connect() as conn:
        conn.execute(insert)
        with pytest.raises(errors.UniqueViolation):
            conn.execute(insert)
        conn.rollback()


def test_reset_data_empties_every_table():
    with db.connect() as conn:
        run_id = conn.execute("INSERT INTO discovery_runs (mode) VALUES ('mock') RETURNING id").fetchone()["id"]
        conn.execute("INSERT INTO ai_registry (id, resource_arn, last_run_id) VALUES ('r1', 'arn:r1', %s)", (run_id,))
        conn.execute("INSERT INTO model_recommendations (registry_id, run_id) VALUES ('r1', %s)", (run_id,))
    db.reset_data()
    with db.connect() as conn:
        for t in db.TABLES:
            assert conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] == 0, t
