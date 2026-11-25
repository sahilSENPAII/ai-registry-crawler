import hashlib

import pytest

from app import aws
from app.crawler.classifier import classify
from app.crawler.collectors import Resource, collect_all
from app.crawler.registry import assess_risk, build_record, decisions_about_people, registry_id


@pytest.fixture(scope="module")
def estate():
    with aws.simulated_aws():
        resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    assert errors == []
    return {r.name: (r, classify(r)) for r in resources}


def risk_input(**overrides):
    record = {"is_agent": False, "tools": [], "human_oversight": "none", "data_classification": "internal",
              "decisions_about_people": False, "customer_facing": False, "provider": "anthropic/bedrock",
              "environment": "prod", "log_group": None}
    return record | overrides


def test_registry_id_is_stable():
    r = Resource("lambda", "fn", "arn:aws:lambda:us-east-1:123456789012:function:fn", "123456789012", "us-east-1")
    moved = Resource("lambda", "fn", "arn:aws:lambda:us-east-1:123456789012:function:fn:v2", "123456789012", "us-east-1")
    assert registry_id(r) == registry_id(moved) == hashlib.sha1(b"lambda|123456789012|us-east-1|fn").hexdigest()[:16]
    assert registry_id(Resource("lambda", "other", r.arn, r.account_id, r.region)) != registry_id(r)


def test_autonomous_agent_with_side_effects_and_pii_is_high():
    score, level, factors = assess_risk(risk_input(
        is_agent=True, data_classification="pii",
        tools=[{"name": "issue_refund", "side_effect": True}, {"name": "lookup_order", "side_effect": False}],
    ))
    assert level == "high" and score == 2 + 2 + 2 + 2 + 1 + 1
    assert "side-effect tools: issue_refund" in factors
    assert "agent with no log group" in factors


def test_genai_with_internal_data_is_low():
    score, level, factors = assess_risk(risk_input())
    assert (score, level) == (3, "low")
    assert factors == ["no human oversight", "runs in prod"]


def test_decisions_about_people(estate):
    assert decisions_about_people(*estate["hr-policy-assistant"])
    assert decisions_about_people(*estate["loan-underwriting-workflow"])
    assert not decisions_about_people(*estate["support-chatbot"])


def test_high_risk_records_need_impact_assessment(estate):
    records = [build_record(r, c, run_id=1) for r, c in estate.values() if c.is_ai]
    high = [rec for rec in records if rec["risk_level"] == "high"]
    assert high
    for rec in high:
        assert rec["impact_assessment_status"] == "required"
        assert "A.5.2" in {c["ref"] for c in rec["controls"]}


def test_tags_are_not_guessed():
    r = Resource("lambda", "fn", "arn:aws:lambda:us-east-1:123456789012:function:fn", "123456789012", "us-east-1",
                 tags={"environment": "production"}, code={"handler.py": "import openai\nclient.chat.completions.create()"})
    rec = build_record(r, classify(r), run_id=1)
    assert (rec["environment"], rec["owner"], rec["data_classification"], rec["customer_facing"]) == \
        ("unknown", None, None, None)
