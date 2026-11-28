"""Scores rules, LLM and hybrid classification against labelled resources, for `cli evaluate`."""
from datetime import date
from pathlib import Path

from app import aws
from app.crawler.classifier import classify
from app.crawler.collectors import collect_all
from app.crawler.hybrid import ask, confident, decide, validate
from app.pricing import PROFILE_PREFIX
from app.seed.expected import EXPECTED
from app.seed.variants import VARIANTS

METHODS = ["rules", "llm", "hybrid"]
CLASSES = ["not AI", "AI, not agent", "agent"]
REPORT = Path(__file__).resolve().parents[2] / "docs" / "evaluation.md"
MIN_AGENT_F1_GAIN = 0.05
WHERE = {
    "bedrock": "The models were called through Amazon Bedrock, so nothing left AWS.",
    "gemini": "The models were called through the Gemini API, outside AWS. Bedrock stays the crawler's default so that "
              "a real account's code and config never leave AWS, but the AWS test account could not call any Bedrock "
              "model when this evaluation was written: Anthropic models were \"not available for this account\" and "
              "every Nova quota (requests and tokens per minute, tokens per day) was 0.",
    "groq": "The models were called through Groq's API, outside AWS. Bedrock stays the crawler's default so that a "
            "real account's code and config never leave AWS. This evaluation ran elsewhere because of where models "
            "could be called when it was written: on Bedrock, the AWS test account got \"not available for this "
            "account\" for Anthropic models and had every Nova quota (requests and tokens per minute, tokens per day) "
            "at 0; on the Gemini API free tier, Flash allowed 20 requests a day against the 41 one model needs, and "
            "Pro allowed none. The Bedrock and Gemini clients stay in the code. On Groq, the Llama models "
            "(llama-3.3-70b-versatile, llama-3.1-8b-instant) returned model_not_found for this key, so gpt-oss-20b is "
            "the small, fast model and gpt-oss-120b stands in for a 70B-class one.",
}


def dataset():
    """The simulated estate with its ground truth, then the hard variants."""
    with aws.simulated_aws():
        resources, _ = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    estate = [(r, (EXPECTED[r.name]["is_ai"], EXPECTED[r.name]["is_agent"]), "simulated estate") for r in resources]
    return estate + VARIANTS


def scores(pairs) -> dict:
    """pairs of (truth, predicted) booleans."""
    tp = sum(t and p for t, p in pairs)
    fp = sum(p and not t for t, p in pairs)
    fn = sum(t and not p for t, p in pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": len(pairs) - tp - fp - fn,
            "precision": precision, "recall": recall, "f1": f1}


def _class(prediction):
    is_ai, is_agent = prediction
    return 2 if is_agent else int(is_ai)


def evaluate(llm) -> dict:
    rows = []
    for resource, truth, why in dataset():
        rules = classify(resource)
        before = (llm.calls, llm.input_tokens, llm.output_tokens, llm.cost_usd)
        verdict = ask(llm, resource)
        usage = (llm.calls - before[0], llm.input_tokens - before[1], llm.output_tokens - before[2],
                 llm.cost_usd - before[3])
        is_ai = bool(verdict and verdict.get("is_ai") is True)
        sure = confident(resource, rules)
        if sure:
            hybrid, decided_by, rejected = rules, "rules", []
        else:
            hybrid, decided_by, stored = decide(resource, rules, verdict)
            rejected = stored.get("rejected", [])
        rows.append({
            "name": resource.name, "why": why, "truth": truth, "confident": sure, "usage": usage,
            "rules": (rules.is_ai, rules.is_agent),
            "llm": (is_ai, is_ai and verdict.get("is_agent") is True),
            "hybrid": (hybrid.is_ai, hybrid.is_agent), "decided_by": decided_by, "rejected": rejected,
            "llm_valid": bool(verdict) and not validate(verdict, resource),
            "reasoning": (verdict or {}).get("reasoning", ""),
        })

    metrics = {m: {label: scores([(r["truth"][i], r[m][i]) for r in rows]) for i, label in enumerate(["is_ai", "is_agent"])}
               for m in METHODS}
    confusion = {m: [[sum(_class(r["truth"]) == t and _class(r[m]) == p for r in rows) for p in range(3)] for t in range(3)]
                 for m in METHODS}

    def cost(selected):
        return [sum(r["usage"][i] for r in selected) for i in range(4)]

    usage = {"rules": [0, 0, 0, 0.0], "llm": cost(rows), "hybrid": cost([r for r in rows if not r["confident"]])}
    return {"model": llm.model_id, "provider": llm.provider, "rows": rows, "metrics": metrics, "confusion": confusion, "usage": usage}


def _pred(prediction):
    return CLASSES[_class(prediction)]


def method_columns(results):
    """(name, result, key) per column: the rules once, then each model alone and in the hybrid."""
    methods = [("rules", results[0], "rules")]
    for result in results:
        model = PROFILE_PREFIX.sub("", result["model"]).removeprefix("anthropic.claude-")
        methods += [(f"llm {model}", result, "llm"), (f"hybrid {model}", result, "hybrid")]
    return methods


def choice(results) -> str:
    """The first model stays the default unless another one's hybrid is clearly better at spotting agents."""
    f1 = {r["model"]: r["metrics"]["hybrid"]["is_agent"]["f1"] for r in results}
    default, best = results[0]["model"], max(f1, key=f1.get)
    return best if round(f1[best] - f1[default], 6) >= MIN_AGENT_F1_GAIN else default


def report(results, skipped=None) -> str:
    """results: one evaluate() result per model, the default model first; skipped: model -> why it could not run."""
    methods = method_columns(results)
    rows = results[0]["rows"]
    variants = sum(r["why"] != "simulated estate" for r in rows)
    models = ", ".join(f"`{r['model']}`" for r in results)
    lines = [
        "# Classification evaluation", "",
        f"Generated by `python -m app.cli evaluate` on {date.today().isoformat()} with {models}. "
        f"{len(rows)} resources: the {len(rows) - variants} in the simulated estate and {variants} hard variants from "
        "`backend/app/seed/variants.py`, which are generated in memory and never deployed. Each model judged every "
        "resource once, so a rerun can differ slightly.", "",
        "- **rules**: the deterministic classifier alone.",
        "- **llm**: one LLM call per resource with its masked, truncated code and config, unchecked.",
        "- **hybrid**: the rules when they are confident; otherwise the LLM's verdict if it passes the validator "
        "(quotes and models must appear in what was collected), else the rules.", "",
        "## Where the models ran", "",
        WHERE.get(results[0]["provider"], "The models were test fakes.") + " Only synthetic code and config were sent: "
        "the simulated estate's seeded functions and the generated variants, with secrets masked as in a real crawl. "
        "No real account's resources were sent.", "",
        "## Scores", "",
        "| Method | is_ai precision | is_ai recall | is_ai F1 | is_agent precision | is_agent recall | is_agent F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {name} | " + " | ".join(f"{result['metrics'][key][label][k]:.2f}" for label in ("is_ai", "is_agent")
                                          for k in ("precision", "recall", "f1")) + " |"
              for name, result, key in methods]

    unsure = [i for i, r in enumerate(rows) if not r["confident"]]
    lines += ["", "## Cases sent to the LLM and cost", "",
              "Rejected verdicts are the ones that fail the validator. The hybrid only asks about the "
              f"{len(unsure)} resources the rules are unsure about, so its cost is those calls; llm is unchecked, "
              "so its rejected count is what the validator would have caught.", "",
              "| Method | Cases sent to the LLM | Rejected verdicts | Input tokens | Output tokens | Cost |",
              "|---|---|---|---|---|---|"]
    for name, result, key in methods:
        calls, in_tokens, out_tokens, cost = result["usage"][key]
        picked = [] if key == "rules" else result["rows"] if key == "llm" else [result["rows"][i] for i in unsure]
        rejected = sum(not r["llm_valid"] for r in picked) if key == "llm" else sum(bool(r["rejected"]) for r in picked)
        lines.append(f"| {name} | {calls} | {rejected if picked else '-'} | {in_tokens:,} | {out_tokens:,} | ${cost:.4f} |")

    f1 = ", ".join(f"`{r['model']}` {r['metrics']['hybrid']['is_agent']['f1']:.2f}" for r in results)
    picked = choice(results)
    lines += ["", "## Model choice", "",
              f"Hybrid is_agent F1: {f1}. The default, `{results[0]['model']}`, is kept unless another model's hybrid "
              f"is_agent F1 is at least {MIN_AGENT_F1_GAIN} higher. "
              + (f"`{picked}` clears that bar." if picked != results[0]["model"] else
                 "No other model was evaluated, so the default stays." if len(results) == 1 else
                 "No model clears that bar, so the default stays.")]
    lines += [f"\n`{model}` could not be evaluated: {why}" for model, why in (skipped or {}).items()]

    lines += ["", "## Confusion", "", "Rows are the truth, columns the prediction.", "",
              "| Method | Truth | " + " | ".join(f"Predicted {c}" for c in CLASSES) + " |", "|---|---|---|---|---|"]
    for name, result, key in methods:
        lines += [f"| {name} | {CLASSES[t]} | " + " | ".join(str(n) for n in counts) + " |"
                  for t, counts in enumerate(result["confusion"][key])]

    hybrids = [(name, result) for name, result, key in methods if key == "hybrid"]
    lines += ["", "## Hybrid decisions", "",
              "| Resource | " + " | ".join(f"{name}: decided by | {name}: why the verdict was rejected" for name, _ in hybrids)
              + " |", "|---" * (1 + 2 * len(hybrids)) + "|"]
    for i in unsure:
        cells = [f"{res['rows'][i]['decided_by']} | {'; '.join(res['rows'][i]['rejected']) or '-'}" for _, res in hybrids]
        lines.append(f"| {rows[i]['name']} | " + " | ".join(cells) + " |")

    def wrong(result, key, i):
        return result["rows"][i][key] != rows[i]["truth"]

    lines += ["", "## Where a method was wrong", "",
              "Bold marks a wrong prediction.", "",
              "| Resource | Why it is hard | Truth | " + " | ".join(name for name, _, _ in methods) + " |",
              "|---" * (3 + len(methods)) + "|"]
    for i, r in enumerate(rows):
        if any(wrong(result, key, i) for _, result, key in methods):
            cells = [f"**{_pred(result['rows'][i][key])}**" if wrong(result, key, i) else _pred(result["rows"][i][key])
                     for _, result, key in methods]
            lines.append(f"| {r['name']} | {r['why']} | {_pred(r['truth'])} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
