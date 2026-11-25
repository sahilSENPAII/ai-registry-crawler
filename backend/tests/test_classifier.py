import pytest

from app import aws
from app.crawler.classifier import classify, provider_for_model
from app.crawler.collectors import Resource, collect_all
from app.seed.expected import EXPECTED


@pytest.fixture(scope="module")
def results():
    with aws.simulated_aws():
        resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    assert errors == []
    return {r.name: classify(r) for r in resources}


def lambda_resource(code="", env=None, iam_actions=None):
    return Resource("lambda", "fn", "arn:aws:lambda:us-east-1:123456789012:function:fn", "123456789012", "us-east-1",
                    env=env or {}, code={"handler.py": code}, iam_actions=iam_actions or [])


def test_estate_matches_expected(results):
    for name, c in results.items():
        assert {"is_ai": c.is_ai, "is_agent": c.is_agent, "category": c.category} == EXPECTED[name], name
    assert sum(c.is_ai for c in results.values()) == 11
    assert sum(c.is_agent for c in results.values()) == 4


def test_iam_permission_alone_is_not_ai(results):
    c = results["order-fulfilment"]
    assert not c.is_ai
    assert "bedrock:InvokeModel" in c.unused_ai_permissions


def test_refund_agent_tools_and_oversight(results):
    c = results["refund-agent"]
    tools = {t["name"]: t["side_effect"] for t in c.tools}
    assert tools["issue_refund"] is True
    assert tools["lookup_order"] is False
    assert c.human_oversight == "approval_gate"


def test_agents_have_evidence(results):
    for name, c in results.items():
        assert c.agent_score <= 1.0
        if c.is_agent:
            assert len(c.evidence) >= 2, name


def test_workflow_and_predictive_categories(results):
    assert results["loan-underwriting-workflow"].category == "ai_workflow"
    assert results["churn-predictor"].category == "predictive_ml"


def test_single_openai_call_is_genai_not_agent():
    code = (
        "from openai import OpenAI\n"
        "client = OpenAI()\n"
        "def handler(event, context):\n"
        "    r = client.chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': event['q']}])\n"
        "    return r.choices[0].message.content\n"
    )
    c = classify(lambda_resource(code))
    assert (c.is_ai, c.is_agent, c.category) == (True, False, "genai_application")
    assert c.models == ["gpt-4o-mini"] and c.provider == "openai/api"


def test_tools_and_loop_is_agent():
    code = (
        "import anthropic\n"
        "client = anthropic.Anthropic()\n"
        "TOOLS = [{'name': 'delete_account', 'input_schema': {}}]\n"
        "def handler(event, context):\n"
        "    msg = client.messages.create(model='claude-sonnet-4', tools=TOOLS, messages=event['messages'])\n"
        "    while msg.stop_reason == 'tool_use':\n"
        "        msg = client.messages.create(model='claude-sonnet-4', tools=TOOLS, messages=event['messages'])\n"
    )
    c = classify(lambda_resource(code))
    assert c.is_agent and c.category == "ai_agent"
    assert c.agent_score == pytest.approx(0.65)


def test_bedrock_permission_only_is_not_ai():
    c = classify(lambda_resource("def handler(event, context):\n    return {'ok': True}\n",
                                 iam_actions=["bedrock:InvokeModel", "s3:GetObject"]))
    assert not c.is_ai and c.category is None
    assert c.unused_ai_permissions == ["bedrock:InvokeModel"]
    assert [e.signal for e in c.evidence] == ["iam_permission_only"]


def test_provider_for_model():
    assert provider_for_model("anthropic.claude-3-haiku-20240307-v1:0") == ("anthropic", "bedrock")
    assert provider_for_model("us.meta.llama3-1-70b-instruct-v1:0") == ("meta", "bedrock")
    assert provider_for_model("gpt-4o") == ("openai", "api")
    assert provider_for_model("claude-sonnet-4") == ("anthropic", "api")
