import dataclasses

import pytest

from app import aws
from app.crawler.classifier import classify
from app.crawler.collectors import collect_all
from app.crawler.hybrid import MAX_CODE_CHARS, ask, confident, context, decide, validate
from app.llm import FakeLLM, reply, tool_use
from app.seed.variants import VARIANTS

VARIANT = {r.name: r for r, _, _ in VARIANTS}


@pytest.fixture(scope="module")
def estate():
    with aws.simulated_aws():
        resources, _ = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    return {r.name: r for r in resources}


def verdict(**overrides):
    return {"is_ai": True, "is_agent": False, "category": "genai_application", "reasoning": "Calls Comprehend.",
            "evidence_quotes": ['comprehend.detect_sentiment(Text=event["review"], LanguageCode="en")'], **overrides}


def test_rules_only_defer_on_unclear_estate_resources(estate):
    unsure = sorted(name for name, r in estate.items() if not confident(r, classify(r)))
    assert unsure == ["order-fulfilment", "sales-research-agent"]


def test_valid_verdict_passes():
    assert validate(verdict(models=[], tools=[]), VARIANT["review-sentiment"]) == []


def test_quotes_match_ignoring_whitespace():
    quote = 'comprehend.detect_sentiment(Text=event["review"],\n        LanguageCode="en")'
    assert validate(verdict(evidence_quotes=[quote]), VARIANT["review-sentiment"]) == []


@pytest.mark.parametrize("quote", [
    '"comprehend.detect_sentiment(Text=event[\\"review\\"], LanguageCode=\\"en\\")"',  # wrapped and JSON-escaped
    "iam actions:\n  comprehend:DetectSentiment",  # with the label it was shown under
    '"iam actions": comprehend:DetectSentiment',
])
def test_quotes_match_what_the_llm_was_shown(quote):
    assert validate(verdict(evidence_quotes=[quote]), VARIANT["review-sentiment"]) == []


@pytest.mark.parametrize("quote", ['"comprehend.detect_sentiment(...)"', "iam actions: comprehend:DetectEntities", "   ",
                                   "past_the_clip = 1"])
def test_paraphrased_unshown_or_empty_quotes_are_rejected(quote):
    r = VARIANT["review-sentiment"]
    r = dataclasses.replace(r, code={"handler.py": r.code["handler.py"] + "#" * MAX_CODE_CHARS + "\npast_the_clip = 1"})
    problems = validate(verdict(evidence_quotes=[quote]), r)
    assert problems and problems[0].startswith("quote not in what the LLM was shown"), problems


@pytest.mark.parametrize("overrides, problem", [
    ({"evidence_quotes": ["bedrock.converse(modelId=MODEL_ID)"]}, "quote not in what the LLM was shown"),
    ({"evidence_quotes": []}, "an AI verdict needs at least one evidence quote"),
    ({"is_agent": True}, "category ai_agent and is_agent disagree"),
    ({"category": "chatbot"}, "unknown category 'chatbot'"),
    ({"is_ai": False, "is_agent": True, "category": "ai_agent"}, "an agent must also be an AI system"),
    ({"is_ai": "yes"}, "is_ai and is_agent must be true or false"),
])
def test_invalid_verdicts_are_rejected(overrides, problem):
    problems = validate(verdict(**overrides), VARIANT["review-sentiment"])
    assert any(p.startswith(problem) for p in problems), problems


def test_accepted_verdict_overrides_only_the_decision():
    r = VARIANT["review-sentiment"]
    rules = classify(r)
    c, decided_by, stored = decide(r, rules, verdict())
    assert (rules.is_ai, c.is_ai, c.is_agent, c.category, decided_by) == (False, True, False, "genai_application", "llm")
    assert stored["reasoning"] == "Calls Comprehend."
    assert any(e.signal == "llm_quote" for e in c.evidence)
    assert c.unused_ai_permissions == [] and c.task_type == "content_generation"


def test_agent_verdict_brings_its_tools_and_models():
    r = VARIANT["pydantic-ai-agent"]
    v = verdict(is_agent=True, category="ai_agent", evidence_quotes=["tools=[get_order, issue_refund]"],
                models=["anthropic.claude-3-5-sonnet-20240620-v1:0"], tools=["get_order", "issue_refund"])
    c, decided_by, _ = decide(r, classify(r), v)
    assert decided_by == "llm" and c.is_agent and c.task_type == "agentic_tool_use"
    assert {"name": "issue_refund", "side_effect": True} in c.tools
    assert c.provider == "anthropic/bedrock"


def test_ungrounded_models_and_tools_are_dropped_and_never_stored():
    r = VARIANT["review-sentiment"]
    v = verdict(models=["Amazon Comprehend", "comprehend"], tools=["issue_refund"])
    assert validate(v, r) == []
    c, decided_by, stored = decide(r, classify(r), v)
    assert decided_by == "llm" and (stored["models"], stored["tools"]) == (["comprehend"], [])
    assert stored["rejected_items"] == ["model 'Amazon Comprehend' is not in what was collected",
                                        "tool 'issue_refund' is not in what was collected"]
    assert "Amazon Comprehend" not in c.models and not any(t["name"] == "issue_refund" for t in c.tools)

    _, decided_by, stored = decide(r, classify(r), {**v, "evidence_quotes": ["made up"]})
    assert decided_by == "rules_fallback" and stored["rejected"] and stored["models"] == ["comprehend"]


def test_rejected_or_missing_verdict_falls_back_to_rules():
    r = VARIANT["review-sentiment"]
    rules = classify(r)
    c, decided_by, stored = decide(r, rules, verdict(evidence_quotes=["made up"]))
    assert (c, decided_by) == (rules, "rules_fallback") and stored["rejected"] and stored["is_ai"] is True
    assert decide(r, rules, None, "no verdict in time") == (rules, "rules_fallback", {"rejected": ["no verdict in time"]})


def test_ask_sends_masked_truncated_context(estate):
    r = estate["sales-research-agent"]
    r = dataclasses.replace(r, code={"handler.py": r.code["handler.py"] + "#" * 10_000})
    llm = FakeLLM([tool_use(("record_verdict", verdict()))])
    assert ask(llm, r) == verdict()
    sent = llm.requests[0][0]["content"][0]["text"]
    assert "OPENAI_API_KEY_SECRET_ARN=[masked]" in sent
    assert "more characters]" in sent and len(sent) < MAX_CODE_CHARS + 2_000
    assert llm.spans[0]["attributes"]["crawler.llm.purpose"] == "classify sales-research-agent"


def test_ask_without_a_verdict_returns_none():
    assert ask(FakeLLM([reply("I am not sure.")]), VARIANT["faq-bot"]) is None


def test_context_lists_env_iam_and_code():
    text = context(VARIANT["review-sentiment"])
    assert "iam actions:\n  comprehend:DetectSentiment" in text and "code handler.py:\nimport boto3" in text
