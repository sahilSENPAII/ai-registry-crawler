import pytest

from app.config import config
from app.recommend.catalog import MODELS, TASK_PROFILES
from app.recommend.engine import recommend

ESTATE_MODELS = ["anthropic.claude-3-5-sonnet-20240620-v1:0", "anthropic.claude-3-sonnet-20240229-v1:0",
                 "anthropic.claude-3-haiku-20240307-v1:0", "gpt-4o", "gpt-4o-mini"]


def record(task="conversational", model="anthropic.claude-3-haiku-20240307-v1:0", data="internal",
           category="genai_application"):
    return {"id": "r1", "category": category, "task_type": task, "models": [model], "data_classification": data}


def test_catalog_covers_estate_models():
    assert set(ESTATE_MODELS) <= MODELS.keys()


@pytest.mark.parametrize("task", sorted(TASK_PROFILES))
@pytest.mark.parametrize("data", ["public", "internal", "confidential", "pii", None])
def test_recommendation_is_eligible(task, data):
    for current in ESTATE_MODELS:
        rec = recommend(record(task, current, data), None)
        m, profile = MODELS[rec["recommended_model"]], TASK_PROFILES[task]
        assert m["capability"] >= profile["min_capability"]
        assert m["supports_tools"] or not profile["needs_tools"]
        if data in ("pii", "confidential"):
            assert m["hosting"] == "bedrock"
            assert all(MODELS[a["model"]]["hosting"] == "bedrock" for a in rec["alternatives"])


def test_predictive_ml_keeps_current():
    rec = recommend(record("tabular_prediction", "churn-predictor", category="predictive_ml"), None)
    assert rec["recommended_model"] == rec["current_model"] == "churn-predictor"
    assert rec["rationale"].startswith("Keep") and "tabular prediction" in rec["rationale"]


def test_observed_volume_beats_default():
    default = recommend(record(), None)
    observed = recommend(record(), {"requests": 300, "window_days": 15})
    assert "600 requests/month observed" in observed["rationale"]
    assert f"default {config.default_monthly_requests:,}" in default["rationale"]
    assert observed["est_monthly_cost_current"] == pytest.approx(
        default["est_monthly_cost_current"] * 600 / config.default_monthly_requests, abs=0.01)


def test_keep():
    best = recommend(record(), None)["recommended_model"]
    rec = recommend(record(model=best), None)
    assert rec["recommended_model"] == best
    assert rec["rationale"].startswith(f"Keep {best}")
    assert rec["est_monthly_cost_current"] == rec["est_monthly_cost_recommended"]


def test_downgrade():
    rec = recommend(record(model="anthropic.claude-3-opus-20240229-v1:0"), None)
    assert rec["rationale"].startswith("Downgrade")
    assert f"instead of ${rec['est_monthly_cost_current']:,.2f}/mo" in rec["rationale"]
    assert rec["est_monthly_cost_recommended"] < rec["est_monthly_cost_current"]


def test_upgrade():
    rec = recommend(record("agentic_tool_use", "meta.llama3-1-8b-instruct-v1:0"), None)
    assert rec["rationale"].startswith("Upgrade")
    assert "below the bar of 75" in rec["rationale"]
    assert MODELS[rec["recommended_model"]]["capability"] >= 75


def test_residency_switch():
    rec = recommend(record(model="gpt-4o", data="pii"), None)
    assert rec["rationale"].startswith("Switch from gpt-4o")
    assert "pii data stays in AWS" in rec["rationale"]
    assert MODELS[rec["recommended_model"]]["hosting"] == "bedrock"


def test_alternatives_are_top_three_others():
    rec = recommend(record(), None)
    scores = [a["score"] for a in rec["alternatives"]]
    assert len(scores) == 3 and scores == sorted(scores, reverse=True)
    assert rec["recommended_model"] not in [a["model"] for a in rec["alternatives"]]
