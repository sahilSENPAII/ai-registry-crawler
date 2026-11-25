import dataclasses
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import api, db
from app.config import config
from app.pipeline import LOCK_KEY

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (e.g. postgresql://registry:registry@localhost:5433/registry_test); these tests TRUNCATE every table",
)
INTERNAL_AGENTS = {"aws-ai-crawler", "ai-observability-agent"}


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DATABASE_URL))
        db.init_schema()
        db.reset_data()
        client = TestClient(api.app)  # no `with`: startup discovery stays off until test_startup_discovery
        run = client.post("/discovery/run")
        assert run.status_code == 200, run.text
        client.run = run.json()
        yield client


def get(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def test_discovery_run(client):
    assert {k: client.run[k] for k in ("resources_scanned", "ai_systems_found", "agents_found", "findings",
                                       "recommendations", "errors")} == {
        "resources_scanned": 15, "ai_systems_found": 11, "agents_found": 4, "findings": 7, "recommendations": 11,
        "errors": []}


def test_discovery_conflict(client):
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        assert client.post("/discovery/run").status_code == 409
        other.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def test_discovery_is_mock_only(client, monkeypatch):
    monkeypatch.setattr(api, "config", dataclasses.replace(config, discovery_mode="aws"))
    assert client.post("/discovery/run").status_code == 403


def test_health_and_runs(client):
    assert get(client, "/health") == {"status": "ok", "db": "ok"}
    runs = get(client, "/runs")
    assert runs[0]["id"] == client.run["run_id"] and runs[0]["status"] == "succeeded"
    assert runs[0]["findings_count"] == 7


def test_summary(client):
    s = get(client, "/summary")
    assert (s["systems"], s["agents"], s["monitored"]) == (11, 4, 4)
    assert s["by_category"] == {"ai_agent": 4, "genai_application": 4, "ai_workflow": 1, "predictive_ml": 2}
    assert sum(s["by_risk_level"].values()) == 11
    assert s["findings_by_severity"] == {"critical": 1, "high": 2, "medium": 3, "low": 1}
    assert s["monthly_cost"]["current"] > 0 and s["monthly_cost"]["recommended"] > 0
    assert s["last_run"]["id"] == client.run["run_id"]


def test_systems_and_filters(client):
    assert len(get(client, "/systems")) == 11
    agents = get(client, "/systems?category=ai_agent")
    assert len(agents) == 4 and all(a["is_agent"] for a in agents)
    assert all(s["risk_level"] == "high" for s in get(client, "/systems?risk_level=high"))
    assert get(client, "/systems?status=retired") == []


def test_system_detail(client):
    refund = next(s for s in get(client, "/systems") if s["name"] == "refund-agent")
    detail = get(client, f"/systems/{refund['id']}")
    assert detail["evidence"] and detail["controls"] and detail["tools"]
    assert detail["recommendation"]["registry_id"] == refund["id"]
    assert {f["rule"] for f in detail["findings"]} == {"side_effect_without_approval", "approval_denied"}
    assert detail["run_stats"]["runs"] > 0 and detail["run_stats"]["tokens"] > 0
    assert client.get("/systems/does-not-exist").status_code == 404


def test_agents(client):
    agents = get(client, "/agents")
    assert len(agents) == 5
    assert sum(a["registered"] for a in agents) == 4
    shadow = next(a for a in agents if not a["registered"])
    assert shadow["name"] == "marketing-copy-agent" and shadow["findings"] == 1
    assert not INTERNAL_AGENTS & {a["name"] for a in agents}
    with_internal = {a["name"] for a in get(client, "/agents?include_internal=true")}
    assert INTERNAL_AGENTS <= with_internal and len(with_internal) == 7


def test_agent_runs_and_trace(client):
    runs = get(client, "/agents/sales-research-agent/runs")
    sales = next(a for a in get(client, "/agents") if a["name"] == "sales-research-agent")
    assert len(runs) == sales["runs"]
    assert {r["status"] for r in runs} == {"ok", "error"}
    assert all(r["duration_ms"] >= 0 and r["spans"] > 0 for r in runs)
    spans = get(client, f"/traces/{runs[0]['trace_id']}")
    assert spans[0]["operation"] == "invoke_agent" and spans[0]["parent_span_id"] is None
    assert [s["started_at"] for s in spans] == sorted(s["started_at"] for s in spans)
    assert client.get("/traces/does-not-exist").status_code == 404


def test_findings_and_filters(client):
    assert len(get(client, "/findings")) == 7
    critical = get(client, "/findings?severity=critical")
    assert [(f["agent_name"], f["rule"]) for f in critical] == [("refund-agent", "side_effect_without_approval")]
    assert {f["rule"] for f in get(client, "/findings?agent=sales-research-agent")} == \
        {"undeclared_tool", "tool_loop", "run_error"}


def test_recommendations(client):
    recs = get(client, "/recommendations")
    assert len(recs) == 11 and all(r["rationale"] for r in recs)
    assert {r["outcome"] for r in recs} == {"keep", "downgrade", "upgrade", "residency"}
    refund = next(s for s in get(client, "/systems") if s["name"] == "refund-agent")
    assert get(client, f"/systems/{refund['id']}")["recommendation"]["outcome"] == "keep"


def test_retired_hidden_by_default(client):
    with db.connect() as conn:
        conn.execute("UPDATE ai_registry SET status = 'retired' WHERE name = 'support-chatbot'")
    try:
        assert "support-chatbot" not in {s["name"] for s in get(client, "/systems")}
        assert "support-chatbot" in {s["name"] for s in get(client, "/systems?include_retired=true")}
        assert [s["name"] for s in get(client, "/systems?status=retired")] == ["support-chatbot"]
        assert len(get(client, "/recommendations")) == 10
        assert get(client, "/summary")["systems"] == 10
    finally:
        with db.connect() as conn:
            conn.execute("UPDATE ai_registry SET status = 'active' WHERE name = 'support-chatbot'")


def test_startup_discovery(client):
    db.reset_data()
    with TestClient(api.app):
        pass
    runs = get(client, "/runs")
    assert len(runs) == 1 and get(client, "/summary")["systems"] == 11
    with TestClient(api.app):  # registry not empty: no second run
        pass
    assert len(get(client, "/runs")) == 1
