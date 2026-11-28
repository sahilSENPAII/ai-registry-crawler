import dataclasses
import os

import pytest

from app import aws, db
from app.config import config
from app.crawler import agent as crawler_agent
from app.crawler.agent import CrawlerAgent
from app.llm import FakeLLM, reply, tool_use
from app.observability.agent import ObservabilityAgent
from app.pipeline import run_discovery

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (e.g. postgresql://registry:registry@localhost:5433/registry_test); these tests TRUNCATE every table",
)

SALES_VERDICT = {
    "name": "sales-research-agent", "is_ai": True, "is_agent": True, "category": "ai_agent",
    "reasoning": "The model picks CRM and web tools in a loop until it has a briefing.",
    "evidence_quotes": ['tools=TOOLS, tool_choice="auto"'], "models": ["gpt-4o"], "tools": ["web_search", "bing_search"],
}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DATABASE_URL))
    db.init_schema()
    db.reset_data()
    with db.connect() as conn:
        yield conn


@pytest.fixture
def estate(conn):
    with aws.simulated_aws():
        yield conn


def planner(verdict=SALES_VERDICT):
    return FakeLLM([
        tool_use(("collect_lambda", {}), ("collect_ecs", {})),
        tool_use(("inspect_resource", {"name": "sales-research-agent"})),
        tool_use(("record_verdict", verdict)),
        reply("Crawled the account."),
    ])


def crawl(conn, llm):
    return CrawlerAgent(aws.session(), aws.lambda_code_fetcher("mock"), conn, llm).run()


def observe(conn, run_id):
    return {f["rule"]: f for f in ObservabilityAgent(aws.session(), conn, run_id).run()["findings"]}


def registry(conn):
    return {r["name"]: r for r in conn.execute("SELECT * FROM ai_registry").fetchall()}


def crawler_spans(conn, run_id):
    return conn.execute("SELECT * FROM agent_traces WHERE agent_name = 'aws-ai-crawler' AND run_id = %s",
                        (run_id,)).fetchall()


def test_llm_plans_the_crawl_and_judges_what_the_rules_are_unsure_about(conn):
    llm = planner()
    summary = run_discovery(conn, "mock", until="crawl", llm=llm)["crawl"]
    assert (summary["resources_scanned"], summary["ai_systems_found"], summary["agents_found"]) == (15, 11, 4)
    assert summary["decided_by"] == {"rules": 13, "llm": 1, "rules_fallback": 1}
    assert summary["llm"]["calls"] == 4 and not summary["llm"]["budget_exceeded"]

    rows = registry(conn)
    sales = rows["sales-research-agent"]
    assert sales["decided_by"] == "llm" and sales["is_agent"]
    assert sales["llm_verdict"]["reasoning"] == SALES_VERDICT["reasoning"]
    assert {"signal": "llm_quote", "weight": 0.0, "detail": 'tools=TOOLS, tool_choice="auto"'} in sales["evidence"]
    dropped = ["tool 'bing_search' is not in what was collected"]
    assert sales["llm_verdict"]["rejected_items"] == dropped and sales["llm_verdict"]["tools"] == ["web_search"]
    assert "bing_search" not in {t["name"] for t in sales["tools"]}
    assert llm.requests[3][-1]["content"][0]["toolResult"]["content"][0]["json"] == {"accepted": True, "dropped": dropped}
    assert rows["refund-agent"]["decided_by"] == "rules" and rows["refund-agent"]["llm_verdict"] is None

    lambdas = llm.requests[1][-1]["content"][0]["toolResult"]["content"][0]["json"]["resources"]
    review = {r["name"]: r["needs_review"] for r in lambdas}
    assert review["sales-research-agent"] and not review["refund-agent"]


def test_llm_steps_are_traced_with_tokens_and_cost(estate):
    conn = estate
    run_id = crawl(conn, planner())["run_id"]
    spans = crawler_spans(conn, run_id)
    root = next(s for s in spans if s["operation"] == "invoke_agent")
    chats = [s for s in spans if s["operation"] == "chat"]
    assert len(chats) == 4 and all(s["parent_span_id"] == root["span_id"] and s["trace_id"] == root["trace_id"] for s in chats)
    assert all(s["model"] == config.llm_model_id and s["input_tokens"] > 0 and s["cost_usd"] > 0 for s in chats)
    assert root["attributes"]["crawler.llm.calls"] == 4
    assert float(root["attributes"]["crawler.llm.cost_usd"]) == pytest.approx(float(sum(s["cost_usd"] for s in chats)))
    tools = {s["tool_name"]: s["attributes"] for s in spans if s["operation"] == "execute_tool"}
    assert set(tools) >= {"collect_lambda", "collect_bedrock_agents", "inspect_resource", "record_verdict"}
    assert "crawler.guardrail" not in tools["collect_lambda"]
    for skipped in ["collect_bedrock_agents", "collect_sagemaker", "collect_stepfunctions"]:
        assert tools[skipped]["crawler.guardrail"] == "not called by the planner, run anyway"


def test_rejected_verdict_falls_back_to_rules_and_is_flagged(estate):
    conn = estate
    wrong = {**SALES_VERDICT, "is_agent": False, "category": "genai_application",
             "evidence_quotes": ["client.chat.completions.create(model=MODEL)"]}
    llm = planner(wrong)
    run_id = crawl(conn, llm)["run_id"]
    sales = registry(conn)["sales-research-agent"]
    assert sales["decided_by"] == "rules_fallback" and sales["is_agent"]
    assert sales["llm_verdict"]["rejected"][0].startswith("quote not in what the LLM was shown")
    assert llm.requests[3][-1]["content"][0]["toolResult"]["status"] == "error"
    finding = observe(conn, run_id)["llm_verdict_rejected"]
    assert finding["severity"] == "medium" and finding["agent_name"] == "aws-ai-crawler"
    assert "sales-research-agent" in finding["description"]


def test_step_budget_is_enforced(estate, monkeypatch):
    conn = estate
    monkeypatch.setattr(crawler_agent, "config", dataclasses.replace(config, llm_max_steps=2))
    llm = FakeLLM(lambda messages, system, tools: tool_use(("inspect_resource", {"name": "refund-agent"})))
    summary = crawl(conn, llm)
    assert summary["llm"]["calls"] == 2 and summary["llm"]["budget_exceeded"]
    assert summary["resources_scanned"] == 15
    sales = registry(conn)["sales-research-agent"]
    assert sales["decided_by"] == "rules_fallback"
    assert sales["llm_verdict"] == {"rejected": ["no LLM verdict within LLM_MAX_STEPS (2)"]}
    assert observe(conn, summary["run_id"])["llm_budget_exceeded"]["severity"] == "low"


def test_llm_failure_leaves_the_rules_in_charge(estate):
    conn = estate
    def down(messages, system, tools):
        raise RuntimeError("AccessDeniedException")

    summary = crawl(conn, FakeLLM(down))
    run = conn.execute("SELECT * FROM discovery_runs WHERE id = %s", (summary["run_id"],)).fetchone()
    assert run["status"] == "succeeded" and summary["ai_systems_found"] == 11
    assert summary["llm"]["error"] == "RuntimeError: AccessDeniedException"
    assert registry(conn)["sales-research-agent"]["llm_verdict"]["rejected"][0].startswith("the planner stopped")


def test_failed_collector_is_a_crawler_finding(estate, monkeypatch):
    def collect_ecs(session):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(crawler_agent, "collectors", lambda fetch: [collect_ecs])
    run_id = crawl(estate, None)["run_id"]
    finding = observe(estate, run_id)["collector_failed"]
    assert finding["description"] == "collect_ecs failed with RuntimeError" and finding["severity"] == "medium"
