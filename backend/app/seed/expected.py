"""Ground truth for the simulated estate. Only tests and the evaluation read this; the crawler must never import it."""

AGENT = {"is_ai": True, "is_agent": True, "category": "ai_agent"}
GENAI = {"is_ai": True, "is_agent": False, "category": "genai_application"}
WORKFLOW = {"is_ai": True, "is_agent": False, "category": "ai_workflow"}
PREDICTIVE = {"is_ai": True, "is_agent": False, "category": "predictive_ml"}
NOT_AI = {"is_ai": False, "is_agent": False, "category": None}

EXPECTED = {
    "refund-agent": AGENT,
    "sales-research-agent": AGENT,
    "hr-policy-assistant": AGENT,
    "procurement-agent": AGENT,
    "support-chatbot": GENAI,
    "invoice-summarizer": GENAI,
    "contract-review-rag": GENAI,
    "ticket-classifier": GENAI,
    "loan-underwriting-workflow": WORKFLOW,
    "churn-predictor": PREDICTIVE,
    "fraud-scoring": PREDICTIVE,
    "image-thumbnailer": NOT_AI,
    "nightly-billing-export": NOT_AI,
    "order-fulfilment": NOT_AI,
    "web-storefront": NOT_AI,
}

# Planted problems in the agent logs. Case g (approved $25k purchase order) is a control and is not listed.
EXPECTED_FINDINGS = [
    {"case": "a", "rule": "side_effect_without_approval", "agent": "refund-agent"},
    {"case": "b", "rule": "approval_denied", "agent": "refund-agent"},
    {"case": "c", "rule": "undeclared_tool", "agent": "sales-research-agent"},
    {"case": "d", "rule": "tool_loop", "agent": "sales-research-agent"},
    {"case": "e", "rule": "run_error", "agent": "sales-research-agent"},
    {"case": "f", "rule": "token_spike", "agent": "procurement-agent"},
    {"case": "h", "rule": "unregistered_agent", "agent": "marketing-copy-agent"},
]
