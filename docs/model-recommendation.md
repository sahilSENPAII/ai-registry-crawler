# Model-fit recommendations

`backend/app/recommend/engine.py` recommends, for every active AI system, the model that best
fits its task. Model facts (capability, speed, prices, tool support, hosting) live only in
`backend/app/recommend/catalog.py`; `pricing.cost_usd` reads its prices from there too.

**Prices and capability scores are illustrative.** They show how the engine reasons, and are
not current list prices or benchmark results. Check them before acting on a recommendation.

## Task profiles

Each `task_type` has a capability bar, a typical request size and whether tools are required.

| Task | Min capability | Tokens in / out per request | Needs tools |
|---|---|---|---|
| conversational | 60 | 800 / 300 | no |
| summarization | 65 | 4,000 / 400 | no |
| classification | 50 | 500 / 10 | no |
| rag | 70 | 3,000 / 500 | no |
| agentic_tool_use | 75 | 6,000 / 1,200 | yes |
| decision_support | 80 | 2,500 / 800 | no |
| content_generation | 65 | 1,000 / 800 | no |

Predictive ML systems (`tabular_prediction`) are not compared with LLMs: the recommendation is
always to keep the current model, because a classical model is cheaper and better at tabular
prediction.

## Eligibility (hard rules, not scores)

A model is eligible only if:

1. its capability is at or above the task's bar,
2. it supports tool use when the task needs tools, and
3. when the system's data is `pii` or `confidential`, it is hosted on Bedrock, so the data stays
   in AWS. External APIs are never recommended for that data.

## Score (out of 100)

| Part | Weight | How |
|---|---|---|
| Capability | 40 | Scaled from 0 at the task bar to 40 at capability 100 |
| Cost | 40 | The cheapest eligible model gets 40; others get 40 x cheapest / their cost |
| Residency | 15 | Bedrock (in AWS) 15, external API 5 |
| Speed | 5 | Speed tier 1-3, scaled to 5 |

The current model is scored the same way. Weights are in `WEIGHTS` at the top of the engine.

## Monthly volume

- If the system has traces in `agent_traces`, the number of traced runs in the lookback window
  (`LOG_LOOKBACK_HOURS`) is scaled to 30 days.
- Otherwise the engine uses `DEFAULT_MONTHLY_REQUESTS` (default 10,000).

The rationale says which one was used. Monthly cost = requests x the task's typical tokens x the
model's prices.

## Rationale

| Case | When | Example |
|---|---|---|
| Residency switch | Current model is an external API and the data is pii/confidential | "Switch from gpt-4o to ... so confidential data stays in AWS" |
| Upgrade | Current model is below the task bar or lacks needed tool support | "Upgrade to ...: ... is not fit for agentic_tool_use because capability 55 is below the bar of 75" |
| Keep | Current model is the best, or within 3 points of it | "Keep ...: it scores 74.3 for decision_support against 58.3 for the next best" |
| Downgrade | A cheaper eligible model scores more than 3 points higher | "Downgrade to ...: it meets the bar for rag at $25.20/mo instead of $165.00/mo" |
| Switch | A more expensive model scores more than 3 points higher | "Switch to ...: it scores ... vs ..." |

Each recommendation also lists the top 3 alternatives with their scores and monthly cost.
