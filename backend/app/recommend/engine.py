"""Model-fit recommendations. recommend() is pure; recommend_all() reads the registry and stores results."""
from datetime import datetime, timedelta, timezone

from app import store
from app.config import config
from app.pricing import cost_usd
from app.recommend.catalog import MODELS, TASK_PROFILES

WEIGHTS = {"capability": 40, "cost": 40, "residency": 15, "speed": 5}
KEEP_MARGIN = 3
SENSITIVE = {"pii", "confidential"}


def _monthly_cost(model, profile, requests):
    return round(cost_usd(model, profile["input_tokens"], profile["output_tokens"]) * requests, 2)


def _score(model, profile, cheapest_cost):
    m, bar = MODELS[model], profile["min_capability"]
    capability = max(0.0, (m["capability"] - bar) / (100 - bar)) * WEIGHTS["capability"]
    cost = WEIGHTS["cost"] * min(1.0, cheapest_cost / cost_usd(model, profile["input_tokens"], profile["output_tokens"]))
    residency = WEIGHTS["residency"] if m["hosting"] == "bedrock" else WEIGHTS["residency"] / 3
    speed = WEIGHTS["speed"] * m["speed"] / 3
    return round(capability + cost + residency + speed, 1)


def _meets_task(model, profile):
    m = MODELS[model]
    return m["capability"] >= profile["min_capability"] and (m["supports_tools"] or not profile["needs_tools"])


def _money(amount):
    return f"${amount:,.2f}"


def recommend(record: dict, observed: dict | None) -> dict:
    """record: an ai_registry row. observed: {"requests": n, "window_days": d} from traces, or None."""
    current = next(iter(record.get("models") or []), None)
    base = {"registry_id": record["id"], "task_type": record["task_type"], "current_model": current}
    if record["category"] == "predictive_ml":
        return base | {
            "recommended_model": current, "current_score": None, "recommended_score": None,
            "est_monthly_cost_current": None, "est_monthly_cost_recommended": None, "alternatives": [],
            "rationale": "Keep the current model: a classical ML model is cheaper and more accurate than an LLM "
                         "for tabular prediction.",
        }

    task = record["task_type"]
    profile = TASK_PROFILES[task]
    if observed and observed["requests"]:
        requests = round(observed["requests"] * 30 / observed["window_days"])
        volume = f"{requests:,} requests/month observed in traces"
    else:
        requests = config.default_monthly_requests
        volume = f"the default {requests:,} requests/month (no traces)"

    sensitive = record.get("data_classification") in SENSITIVE
    eligible = [m for m in MODELS if _meets_task(m, profile) and (MODELS[m]["hosting"] == "bedrock" or not sensitive)]
    cheapest = min(cost_usd(m, profile["input_tokens"], profile["output_tokens"]) for m in eligible)
    scores = sorted(((_score(m, profile, cheapest), m) for m in eligible), reverse=True)
    best_score, best = scores[0]

    known = current in MODELS
    current_score = _score(current, profile, cheapest) if known else None
    current_cost = _monthly_cost(current, profile, requests) if known else None
    best_cost = _monthly_cost(best, profile, requests)

    if not known:
        recommended = best
        rationale = f"No catalogued model was detected; {best} is the best fit for {task}"
    elif sensitive and MODELS[current]["hosting"] == "external_api":
        recommended = best
        rationale = (f"Switch from {current} to {best} so {record['data_classification']} data stays in AWS: "
                     f"{current} is an external API, and {best} runs on Bedrock at {_money(best_cost)}/mo "
                     f"instead of {_money(current_cost)}/mo")
    elif not _meets_task(current, profile):
        recommended = best
        why = (f"capability {MODELS[current]['capability']} is below the bar of {profile['min_capability']}"
               if MODELS[current]["capability"] < profile["min_capability"] else "it does not support tool use")
        rationale = f"Upgrade to {best}: {current} is not fit for {task} because {why}"
    elif current == best or best_score - current_score <= KEEP_MARGIN:
        recommended = current
        rival_score, rival = next((s, m) for s, m in scores if m != current)
        rationale = (f"Keep {current}: it scores {current_score} for {task} against {rival_score} for the next best, "
                     f"{rival}, at {_money(current_cost)}/mo")
    elif best_cost < current_cost:
        recommended = best
        rationale = (f"Downgrade to {best}: it meets the bar for {task} at {_money(best_cost)}/mo "
                     f"instead of {_money(current_cost)}/mo ({best_score} vs {current_score} points)")
    else:
        recommended = best
        rationale = f"Switch to {best}: it scores {best_score} vs {current_score} for {task}, at {_money(best_cost)}/mo"

    recommended_score = current_score if recommended == current else best_score
    alternatives = [{"model": m, "score": s, "est_monthly_cost": _monthly_cost(m, profile, requests)}
                    for s, m in scores if m != recommended][:3]
    return base | {
        "recommended_model": recommended, "current_score": current_score, "recommended_score": recommended_score,
        "est_monthly_cost_current": current_cost, "est_monthly_cost_recommended": _monthly_cost(recommended, profile, requests),
        "rationale": f"{rationale}. Volume: {volume}.", "alternatives": alternatives,
    }


def recommend_all(conn, run_id) -> list[tuple[str, dict]]:
    window_days = config.log_lookback_hours / 24
    since = datetime.now(timezone.utc) - timedelta(hours=config.log_lookback_hours)
    requests = store.observed_requests(conn, since)
    results = []
    for record in store.active_registry(conn):
        observed = {"requests": requests[record["id"]], "window_days": window_days} if record["id"] in requests else None
        rec = recommend(record, observed)
        store.upsert_recommendation(conn, run_id, rec)
        results.append((record["name"], rec))
    conn.commit()
    return results
