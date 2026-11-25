import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import groupby

import pytest
from moto import mock_aws

from app import aws
from app.config import config
from app.crawler.collectors import collect_all
from app.seed.agent_logs import seed_agent_logs
from app.seed.estate import LAMBDA_SRC, seed_estate
from app.seed.expected import EXPECTED

CATEGORIES = {"ai_agent", "genai_application", "ai_workflow", "predictive_ml"}
GIVEAWAY_KEYS = {"type", "kind", "category", "is-ai", "is-agent", "ai-type", "ai-category", "workload-type"}
GIVEAWAY_VALUES = CATEGORIES | {"agent", "ai-agent", "genai", "ai", "ml", "non-ai", "predictive-ml", "ai-workflow"}


@pytest.fixture
def estate():
    with aws.simulated_aws():
        yield


def read_spans():
    logs = aws.session().client("logs")
    spans = {}
    for group in logs.describe_log_groups(logGroupNamePrefix=config.agent_log_prefix)["logGroups"]:
        pages = logs.get_paginator("filter_log_events").paginate(logGroupName=group["logGroupName"])
        agent = group["logGroupName"].removeprefix(config.agent_log_prefix)
        spans[agent] = [json.loads(e["message"]) for page in pages for e in page["events"]]
    return spans


def runs(spans):
    by_trace = defaultdict(list)
    for span in spans:
        by_trace[span["trace_id"]].append(span)
    return [sorted(s, key=lambda x: x["start_time"]) for s in by_trace.values()]


def op(span):
    return span["attributes"]["gen_ai.operation.name"]


def tool_calls(run):
    return [(s["attributes"]["gen_ai.tool.name"], json.loads(s["attributes"]["gen_ai.tool.call.arguments"]))
            for s in run if op(s) == "execute_tool"]


def approvals(run):
    return [s["attributes"] for s in run if op(s) == "human_approval"]


def collected():
    resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    assert errors == []
    return resources


def test_resource_counts(estate):
    resources = collected()
    assert Counter(r.service for r in resources) == {
        "lambda": 8, "bedrock-agent": 1, "sagemaker": 2, "stepfunctions": 2, "ecs": 2,
    }
    assert sorted(r.name for r in resources) == sorted(EXPECTED)
    assert Counter(e["category"] for e in EXPECTED.values()) == {
        "ai_agent": 4, "genai_application": 4, "ai_workflow": 1, "predictive_ml": 2, None: 4,
    }


def test_lambda_code_readable(estate):
    fetch = aws.lambda_code_fetcher("mock")
    functions = aws.session().client("lambda").list_functions()["Functions"]
    assert len(functions) == 8
    for fn in functions:
        src_dir = fn["FunctionName"].replace("-", "_")
        assert fetch(fn["FunctionName"]) == {"handler.py": (LAMBDA_SRC / src_dir / "handler.py").read_text()}


def test_planted_cases(estate):
    spans = read_spans()
    refund, sales, procurement = runs(spans["refund-agent"]), runs(spans["sales-research-agent"]), runs(spans["procurement-agent"])

    # a. $1,200 refund without approval
    assert any(not approvals(r) and ("issue_refund", 1200.0) in [(n, a.get("amount")) for n, a in tool_calls(r)]
               for r in refund)
    # b. $750 refund denied
    assert any({"approval.action": "issue_refund", "approval.amount": 750.0, "approval.decision": "denied"}.items()
               <= a.items() for r in refund for a in approvals(r))
    # c. undeclared tool
    assert "export_contacts_csv" in {n for r in sales for n, _ in tool_calls(r)}
    # d. web_search five times in a row; normal runs never exceed two
    longest = [max(len(list(g)) for _, g in groupby(n for n, _ in tool_calls(r))) for r in sales if tool_calls(r)]
    assert sorted(longest)[-1] == 5 and sorted(longest)[-2] <= 2
    # e. run ending in RateLimitError
    assert any(s["status"] == "error" and s["attributes"].get("error.type") == "RateLimitError"
               and s["parent_span_id"] is None for s in spans["sales-research-agent"])
    # f. ~100k-token run, normal runs 5-10k
    tokens = sorted(sum(s["attributes"].get("gen_ai.usage.input_tokens", 0) + s["attributes"].get("gen_ai.usage.output_tokens", 0)
                        for s in r) for r in procurement)
    assert tokens[-1] >= 90_000 and tokens[-2] <= 10_000
    # g. control: approved $25k purchase order, approval before the call
    assert any(
        [op(s) for s in r if op(s) != "chat"][-2:] == ["human_approval", "execute_tool"]
        and approvals(r)[0]["approval.decision"] == "approved"
        and tool_calls(r)[-1] == ("create_purchase_order", {"supplier": "Kestrel Freight", "amount": 25000.0})
        for r in procurement
    )
    # h. shadow agent with logs but no inventory entry
    assert spans["marketing-copy-agent"] and "marketing-copy-agent" not in EXPECTED
    assert set(spans) == {"refund-agent", "sales-research-agent", "hr-policy-assistant", "procurement-agent",
                          "marketing-copy-agent"}
    assert all(10 <= len(runs(s)) <= 25 for s in spans.values())


def test_seeding_is_deterministic():
    now = datetime.now(timezone.utc)
    results = []
    for _ in range(2):
        with mock_aws():
            seed_estate()
            seed_agent_logs(now)
            results.append(read_spans())
    assert results[0] == results[1]


def test_no_category_giveaways(estate):
    pairs = [kv for r in collected() for kv in (r.tags | r.env).items()]
    assert pairs
    for key, value in pairs:
        assert key.lower().replace("_", "-") not in GIVEAWAY_KEYS, key
        assert str(value).lower() not in GIVEAWAY_VALUES, (key, value)
