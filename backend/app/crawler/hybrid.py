"""Hybrid classification: the rules decide when they are confident, otherwise an LLM verdict that passes the validator.
Pure apart from the LLM call in ask()."""
import json

from app.crawler.classifier import MODEL_ID, Classification, classify
from app.crawler.collectors import Resource

CLEAR_AI = {"model_call", "bedrock_agent", "ml_image", "workflow_ai_step", "agent_framework_env"}
CATEGORIES = ["ai_agent", "genai_application", "ai_workflow", "predictive_ml"]
MAX_CODE_CHARS = 6000

DEFINITIONS = """Definitions used by the AI registry:
- An AI system itself sends requests to a model or an AI service (an LLM API, Bedrock, a SageMaker endpoint, \
Comprehend, Rekognition, Textract and so on) or serves a trained model. Importing an AI library, holding an AI \
permission, mentioning a model name, or invoking another function that uses AI does not make a resource AI.
- An agent is an AI system in which a model chooses which tools to call, usually in a loop, to complete a task. \
A single model call is not an agent, even when it passes a tool schema to get structured output. A resource that \
only calls an agent through its API is an AI system but not an agent.
- category: ai_agent for agents; otherwise ai_workflow for Step Functions, predictive_ml for a trained ML model, \
genai_application for the rest; none when the resource is not AI.
Quote evidence word for word from what you were shown. List only models and tools that appear there."""

JUDGE_PROMPT = f"""You classify one AWS resource for an ISO/IEC 42001 AI registry. Read its code and configuration, \
then call record_verdict exactly once.

{DEFINITIONS}"""

VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record whether a resource is an AI system and whether it is an agent, with quoted evidence.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "the resource name"},
            "is_ai": {"type": "boolean"},
            "is_agent": {"type": "boolean"},
            "category": {"type": "string", "enum": [*CATEGORIES, "none"]},
            "reasoning": {"type": "string", "description": "two or three sentences"},
            "evidence_quotes": {"type": "array", "items": {"type": "string"},
                                "description": "short verbatim snippets from the code or configuration"},
            "models": {"type": "array", "items": {"type": "string"}},
            "tools": {"type": "array", "items": {"type": "string"}, "description": "tools the model can call"},
        },
        "required": ["is_ai", "is_agent", "category", "reasoning", "evidence_quotes"],
    }},
}


def _mentions_model(r: Resource) -> bool:
    return any(MODEL_ID.search(text) for text in [*r.code.values(), *r.env.values(), json.dumps(r.config)])


def confident(resource: Resource, c: Classification) -> bool:
    """A clear AI or non-AI signal, and an agent score clearly low or high."""
    signals = {e.signal for e in c.evidence}
    clear = bool(signals & CLEAR_AI) or (not signals and not _mentions_model(resource))
    return clear and (c.agent_score <= 0.2 or c.agent_score >= 0.8)


def _clip(text, limit):
    return text if len(text) <= limit else f"{text[:limit]}\n... [{len(text) - limit} more characters]"


def context(r: Resource) -> str:
    """What the LLM is shown: already masked at collection, with the code truncated."""
    per_file = MAX_CODE_CHARS // max(len(r.code), 1)
    lines = [f"name: {r.name}", f"service: {r.service}",
             "env:", *(f"  {k}={v}" for k, v in r.env.items()),
             "tags:", *(f"  {k}={v}" for k, v in r.tags.items()),
             "iam actions:", *(f"  {a}" for a in r.iam_actions),
             f"config: {json.dumps(r.config, indent=1)}"]
    lines += [f"code {path}:\n{_clip(text, per_file)}" for path, text in r.code.items()]
    return "\n".join(lines)


def _collected_text(r: Resource) -> str:
    parts = [*r.code.values(), *(f"{k}={v}" for k, v in r.env.items()), *(f"{k}={v}" for k, v in r.tags.items()),
             *r.iam_actions, json.dumps(r.config, indent=1)]
    return "\n".join(parts)


def _squash(text):
    return " ".join(text.split())


def validate(verdict: dict, resource: Resource) -> list[str]:
    """Why a verdict cannot be trusted; empty when it can."""
    if not isinstance(verdict.get("is_ai"), bool) or not isinstance(verdict.get("is_agent"), bool):
        return ["is_ai and is_agent must be true or false"]
    problems = []
    is_ai, is_agent, category = verdict["is_ai"], verdict["is_agent"], verdict.get("category")
    if is_agent and not is_ai:
        problems.append("an agent must also be an AI system")
    if is_ai and category not in CATEGORIES:
        problems.append(f"unknown category {category!r}")
    if is_ai and (category == "ai_agent") != is_agent:
        problems.append("category ai_agent and is_agent disagree")
    quotes = verdict.get("evidence_quotes") or []
    if is_ai and not quotes:
        problems.append("an AI verdict needs at least one evidence quote")
    text = _collected_text(resource)
    squashed = _squash(text)
    problems += [f"quote not in the collected code or config: {q[:80]!r}" for q in quotes if _squash(q) not in squashed]
    problems += [f"{kind[:-1]} {name!r} is not in what was collected"
                 for kind in ("models", "tools") for name in verdict.get(kind) or [] if name not in text]
    return problems


def decide(resource: Resource, rules: Classification, verdict: dict | None,
           missing="no LLM verdict for this resource") -> tuple[Classification, str, dict]:
    """-> (classification, decided_by, llm_verdict) for a resource the rules are not confident about."""
    if verdict is None:
        return rules, "rules_fallback", {"rejected": [missing]}
    problems = validate(verdict, resource)
    if problems:
        return rules, "rules_fallback", {**verdict, "rejected": problems}
    return classify(resource, verdict), "llm", verdict


def ask(llm, resource: Resource) -> dict | None:
    """One LLM call that judges a single resource; None if it gave no verdict."""
    message = {"role": "user", "content": [{"text": f"Classify this resource.\n\n{context(resource)}"}]}
    response = llm.converse([message], JUDGE_PROMPT, [VERDICT_TOOL], purpose=f"classify {resource.name}")
    calls = [b["toolUse"]["input"] for b in response["output"]["message"]["content"]
             if b.get("toolUse", {}).get("name") == "record_verdict"]
    return calls[0] if calls else None
