import dataclasses
import os

import pytest

from app import aws, db
from app.config import config
from app.crawler.agent import CrawlerAgent
from app.observability.agent import ObservabilityAgent
from app.seed.expected import EXPECTED_FINDINGS

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (e.g. postgresql://registry:registry@localhost:5433/registry_test); these tests TRUNCATE every table",
)


def observe(conn):
    run_id = CrawlerAgent(aws.session(), aws.lambda_code_fetcher("mock"), conn).run()["run_id"]
    return ObservabilityAgent(aws.session(), conn, run_id).run()


@pytest.fixture(scope="module")
def conn():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DATABASE_URL))
        db.init_schema()
        db.reset_data()
        with aws.simulated_aws(), db.connect() as conn:
            observe(conn)
            yield conn


def counts(conn):
    return (conn.execute("SELECT count(*) AS n FROM agent_traces").fetchone()["n"],
            conn.execute("SELECT count(*) AS n FROM agent_anomalies").fetchone()["n"])


def test_findings_match_expected(conn):
    rows = conn.execute("SELECT rule, agent_name FROM agent_anomalies").fetchall()
    assert sorted((r["rule"], r["agent_name"]) for r in rows) == sorted((f["rule"], f["agent"]) for f in EXPECTED_FINDINGS)


def test_shadow_agent_spans_are_unlinked(conn):
    rows = conn.execute("SELECT DISTINCT registry_id FROM agent_traces WHERE agent_name = 'marketing-copy-agent'").fetchall()
    assert rows == [{"registry_id": None}]
    linked = conn.execute("SELECT count(*) AS n FROM agent_traces WHERE agent_name = 'refund-agent' "
                          "AND registry_id IS NULL").fetchone()["n"]
    assert linked == 0


def test_agents_are_monitored(conn):
    rows = conn.execute("SELECT name, monitored FROM ai_registry WHERE is_agent").fetchall()
    assert len(rows) == 4 and all(r["monitored"] for r in rows)


def test_rerun_does_not_duplicate(conn):
    agent_spans = "SELECT count(*) AS n FROM agent_traces WHERE NOT (attributes ? 'internal')"
    before_spans = conn.execute(agent_spans).fetchone()["n"]
    before_findings = counts(conn)[1]
    observe(conn)
    run_id = conn.execute("SELECT max(id) AS id FROM discovery_runs").fetchone()["id"]
    ObservabilityAgent(aws.session(), conn, run_id).run()
    assert conn.execute(agent_spans).fetchone()["n"] == before_spans == 564
    assert counts(conn)[1] == before_findings == 7
    assert conn.execute("SELECT findings_count FROM discovery_runs WHERE id = %s", (run_id,)).fetchone()["findings_count"] == 7
