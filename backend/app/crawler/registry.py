"""Turns a classified Resource into an ISO/IEC 42001 AI-registry record. Pure: no DB or AWS calls."""
import hashlib
import json
import re
from dataclasses import asdict

from app.crawler.classifier import Classification
from app.crawler.collectors import Resource

PEOPLE_TERMS = re.compile(r"\b(hr|hiring|employees?|leave|loans?|credit|underwrit\w*|eligibility)\b", re.I)
ENVIRONMENTS = {"dev", "staging", "prod"}
DATA_CLASSES = {"public", "internal", "confidential", "pii"}
PURPOSES = {
    "conversational": "Conversational assistant",
    "summarization": "Summarises documents",
    "classification": "Classifies incoming items into fixed labels",
    "rag": "Answers questions from a document knowledge base",
    "agentic_tool_use": "Agent that completes tasks by calling tools",
    "tabular_prediction": "Scores records with a trained ML model",
    "decision_support": "Supports decisions in a business workflow",
    "content_generation": "Generates content",
}

# ref -> (title, applies(record), why)
CONTROLS = {
    "A.3.2": ("AI roles and responsibilities", lambda r: True,
              "every AI system needs an accountable owner"),
    "A.6.2.7": ("AI system technical documentation", lambda r: True,
                "every AI system needs up-to-date technical documentation"),
    "A.5.2": ("AI system impact assessment process", lambda r: _needs_impact(r),
              "impact assessment is required or recommended, or it decides about people"),
    "A.5.4": ("Assessing AI system impact on individuals or groups of individuals", lambda r: _needs_impact(r),
              "impact assessment is required or recommended, or it decides about people"),
    "A.6.2.6": ("AI system operation and monitoring", lambda r: r["is_agent"],
                "agents act on their own and must be monitored in operation"),
    "A.6.2.8": ("AI system recording of event logs", lambda r: r["is_agent"],
                "agent actions must be traceable through event logs"),
    "A.7.5": ("Data provenance", lambda r: r["data_classification"] in ("pii", "confidential"),
              "it processes pii or confidential data"),
    "A.4.3": ("Data resources", lambda r: bool(r["data_sources"]),
              "it reads or writes data sources"),
    "A.4.4": ("Tooling resources", lambda r: bool(r["tools"]),
              "it calls tools"),
    "A.10.3": ("Suppliers", lambda r: _external(r),
               "it sends data to a model provider outside AWS"),
    "A.9.2": ("Processes for responsible use of AI systems", lambda r: r["customer_facing"] is True,
              "customers interact with it directly"),
    "A.9.4": ("Intended use of the AI system", lambda r: r["customer_facing"] is True,
              "customers interact with it directly"),
}


def _needs_impact(record):
    return record["impact_assessment_status"] in ("required", "recommended") or record["decisions_about_people"]


def _external(record):
    return "/api" in (record["provider"] or "")


def registry_id(resource: Resource) -> str:
    key = f"{resource.service}|{resource.account_id}|{resource.region}|{resource.name}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _purpose_text(resource: Resource) -> str:
    cfg = resource.config
    return cfg.get("description") or cfg.get("instruction") or cfg.get("definition", {}).get("Comment") or ""


def decisions_about_people(resource: Resource, classification: Classification) -> bool:
    if not classification.is_ai:
        return False
    cfg = resource.config
    text = " ".join([cfg.get("description") or "", cfg.get("instruction") or "", json.dumps(cfg.get("definition", {}))])
    return bool(PEOPLE_TERMS.search(text))


def assess_risk(record: dict) -> tuple[int, str, list[str]]:
    side_effects = [t["name"] for t in record["tools"] if t["side_effect"]]
    rules = [
        (record["is_agent"], 2, "agent"),
        (side_effects, 2, f"side-effect tools: {', '.join(side_effects)}"),
        (record["human_oversight"] == "none", 2, "no human oversight"),
        (record["data_classification"] == "pii", 2, "processes pii"),
        (record["data_classification"] == "confidential", 1, "processes confidential data"),
        (record["decisions_about_people"], 2, "makes decisions about people"),
        (record["customer_facing"] is True, 1, "customer-facing"),
        (_external(record), 1, f"external model provider: {record['provider']}"),
        (record["environment"] == "prod", 1, "runs in prod"),
        (record["is_agent"] and not record["log_group"], 1, "agent with no log group"),
    ]
    score = sum(points for applies, points, _ in rules if applies)
    factors = [factor for applies, _, factor in rules if applies]
    level = "high" if score >= 7 else "medium" if score >= 4 else "low"
    return score, level, factors


def autonomy_level(record: dict) -> str:
    gated = record["human_oversight"] == "approval_gate"
    if record["category"] == "predictive_ml":
        return "automated_scoring"
    if (record["is_agent"] or record["category"] == "ai_workflow") and gated:
        return "supervised"
    if record["is_agent"] and any(t["side_effect"] for t in record["tools"]):
        return "autonomous"
    return "assistive"


def controls_for(record: dict) -> list[dict]:
    return [{"ref": ref, "title": title, "why": why} for ref, (title, applies, why) in CONTROLS.items() if applies(record)]


def build_record(resource: Resource, classification: Classification, run_id: int) -> dict:
    r, c = resource, classification
    customer_facing = {"true": True, "false": False}.get(r.tags.get("customer-facing", "").lower())
    record = {
        "id": registry_id(r),
        "resource_arn": r.arn,
        "name": r.name,
        "service": r.service,
        "account_id": r.account_id,
        "region": r.region,
        "category": c.category,
        "is_agent": c.is_agent,
        "agent_score": c.agent_score,
        "evidence": [asdict(e) for e in c.evidence],
        "models": c.models,
        "provider": c.provider,
        "frameworks": c.frameworks,
        "tools": c.tools,
        "data_sources": c.data_sources,
        "data_classification": r.tags.get("data-classification") if r.tags.get("data-classification") in DATA_CLASSES else None,
        "task_type": c.task_type,
        "purpose": _purpose_text(r) or PURPOSES.get(c.task_type, ""),
        "owner": r.tags.get("owner"),
        "environment": r.tags.get("environment") if r.tags.get("environment") in ENVIRONMENTS else "unknown",
        "customer_facing": customer_facing,
        "decisions_about_people": decisions_about_people(r, c),
        "human_oversight": c.human_oversight,
        "log_group": r.log_groups[0] if r.log_groups else None,
        "monitored": bool(r.log_groups),
        "unused_ai_permissions": c.unused_ai_permissions,
        "status": "active",
        "last_run_id": run_id,
        "tags": r.tags,
    }
    record["risk_score"], record["risk_level"], record["risk_factors"] = assess_risk(record)
    record["impact_assessment_status"] = {"high": "required", "medium": "recommended", "low": "not_required"}[record["risk_level"]]
    record["autonomy_level"] = autonomy_level(record)
    record["controls"] = controls_for(record)
    return record
