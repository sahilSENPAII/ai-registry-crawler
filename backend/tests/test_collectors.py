import json

import pytest
from moto import mock_aws

from app import aws
from app.crawler import collectors
from app.crawler.collectors import _role_actions, collect_all
from app.seed.estate import LAMBDA_SRC
from app.seed.expected import EXPECTED

AGENT_LOG_GROUPS = {
    "refund-agent": "/ai/agents/refund-agent",
    "sales-research-agent": "/ai/agents/sales-research-agent",
    "hr-policy-assistant": "/ai/agents/hr-policy-assistant",
    "procurement-agent": "/ai/agents/procurement-agent",
}


@pytest.fixture(scope="module")
def collected():
    with aws.simulated_aws():
        resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
        yield {r.name: r for r in resources}, errors


def test_collects_all_15(collected):
    by_name, errors = collected
    assert errors == []
    assert sorted(by_name) == sorted(EXPECTED)
    assert all(not r.errors for r in by_name.values())


def test_lambdas_have_handler_source(collected):
    lambdas = [r for r in collected[0].values() if r.service == "lambda"]
    assert len(lambdas) == 8
    for r in lambdas:
        assert r.code == {"handler.py": (LAMBDA_SRC / r.name.replace("-", "_") / "handler.py").read_text()}


def test_shared_role_actions(collected):
    by_name = collected[0]
    assert "bedrock:InvokeModel" in by_name["order-fulfilment"].iam_actions
    assert by_name["order-fulfilment"].role_arn == by_name["loan-underwriting-workflow"].role_arn


def test_ecs_env(collected):
    assert collected[0]["procurement-agent"].env["AGENT_FRAMEWORK"] == "crewai"


def test_bedrock_agent(collected):
    hr = collected[0]["hr-policy-assistant"]
    assert hr.config["foundation_model"].startswith("anthropic.claude")
    assert {"ai:declared-tools", "ai:knowledge-base", "ai:log-group"} <= hr.tags.keys()


def test_stepfunctions_definition_parsed(collected):
    states = collected[0]["loan-underwriting-workflow"].config["definition"]["States"]
    assert states["AssessApplication"]["Resource"] == "arn:aws:states:::bedrock:invokeModel"


def test_log_groups(collected):
    by_name = collected[0]
    for name, r in by_name.items():
        assert r.log_groups == ([AGENT_LOG_GROUPS[name]] if name in AGENT_LOG_GROUPS else []), name
    assert not any("/ai/agents/marketing-copy-agent" in r.log_groups for r in by_name.values())


def test_failing_collector_is_recorded(collected, monkeypatch):
    def broken(session):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(collectors, "collect_sagemaker", broken)
    resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    assert len(resources) == 13
    assert errors == ["broken: AccessDenied"]


@mock_aws
def test_role_actions_include_attached_policies():
    s = aws.session()
    iam = s.client("iam")
    iam.create_role(RoleName="r", AssumeRolePolicyDocument="{}")
    policy = {"Version": "2012-10-17", "Statement": {"Effect": "Allow", "Action": "bedrock:InvokeModel", "Resource": "*"}}
    arn = iam.create_policy(PolicyName="p", PolicyDocument=json.dumps(policy))["Policy"]["Arn"]
    iam.attach_role_policy(RoleName="r", PolicyArn=arn)
    iam.put_role_policy(RoleName="r", PolicyName="inline", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "*"},
                      {"Effect": "Deny", "Action": "s3:PutObject", "Resource": "*"}],
    }))
    assert _role_actions(s, "arn:aws:iam::123456789012:role/r") == ("bedrock:InvokeModel", "s3:GetObject")
