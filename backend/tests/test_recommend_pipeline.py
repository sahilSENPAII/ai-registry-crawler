import dataclasses
import os

import pytest

from app import aws, db
from app.config import config
from app.crawler.agent import CrawlerAgent
from app.observability.agent import ObservabilityAgent
from app.recommend.engine import recommend_all

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (e.g. postgresql://registry:registry@localhost:5433/registry_test); these tests TRUNCATE every table",
)


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DATABASE_URL))
    db.init_schema()
    db.reset_data()
    with aws.simulated_aws(), db.connect() as conn:
        yield conn


def pipeline(conn):
    run_id = CrawlerAgent(aws.session(), aws.lambda_code_fetcher("mock"), conn).run()["run_id"]
    ObservabilityAgent(aws.session(), conn, run_id).run()
    recommend_all(conn, run_id)
    return run_id


def test_every_ai_system_gets_one_recommendation(conn):
    pipeline(conn)
    count = "SELECT count(*) AS n FROM model_recommendations"
    assert conn.execute(count).fetchone()["n"] == 11
    missing = conn.execute("SELECT name FROM ai_registry r LEFT JOIN model_recommendations m ON m.registry_id = r.id "
                           "WHERE m.registry_id IS NULL").fetchall()
    assert missing == []

    hr = conn.execute("SELECT m.rationale FROM model_recommendations m JOIN ai_registry r ON r.id = m.registry_id "
                      "WHERE r.name = 'hr-policy-assistant'").fetchone()
    assert hr["rationale"].startswith("Upgrade")

    run_id = pipeline(conn)
    assert conn.execute(count).fetchone()["n"] == 11
    assert {r["run_id"] for r in conn.execute("SELECT run_id FROM model_recommendations").fetchall()} == {run_id}
