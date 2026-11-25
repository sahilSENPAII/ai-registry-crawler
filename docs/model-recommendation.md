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
| agentic_tool_use | 85 | 6,000 / 1,200 | yes |
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

The agentic bar is high on purpose: only strong tool-using models qualify (in this catalog,
Llama 3.1 70B at 78 and Claude 3 Sonnet at 80 fall below it).

## Score (out of 100)

Weights depend on the system's risk level: high-risk systems weigh capability over cost.

| Part | High risk | Medium / low risk | How |
|---|---|---|---|
| Capability | 55 | 40 | Scaled from 0 at the task bar to the full weight at capability 100 |
| Cost | 25 | 40 | The cheapest eligible model gets the full weight; others get weight x cheapest / their cost |
| Residency | 15 | 15 | Bedrock (in AWS) gets 15; an external API gets 5 |
| Speed | 5 | 5 | Speed tier 1-3, scaled to 5 |

The current model is scored the same way. Both weight sets are in `WEIGHTS` at the top of the engine.

## Token profile and monthly volume

- **Token profile.** If the system has traces in `agent_traces`, the per-request input and output
  tokens are the averages per traced run. Otherwise the task profile's typical tokens are used.
- **Volume.** Traced runs in the lookback window (`LOG_LOOKBACK_HOURS`) are scaled to 30 days. That
  figure is used only if it reaches 1,000 requests a month; below that, or with no traces, the
  engine assumes `DEFAULT_MONTHLY_REQUESTS` (default 10,000).

The rationale says which applied, for example "token profile from 18 observed runs; volume
assumed 10,000/mo (too little traffic observed)". Monthly cost = requests x tokens per request x
the model's prices.

## Rationale

Each recommendation stores its case in `outcome`, so the dashboard never parses the rationale.

| Case (`outcome`) | When | Example |
|---|---|---|
| Residency switch (`residency`) | Current model is an external API and the data is pii/confidential | "Switch from gpt-4o to ... so confidential data stays in AWS" |
| Upgrade (`upgrade`) | Current model is below the task bar or lacks needed tool support | "Upgrade to ...: ... is not fit for agentic_tool_use because capability 80 is below the bar of 85" |
| Keep (`keep`, also every predictive model) | Current model is the best, or within 3 points of it | "Keep ...: it scores 74.3 for decision_support against 58.3 for the next best" |
| Downgrade (`downgrade`) | A cheaper eligible model scores more than 3 points higher | "Downgrade to ...: it meets the bar for rag at $25.20/mo instead of $165.00/mo" |
| Switch (`switch`) | A more expensive model scores more than 3 points higher, or no catalogued model was detected | "Switch to ...: it scores ... vs ..." |

Each recommendation also lists the top 3 alternatives with their scores and monthly cost.
