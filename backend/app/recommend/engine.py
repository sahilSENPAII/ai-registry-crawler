"""Model-fit recommendations. recommend() is pure; recommend_all() reads the registry and stores results."""
from datetime import datetime, timedelta, timezone

from app import store
from app.config import config
from app.pricing import cost_usd
from app.recommend.catalog import MODELS, TASK_PROFILES

# high-risk systems weigh capability over cost; residency is the in-AWS score, external APIs get EXTERNAL_RESIDENCY
WEIGHTS = {
    "high": {"capability": 55, "cost": 25, "residency": 15, "speed": 5},
    "standard": {"capability": 40, "cost": 40, "residency": 15, "speed": 5},
}
EXTERNAL_RESIDENCY = 5
KEEP_MARGIN = 3
MIN_OBSERVED_MONTHLY = 1_000
SENSITIVE = {"pii", "confidential"}


def _monthly_cost(model, profile, requests):
    return round(cost_usd(model, profile["input_tokens"], profile["output_tokens"]) * requests, 2)


def _score(model, profile, cheapest_cost, weights):
    m, bar = MODELS[model], profile["min_capability"]
    capability = max(0.0, (m["capability"] - bar) / (100 - bar)) * weights["capability"]
    cost = weights["cost"] * min(1.0, cheapest_cost / cost_usd(model, profile["input_tokens"], profile["output_tokens"]))
    residency = weights["residency"] if m["hosting"] == "bedrock" else EXTERNAL_RESIDENCY
    speed = weights["speed"] * m["speed"] / 3
    return round(capability + cost + residency + speed, 1)


def _meets_task(model, profile):
    m = MODELS[model]
    return m["capability"] >= profile["min_capability"] and (m["supports_tools"] or not profile["needs_tools"])


def _money(amount):
    return f"${amount:,.2f}"


def recommend(record: dict, observed: dict | None) -> dict:
    """record: an ai_registry row. observed: {"runs", "input_tokens", "output_tokens", "window_days"} totals
    from traces, or None."""
    current = next(iter(record.get("models") or []), None)
    base = {"registry_id": record["id"], "task_type": record["task_type"], "current_model": current}
    if record["category"] == "predictive_ml":
        return base | {
            "outcome": "keep", "recommended_model": current, "current_score": None, "recommended_score": None,
            "est_monthly_cost_current": None, "est_monthly_cost_recommended": None, "alternatives": [],
            "rationale": "Keep the current model: a classical ML model is cheaper and more accurate than an LLM "
                         "for tabular prediction.",
        }

    task = record["task_type"]
    profile = TASK_PROFILES[task]
    weights = WEIGHTS["high" if record.get("risk_level") == "high" else "standard"]
    runs = observed["runs"] if observed else 0
    if runs and observed["input_tokens"] + observed["output_tokens"] > 0:
        profile = {**profile, "input_tokens": round(observed["input_tokens"] / runs),
                   "output_tokens": round(observed["output_tokens"] / runs)}
        tokens = f"token profile from {runs} observed runs"
    else:
        tokens = "typical token profile for the task"
    observed_monthly = round(runs * 30 / observed["window_days"]) if runs else 0
    if observed_monthly >= MIN_OBSERVED_MONTHLY:
        requests = observed_monthly
        volume = f"volume {requests:,}/mo observed"
    else:
        requests = config.default_monthly_requests
        why = "too little traffic observed" if runs else "no traces"
        volume = f"volume assumed {requests:,}/mo ({why})"

    sensitive = record.get("data_classification") in SENSITIVE
    eligible = [m for m in MODELS if _meets_task(m, profile) and (MODELS[m]["hosting"] == "bedrock" or not sensitive)]
    cheapest = min(cost_usd(m, profile["input_tokens"], profile["output_tokens"]) for m in eligible)
    scores = sorted(((_score(m, profile, cheapest, weights), m) for m in eligible), reverse=True)
    best_score, best = scores[0]

    known = current in MODELS
    current_score = _score(current, profile, cheapest, weights) if known else None
    current_cost = _monthly_cost(current, profile, requests) if known else None
    best_cost = _monthly_cost(best, profile, requests)

    if not known:
        recommended, outcome = best, "switch"
        rationale = f"No catalogued model was detected; {best} is the best fit for {task}"
    elif sensitive and MODELS[current]["hosting"] == "external_api":
        recommended, outcome = best, "residency"
        rationale = (f"Switch from {current} to {best} so {record['data_classification']} data stays in AWS: "
                     f"{current} is an external API, and {best} runs on Bedrock at {_money(best_cost)}/mo "
                     f"instead of {_money(current_cost)}/mo")
    elif not _meets_task(current, profile):
        recommended, outcome = best, "upgrade"
        why = (f"capability {MODELS[current]['capability']} is below the bar of {profile['min_capability']}"
               if MODELS[current]["capability"] < profile["min_capability"] else "it does not support tool use")
        rationale = f"Upgrade to {best}: {current} is not fit for {task} because {why}"
    elif current == best or best_score - current_score <= KEEP_MARGIN:
        recommended, outcome = current, "keep"
        rival_score, rival = next((s, m) for s, m in scores if m != current)
        rationale = (f"Keep {current}: it scores {current_score} for {task} against {rival_score} for the next best, "
                     f"{rival}, at {_money(current_cost)}/mo")
    elif best_cost < current_cost:
        recommended, outcome = best, "downgrade"
        rationale = (f"Downgrade to {best}: it meets the bar for {task} at {_money(best_cost)}/mo "
                     f"instead of {_money(current_cost)}/mo ({best_score} vs {current_score} points)")
    else:
        recommended, outcome = best, "switch"
        rationale = f"Switch to {best}: it scores {best_score} vs {current_score} for {task}, at {_money(best_cost)}/mo"

    recommended_score = current_score if recommended == current else best_score
    alternatives = [{"model": m, "score": s, "est_monthly_cost": _monthly_cost(m, profile, requests)}
                    for s, m in scores if m != recommended][:3]
    return base | {
        "outcome": outcome, "recommended_model": recommended,
        "current_score": current_score, "recommended_score": recommended_score,
        "est_monthly_cost_current": current_cost,
        "est_monthly_cost_recommended": _monthly_cost(recommended, profile, requests),
        "rationale": f"{rationale}. Based on {tokens}; {volume}.", "alternatives": alternatives,
    }


def recommend_all(conn, run_id) -> list[tuple[str, dict]]:
    window_days = config.log_lookback_hours / 24
    since = datetime.now(timezone.utc) - timedelta(hours=config.log_lookback_hours)
    usage = store.observed_usage(conn, since)
    results = []
    for record in store.active_registry(conn):
        observed = {**usage[record["id"]], "window_days": window_days} if record["id"] in usage else None
        rec = recommend(record, observed)
        store.upsert_recommendation(conn, run_id, rec)
        results.append((record["name"], rec))
    conn.commit()
    return results
