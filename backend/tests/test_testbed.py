import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from moto import mock_aws

from app import aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "testbed"))
import deploy  # noqa: E402
import teardown  # noqa: E402


@pytest.fixture
def account():
    with mock_aws():
        yield aws.session()


def test_spans_fit_one_log_batch_and_include_the_planted_cases():
    now = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
    spans = deploy.build_spans(now)
    times = [datetime.fromisoformat(s["start_time"]) for group in spans.values() for s in group]
    assert max(times) - min(times) < timedelta(hours=24) and max(times) < now
    refunds = [json.loads(s["attributes"]["gen_ai.tool.call.arguments"]) for s in spans["refund-agent"]
               if s["attributes"].get("gen_ai.tool.name") == "issue_refund"]
    assert {"order_id": "ORD-731204", "amount": 1200.0, "reason": "late delivery"} in refunds
    assert not any(s["attributes"]["gen_ai.operation.name"] == "human_approval" for s in spans["refund-agent"])
    assert {s["attributes"]["gen_ai.agent.name"] for s in spans["marketing-copy-agent"]} == {"marketing-copy-agent"}


def test_deploy_is_idempotent_and_tags_everything(account):
    deploy.deploy()
    deploy.deploy()
    lam, logs = account.client("lambda"), account.client("logs")
    functions = lam.list_functions()["Functions"]
    assert sorted(f["FunctionName"] for f in functions) == sorted(deploy.LAMBDAS)
    refund = lam.get_function_configuration(FunctionName="refund-agent")
    assert refund["Environment"]["Variables"]["APPROVAL_THRESHOLD"] == "500"
    assert lam.list_tags(Resource=refund["FunctionArn"])["Tags"]["data-classification"] == "pii"

    events = logs.filter_log_events(logGroupName="/ai/agents/refund-agent")["events"]
    assert len(events) == len(deploy.build_spans(datetime.now(timezone.utc))["refund-agent"])

    found = list(teardown.tagged(account))
    assert sorted(name for kind, name in found if kind == "lambda") == sorted(deploy.LAMBDAS)
    assert sorted(name for kind, name in found if kind == "log group") == \
        ["/ai/agents/marketing-copy-agent", "/ai/agents/refund-agent"]
    assert sorted(name for kind, name in found if kind == "iam role") == \
        sorted([deploy.CRAWLER_ROLE] + [f"testbed-{n}" for n in deploy.LAMBDAS])


def test_teardown_removes_only_tagged_resources(account):
    deploy.deploy()
    account.client("logs").create_log_group(logGroupName="/ai/agents/someone-elses-agent")
    teardown.teardown()
    assert list(teardown.tagged(account)) == []
    assert account.client("lambda").list_functions()["Functions"] == []
    groups = account.client("logs").describe_log_groups()["logGroups"]
    assert [g["logGroupName"] for g in groups] == ["/ai/agents/someone-elses-agent"]
