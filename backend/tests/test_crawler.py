import dataclasses
import os

import pytest

from app import aws, db
from app.config import config
from app.crawler import agent as crawler_agent
from app.crawler.agent import CrawlerAgent

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


def crawl(conn):
    return CrawlerAgent(aws.session(), aws.lambda_code_fetcher("mock"), conn).run()


def registry(conn):
    return {r["name"]: r for r in conn.execute("SELECT * FROM ai_registry").fetchall()}


def test_first_crawl(conn):
    summary = crawl(conn)
    rows = registry(conn)
    assert len(rows) == 11 and all(r["status"] == "active" for r in rows.values())
    assert sum(r["is_agent"] for r in rows.values()) == 4
    run = conn.execute("SELECT * FROM discovery_runs WHERE id = %s", (summary["run_id"],)).fetchone()
    assert run["status"] == "succeeded" and run["finished_at"] is not None
    assert (run["resources_scanned"], run["ai_systems_found"], run["agents_found"]) == (15, 11, 4)


def test_second_crawl_keeps_first_seen(conn):
    crawl(conn)
    first = {name: r["first_seen_at"] for name, r in registry(conn).items()}
    second = crawl(conn)
    rows = registry(conn)
    assert len(rows) == 11
    assert {name: r["first_seen_at"] for name, r in rows.items()} == first
    assert all(r["last_run_id"] == second["run_id"] for r in rows.values())


def test_deleted_lambda_is_retired(conn):
    crawl(conn)
    aws.session().client("lambda").delete_function(FunctionName="support-chatbot")
    summary = crawl(conn)
    rows = registry(conn)
    assert summary["retired"] == 1
    assert rows["support-chatbot"]["status"] == "retired"
    assert sum(r["status"] == "active" for r in rows.values()) == 10


def test_crawler_traces_itself(conn):
    summary = crawl(conn)
    spans = conn.execute("SELECT * FROM agent_traces WHERE agent_name = 'aws-ai-crawler' AND run_id = %s",
                         (summary["run_id"],)).fetchall()
    assert [s["operation"] for s in spans].count("invoke_agent") == 1
    assert [s["operation"] for s in spans].count("execute_tool") == 5
    assert all(s["registry_id"] is None and s["attributes"]["internal"] is True for s in spans)
    lam = next(s for s in spans if s["tool_name"] == "collect_lambda")
    assert lam["attributes"]["crawler.resources"] == 8


def test_failed_collector_is_recorded_and_nothing_retired(conn, monkeypatch):
    crawl(conn)

    def collect_ecs(session):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(crawler_agent, "collectors", lambda fetch: [collect_ecs])
    summary = crawl(conn)
    run = conn.execute("SELECT * FROM discovery_runs WHERE id = %s", (summary["run_id"],)).fetchone()
    assert run["status"] == "succeeded" and "AccessDenied" in run["error"]
    assert summary["retired"] == 0
    assert all(r["status"] == "active" for r in registry(conn).values())


def test_crash_marks_run_failed(conn, monkeypatch):
    def broken(resource):
        raise ValueError("boom")

    monkeypatch.setattr(crawler_agent, "classify", broken)
    with pytest.raises(ValueError):
        crawl(conn)
    run = conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["status"] == "failed" and "boom" in run["error"]
    assert registry(conn) == {}
