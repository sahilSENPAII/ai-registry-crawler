"""Deterministic, rule-based classification of collected Resources. Pure: no AWS or LLM calls.
An accepted LLM verdict can override the AI and agent decision; every other field still comes from the rules."""
import json
import re
from dataclasses import dataclass, field

from app.crawler.collectors import Resource

AGENT_WEIGHTS = {
    "managed_agent": 1.00,
    "tool_definitions": 0.35,
    "model_loop": 0.30,
    "agent_framework": 0.30,
    "memory": 0.15,
    "iteration_budget": 0.10,
    "tool_choice_auto": 0.05,
}
AGENT_THRESHOLD = 0.6

MODEL_CALLS = {
    "bedrock converse": r"\.converse\(",
    "bedrock invoke_model": r"\.invoke_model\(",
    "bedrock retrieve_and_generate": r"\.retrieve_and_generate\(",
    "openai chat.completions": r"chat\.completions\.create\(",
    "anthropic messages": r"\.messages\.create\(",
}
AI_SDK = re.compile(r"^\s*(?:from|import)\s+(openai|anthropic|langchain\w*|langgraph|crewai|autogen|llama_index)\b"
                    r"|boto3\.client\(\s*[\"'](bedrock-runtime|bedrock-agent-runtime)[\"']", re.M)
AGENT_FRAMEWORKS = {"crewai", "langchain", "langgraph", "autogen"}
BEDROCK_VENDORS = {"anthropic", "amazon", "meta", "mistral", "cohere", "ai21", "stability"}
ML_IMAGE = re.compile(r"xgboost|pytorch|tensorflow|sklearn|scikit|mxnet|huggingface|djl|tgi|lmi|triton")
LLM_IMAGE = re.compile(r"tgi|lmi|text-generation")
SFN_AI = re.compile(r"arn:aws:states:::(bedrock:invokeModel|sagemaker:\w+)")
AI_ACTION = re.compile(r"^(bedrock[\w-]*|comprehend|rekognition|textract|transcribe|translate|polly|lex|kendra):"
                       r"|^sagemaker:InvokeEndpoint")
MODEL_ID = re.compile(r"\b(?:(?:us|eu|apac)\.)?(?:anthropic|amazon|meta|mistral|cohere|ai21|stability)\.[\w.:-]+[\w]"
                      r"|\b(?:gpt-[\w.-]*\w|o[134](?:-mini)?|claude-[\w.-]*\w)\b")
WRITE_VERBS = {"issue", "send", "create", "update", "delete", "submit", "export", "write", "refund", "pay", "approve"}

TOOL_DEFINITIONS = re.compile(r"\btoolConfig\s*=|\btools\s*=")
MODEL_LOOP = re.compile(r"while\b[\s\S]{0,400}?(tool_use|tool_calls|stopReason|stop_reason)")
ITERATION_BUDGET = re.compile(r"\bMAX_(?:ITERATIONS|STEPS|TURNS)\b")
TOOL_CHOICE_AUTO = re.compile(r"tool_choice\s*=\s*[\"']auto[\"']")
TOOL_NAMES = re.compile(r"[\"'](?:toolSpec|function)[\"']:\s*\{\s*[\"']name[\"']:\s*[\"'](\w+)[\"']")
MEMORY_ENV = re.compile(r"SESSION_TABLE|MEMORY|CHAT_HISTORY")


@dataclass
class Evidence:
    signal: str
    weight: float
    detail: str


@dataclass
class Classification:
    is_ai: bool
    is_agent: bool
    agent_score: float
    category: str | None
    evidence: list[Evidence] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    provider: str | None = None
    frameworks: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    human_oversight: str = "none"
    task_type: str | None = None
    unused_ai_permissions: list[str] = field(default_factory=list)


def provider_for_model(model_id: str) -> tuple[str, str]:
    """(provider, hosting): 'anthropic.claude-…' -> ('anthropic', 'bedrock'), 'gpt-4o' -> ('openai', 'api')."""
    base = re.sub(r"^(us|eu|apac)\.", "", model_id)
    vendor, dot, _ = base.partition(".")
    if dot and vendor in BEDROCK_VENDORS:
        return vendor, "bedrock"
    if base.startswith("claude"):
        return "anthropic", "api"
    if base.startswith(("gpt", "o1", "o3", "o4")):
        return "openai", "api"
    return "unknown", "unknown"


def _unique(items):
    return list(dict.fromkeys(i for i in items if i))


def classify(resource: Resource, verdict: dict | None = None) -> Classification:
    r = resource
    code = "\n".join(r.code.values())
    definition = json.dumps(r.config.get("definition", {}))
    images = [m["image"] for m in r.config.get("models", [])] + [c["image"] for c in r.config.get("containers", [])]
    ai, agent = [], []

    def add(bucket, signal, detail):
        bucket.append(Evidence(signal, AGENT_WEIGHTS.get(signal, 0.0), detail))

    # strong AI signals
    for label, pattern in MODEL_CALLS.items():
        if re.search(pattern, code):
            add(ai, "model_call", label)
    for match in AI_SDK.finditer(code):
        add(ai, "ai_sdk", match.group(1) or match.group(2))
    if r.service == "bedrock-agent":
        add(ai, "bedrock_agent", f"foundation model {r.config.get('foundation_model')}")
    ml_images = [i for i in images if r.service == "sagemaker" and ML_IMAGE.search(i)]
    for image in ml_images:
        add(ai, "ml_image", image.split("/")[-1])
    for match in SFN_AI.finditer(definition):
        add(ai, "workflow_ai_step", match.group(1))
    env_framework = r.env.get("AGENT_FRAMEWORK", "").lower()
    if env_framework in AGENT_FRAMEWORKS:
        add(ai, "agent_framework_env", env_framework)
    frameworks = _unique([env_framework if env_framework in AGENT_FRAMEWORKS else None] +
                         [m.group(1) for m in AI_SDK.finditer(code) if m.group(1) in AGENT_FRAMEWORKS])
    is_ai = bool(ai)

    # agent signals
    if r.service == "bedrock-agent":
        add(agent, "managed_agent", "Bedrock agent")
    tool_sources = [label for label, found in [("code", TOOL_DEFINITIONS.search(code)), ("TOOLS env", "TOOLS" in r.env),
                                                ("ai:declared-tools tag", "ai:declared-tools" in r.tags)] if found]
    if tool_sources:
        add(agent, "tool_definitions", ", ".join(tool_sources))
    if MODEL_LOOP.search(code):
        add(agent, "model_loop", "loop driven by tool_use / tool_calls")
    if frameworks:
        add(agent, "agent_framework", ", ".join(frameworks))
    memory = [k for k in r.env if MEMORY_ENV.search(k)]
    if memory:
        add(agent, "memory", ", ".join(memory))
    if ITERATION_BUDGET.search(code) or any(ITERATION_BUDGET.search(k) for k in r.env):
        add(agent, "iteration_budget", "MAX_ITERATIONS-style limit")
    if TOOL_CHOICE_AUTO.search(code):
        add(agent, "tool_choice_auto", 'tool_choice="auto"')
    agent_score = round(min(1.0, sum((e.weight for e in agent), 0.0)), 3)
    is_agent = is_ai and agent_score >= AGENT_THRESHOLD
    if verdict:
        is_ai, is_agent = verdict["is_ai"], verdict["is_ai"] and verdict["is_agent"]
        ai += [Evidence("llm_quote", 0.0, q) for q in verdict.get("evidence_quotes", [])]

    ai_actions = [a for a in r.iam_actions if AI_ACTION.search(a)]
    # shortcut: AI resources report no unused AI permissions; upgrade by mapping each action to the calls that use it
    unused = [] if is_ai else ai_actions
    weak = [Evidence("iam_permission_only", 0.0, a) for a in unused]
    if not is_ai:
        return Classification(False, False, agent_score, None, evidence=weak, unused_ai_permissions=unused)

    llm_image = any(LLM_IMAGE.search(i) for i in ml_images)
    if verdict:
        category = verdict["category"]
    elif is_agent:
        category = "ai_agent"
    elif r.service == "stepfunctions":
        category = "ai_workflow"
    elif r.service == "sagemaker" and not llm_image:
        category = "predictive_ml"
    else:
        category = "genai_application"

    if r.service == "sagemaker":
        models = _unique(m["name"] for m in r.config["models"])
        provider = "sagemaker"
        frameworks = _unique(ML_IMAGE.search(i).group(0) for i in ml_images)
    else:
        texts = [code, *r.env.values(), str(r.config.get("foundation_model", "")), definition]
        models = _unique([m for text in texts for m in MODEL_ID.findall(text)] + (verdict or {}).get("models", []))
        provider = ", ".join(_unique(f"{p}/{h}" for p, h in map(provider_for_model, models))) or None

    # tools
    names = TOOL_NAMES.findall(code) + r.env.get("TOOLS", "").split(",") + \
        re.split(r"[\s,]+", r.tags.get("ai:declared-tools", "")) + (verdict or {}).get("tools", [])
    tools = [{"name": n, "side_effect": n.split("_")[0] in WRITE_VERBS} for n in _unique(t.strip() for t in names)]

    # data sources
    sources = []
    for key, value in r.env.items():
        if key.endswith("_TABLE"):
            sources.append(f"dynamodb:{value}")
        elif key.endswith("_BUCKET"):
            sources.append(f"s3:{value}")
        elif key == "KNOWLEDGE_BASE_ID":
            sources.append(f"knowledge_base:{value}")
        elif key == "DB_HOST" and "rds" in value:
            sources.append(f"rds:{value}")
        elif key.startswith("CRM"):
            sources.append(f"crm:{value}")
    if "ai:knowledge-base" in r.tags:
        sources.append(f"knowledge_base:{r.tags['ai:knowledge-base']}")
    if re.search(r"s3\.get_object\(", code):
        sources.append("s3")
    sources += [f"dynamodb:{t}" for t in re.findall(r'"TableName": "([\w-]+)"', definition)]

    oversight = "approval_gate" if ("waitForTaskToken" in definition or "APPROVAL_THRESHOLD" in r.env
                                    or "human_approval" in code) else "none"

    lowered = code.lower()
    if is_agent:
        task_type = "agentic_tool_use"
    elif category == "ai_workflow":
        task_type = "decision_support"
    elif category == "predictive_ml":
        task_type = "tabular_prediction"
    elif "retrieve_and_generate" in code or "KNOWLEDGE_BASE_ID" in r.env:
        task_type = "rag"
    elif "classif" in lowered:
        task_type = "classification"
    elif "summar" in lowered:
        task_type = "summarization"
    elif re.search(r"history|conversation", lowered):
        task_type = "conversational"
    else:
        task_type = "content_generation"

    return Classification(
        is_ai=True, is_agent=is_agent, agent_score=agent_score, category=category, evidence=ai + agent,
        models=models, provider=provider, frameworks=frameworks, tools=tools, data_sources=_unique(sources),
        human_oversight=oversight, task_type=task_type, unused_ai_permissions=unused,
    )
