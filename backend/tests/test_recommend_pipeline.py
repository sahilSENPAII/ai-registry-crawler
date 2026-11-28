import dataclasses
import os

import pytest

from app import aws, db
from app.config import config
from app.crawler.agent import CrawlerAgent
from app.observability.agent import ObservabilityAgent
from app.pipeline import run_discovery
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

    outcomes = {r["name"]: r["outcome"] for r in conn.execute(
        "SELECT r.name, m.outcome FROM model_recommendations m JOIN ai_registry r ON r.id = m.registry_id").fetchall()}
    assert outcomes == {
        "churn-predictor": "keep", "fraud-scoring": "keep", "loan-underwriting-workflow": "keep",
        "procurement-agent": "keep", "refund-agent": "keep",
        "contract-review-rag": "downgrade", "invoice-summarizer": "downgrade", "support-chatbot": "downgrade",
        "hr-policy-assistant": "upgrade",
        "sales-research-agent": "residency", "ticket-classifier": "residency",
    }

    run_id = pipeline(conn)
    assert conn.execute(count).fetchone()["n"] == 11
    assert {r["run_id"] for r in conn.execute("SELECT run_id FROM model_recommendations").fetchall()} == {run_id}


@pytest.fixture
def empty(monkeypatch):
    """No moto here: run_discovery starts the simulated estate itself."""
    monkeypatch.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DATABASE_URL))
    db.init_schema()
    db.reset_data()
    with db.connect() as conn:
        yield conn


def test_critical_or_high_findings_bring_the_incident_control(empty):
    run_discovery(empty, "mock")
    refs = {r["name"]: {c["ref"] for c in r["controls"]} for r in empty.execute("SELECT name, controls FROM ai_registry")}
    worst = {r["name"]: r["severity"] for r in empty.execute(
        "SELECT r.name, min(array_position(ARRAY['critical','high','medium','low'], a.severity)) AS severity "
        "FROM agent_anomalies a JOIN ai_registry r ON r.id = a.registry_id GROUP BY r.name")}
    assert "A.8.4" in refs["refund-agent"] and "A.8.4" in refs["sales-research-agent"]
    assert worst["procurement-agent"] == 3 and "A.8.4" not in refs["procurement-agent"]  # medium only
    assert "invoice-summarizer" not in worst and "A.8.4" not in refs["invoice-summarizer"]
    assert {name for name, r in refs.items() if "A.8.4" in r} == {name for name, s in worst.items() if s <= 2}

    run_discovery(empty, "mock")  # a second run does not add it twice
    controls = empty.execute("SELECT controls FROM ai_registry WHERE name = 'refund-agent'").fetchone()["controls"]
    assert [c["ref"] for c in controls].count("A.8.4") == 1
