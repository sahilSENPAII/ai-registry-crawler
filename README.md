# ai-registry-crawler

Most companies cannot say which of their cloud workloads use AI, which of those are agents
that act on their own, or what those agents actually did last week. This tool crawls an AWS
account, finds every AI system, tells agents apart from plain model calls, and records each one
in a Postgres AI registry with the attributes ISO/IEC 42001 asks for. An observability agent
reads the agents' activity logs and flags risky behaviour, a recommender checks whether each
system uses the right model, and an executive dashboard puts it all on one screen.

Everything runs against a simulated AWS account (moto) by default, so it works with no cloud
credentials.

## Assignment coverage

| Requirement | Where it is implemented |
|---|---|
| Crawl AWS for AI use cases | `backend/app/crawler/collectors.py`: read-only collectors for Lambda, Bedrock agents, SageMaker, Step Functions and ECS, plus IAM and CloudWatch Logs |
| Varied use cases, including agents | `backend/app/seed/`: a simulated company account with 15 workloads (4 agents, 4 GenAI apps, 1 AI workflow, 2 predictive models, 4 non-AI decoys) |
| Tell agents from non-agents | `backend/app/crawler/classifier.py`: weighted, explainable signals from code, config, env and tags; every decision carries its evidence. With `LLM_ENABLED`, `backend/app/crawler/hybrid.py` lets an LLM judge the cases the rules are unsure about, checked against the evidence; scored in [docs/evaluation.md](docs/evaluation.md) |
| Capture AI-registry attributes (ISO/IEC 42001 AIMS) | `backend/app/crawler/registry.py`: owner, purpose, data, models, tools, oversight, autonomy, risk, impact assessment and Annex A controls; mapped in [docs/registry-fields.md](docs/registry-fields.md) |
| Store them in Postgres | `backend/app/schema.sql`, `backend/app/store.py`; tables described in [docs/data-model.md](docs/data-model.md) |
| Observability agent tracing agent behaviour, in a separate table | `backend/app/observability/agent.py` writes spans to `agent_traces` and findings to `agent_anomalies`; see [docs/observability-attributes.md](docs/observability-attributes.md) |
| Executive dashboard | `frontend/`: Next.js app with Overview, AI inventory, Agent activity, Issues and Model fit |
| Model recommendation (good to have) | `backend/app/recommend/`: catalog plus scoring engine; see [docs/model-recommendation.md](docs/model-recommendation.md) |

## Architecture

```mermaid
flowchart LR
    subgraph aws ["AWS account, simulated by moto or real"]
        res["Lambda, Bedrock agents, SageMaker, Step Functions, ECS, IAM"]
        logs["CloudWatch Logs under /ai/agents/"]
    end
    res --> col[Collectors] --> cls[Classifier] --> reg[Registry builder] --> pg[(Postgres)]
    logs --> obs[Observability agent] --> pg
    pg --> rec[Model recommender] --> pg
    pg --> api[FastAPI] --> ui[Next.js dashboard]
```

- **Collectors** list each service's workloads with read-only calls and return one common shape: tags, env, config, code, IAM actions and log groups.
- **Classifier** decides whether a workload is AI, whether it is an agent and which category it is, using rules only.
- **Registry builder** turns a classified workload into a registry record with a risk score, autonomy level and the applicable ISO/IEC 42001 controls.
- **Crawler agent** runs collect, classify and register, retires systems that disappeared, and traces its own steps. With `LLM_ENABLED=true` an LLM plans the crawl in a tool-use loop, with the rules as guardrails; see [The LLM crawler agent](#the-llm-crawler-agent).
- **Observability agent** reads span logs, links them to registry entries, stores them and runs seven behaviour rules, plus four rules over the crawler's own spans.
- **Model recommender** scores catalogued models for each system's task and explains the best fit.
- **Pipeline** (`backend/app/pipeline.py`) runs crawl, observe and recommend in one process, under a Postgres advisory lock so runs never overlap.
- **FastAPI** serves read endpoints for the dashboard plus `POST /discovery/run`.
- **Next.js** renders the dashboard on the server from the API.

## Quick start

```bash
docker compose up --build
```

Open http://localhost:3000. On first start the API finds an empty registry and runs discovery
once against the simulated account, so the dashboard has data straight away. The API is on
http://localhost:8000 and Postgres on `localhost:5433`.

### Local development

```bash
docker compose up -d db                      # Postgres only, host port 5433

cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli discover                   # crawl, observe and recommend into the DB
uvicorn app.api:app --reload                 # API on :8000

cd ../frontend
npm install && npm run dev                   # dashboard on :3000
```

Copy `.env.example` to `.env` to see or override every setting.

### CLI

Run from `backend/` as `python -m app.cli <command>`.

| Command | What it does |
|---|---|
| `discover` | Full pipeline: crawl, observe, recommend; prints a summary |
| `crawl`, `observe`, `recommend` | The pipeline up to that step, printing that step's detail |
| `show` | The registry: category, risk, autonomy, oversight, owner, status |
| `evaluate` | Scores rules, each model in `LLM_EVAL_MODELS` and the hybrid with each, on the estate plus 26 hard variants, and writes [docs/evaluation.md](docs/evaluation.md); the only command that always calls the LLM (through `LLM_PROVIDER`) |
| `estate`, `collect`, `classify` | Inspect the simulated account, the raw collected facts or the classifier's verdicts; no DB writes |
| `init`, `reset`, `tables` | Create the schema, empty every table, show row counts |

The commands that write to the database create the schema first, so a fresh database needs no `init`.

## How it decides

**Agent or not.** A workload is AI only on a strong signal: a model call in its code, an AI SDK,
a Bedrock agent, an ML framework image on SageMaker, a workflow step that calls a model, or an
agent framework in its environment. An IAM permission alone never makes something AI. Among AI
systems, these signals add up to an agent score, capped at 1.0, and 0.6 or more is an agent:

| Signal | Weight |
|---|---|
| Managed agent (Bedrock agent) | 1.00 |
| Tool definitions (`toolConfig`, `tools=`, `TOOLS` env, declared-tools tag) | 0.35 |
| Model-driven loop (`while` on `tool_use` / `tool_calls`) | 0.30 |
| Agent framework (crewai, langchain, langgraph, autogen) | 0.30 |
| Memory (session table, `MEMORY_BACKEND`) | 0.15 |
| Iteration budget (`MAX_ITERATIONS`) | 0.10 |
| `tool_choice="auto"` | 0.05 |

**Risk.** Points for being an agent (2), side-effect tools (2), no human oversight where the
system can act or decide (2), pii data (2) or confidential data (1), decisions about people (2),
customer-facing (1), an external model provider (1), running in prod (1), and an agent with no
log group (1). 7 or more is high, 4 to 6 medium, otherwise low. High risk requires an impact
assessment. Details in [docs/registry-fields.md](docs/registry-fields.md).

**Behaviour rules.** Over every agent's spans in the lookback window:

| Rule | Severity | Fires when |
|---|---|---|
| Agent not in the inventory | high | Spans come from an agent with no registry entry |
| Undeclared tool | high | The agent calls a tool it never declared |
| Action without approval | critical | A side-effect call's amount is above the agent's approval threshold with no approved human step before it |
| Tool loop | medium | The same tool 4 or more times in a row |
| Token spike | medium | A run uses more than 5x the agent's median tokens and over 20k |
| Run error | medium | A run ends in an error |
| Approval denied | low | An approver said no; informational |

Details in [docs/observability-attributes.md](docs/observability-attributes.md).

**Model fit.** A model is eligible only if it meets the task's capability bar, supports tools
when the task needs them, and, for pii or confidential data, runs on Bedrock so the data stays
in AWS. Eligible models are scored out of 100 on capability, cost, residency and speed, with
capability weighted higher for high-risk systems. The result is keep, downgrade, upgrade,
residency switch or switch, with a one-sentence rationale. Details in
[docs/model-recommendation.md](docs/model-recommendation.md).

## The simulated estate

The seed (`backend/app/seed/`) builds a believable company account. No resource carries a tag
that gives away its category; the ground truth lives only in `seed/expected.py`, which only
tests read.

| Workload | Service | What it is | Classified as |
|---|---|---|---|
| refund-agent | Lambda | Bedrock `converse` with tools, a tool-use loop, session memory, approval threshold $500 | Agent |
| sales-research-agent | Lambda | OpenAI with tools, `tool_choice="auto"`, loop until no tool calls | Agent |
| hr-policy-assistant | Bedrock agent | HR policy agent with declared tools and a knowledge base | Agent |
| procurement-agent | ECS | crewai agent with Redis memory and purchase-order tools | Agent |
| support-chatbot | Lambda | One Claude Haiku chat call | GenAI app |
| invoice-summarizer | Lambda | Summarises supplier invoice PDFs | GenAI app |
| contract-review-rag | Lambda | Bedrock knowledge base `retrieve_and_generate` | GenAI app |
| ticket-classifier | Lambda | OpenAI call that picks one label | GenAI app |
| loan-underwriting-workflow | Step Functions | Calls a model, then waits for an underwriter | AI workflow |
| churn-predictor | SageMaker | XGBoost endpoint | Predictive model |
| fraud-scoring | SageMaker | PyTorch endpoint | Predictive model |
| image-thumbnailer | Lambda | PIL resize | Not AI |
| nightly-billing-export | Lambda | SQL to CSV | Not AI |
| order-fulfilment | Step Functions | No AI calls, but shares a role that allows `bedrock:InvokeModel` | Not AI (permission flagged as unused) |
| web-storefront | ECS | nginx and node | Not AI |

The agents' CloudWatch logs hold 10 to 20 normal runs each over 14 days, plus eight planted cases:

| Case | Planted in the logs | What the tool reports |
|---|---|---|
| a | refund-agent issues a $1,200 refund with no human approval (threshold $500) | critical: issue_refund of $1,200 without approval (threshold $500) |
| b | refund-agent: the approver denies a $750 refund | low: approver denied issue_refund of $750; the agent stopped |
| c | sales-research-agent calls `export_contacts_csv`, which it never declared | high: called export_contacts_csv, which is not in its declared tools |
| d | sales-research-agent calls `web_search` five times in a row | medium: web_search called 5 times in a row in one run |
| e | sales-research-agent run ends in `RateLimitError` | medium: run failed with RateLimitError |
| f | procurement-agent run uses about 100k tokens (normal is 5 to 10k) | medium: run used 99,600 tokens, 15x the agent's median |
| g | procurement-agent creates a $25k purchase order after an approved human step (control case) | nothing, as intended |
| h | marketing-copy-agent logs activity but is in no inventory | high: shadow agent, 52 spans in 13 runs, not in the AI registry |

## Key design decisions

- **Rules first, the LLM only where they are unsure.** Classification, risk, behaviour checks and model fit are deterministic rules, so every verdict is reproducible, auditable against its evidence and free to run. In LLM mode the LLM decides only the resources the rules are not confident about, and only when its quotes check out.
- **An IAM permission alone is not AI.** Permissions are recorded as unused AI permissions instead; `order-fulfilment` is the decoy that tests this.
- **Stable registry ids and retirement.** A system's id is a hash of service, account, region and name, not its ARN, so reruns update the same row and keep `first_seen_at`. Systems not seen in a run are marked retired, not deleted.
- **Secrets are masked at collection.** Values under names containing key, token, secret, password, passwd, credential or auth, passwords in connection strings, and values shaped like `sk-...`, `AKIA...` or long hex/base64 become `[masked]` in env vars, config, tags and code before anything is classified or stored. Names are kept.
- **A failed collector never retires systems.** If one service fails (for example AccessDenied), the run still succeeds with the error recorded, and nothing is retired that run.
- **Internal agents trace themselves but are not flagged.** The crawler and observability agents write their own spans marked `internal: true`, so they are visible but never reported as shadow agents.
- **Residency is a hard rule.** pii and confidential systems are only ever recommended models hosted on Bedrock, whatever their score.
- **Risk-aware model weights.** High-risk systems weigh capability over cost when picking a model.
- **Prices are illustrative.** Model prices and capability scores in the catalog show how the engine reasons; they are not list prices or benchmarks.

## The LLM crawler agent

Off by default. With `LLM_ENABLED=true`, the crawler hands an LLM a tool-use loop. `LLM_PROVIDER`
picks where the LLM runs:

- **`bedrock`** (the default, for real deployments): Bedrock Converse, so an account's code and
  config never leave AWS. `LLM_MODEL_ID` defaults to the Claude Haiku 5.5 inference profile
  `au.anthropic.claude-haiku-5-5`.
- **`gemini`** and **`groq`**: the Gemini API or Groq's OpenAI-compatible API, with
  `GEMINI_API_KEY` or `GROQ_API_KEY` read from `backend/.env` and never logged. `LLM_MODEL_ID`
  defaults to `gemini-3-flash-preview` or `openai/gpt-oss-120b`. Code and config then leave AWS,
  so use these only for synthetic data or where that is acceptable. Rate limits are waited out
  as the server asks; a daily quota fails the call.

The loop:

- **Tools:** one per collector, `inspect_resource(name)` (masked env, tags, IAM actions, config
  and code truncated to 6,000 characters) and `record_verdict(...)`. Collector results say what
  the rules concluded and mark with `needs_review` what they are unsure about.
- **Budget:** at most `LLM_MAX_STEPS` LLM calls (default 12).
- **Guardrails:** any collector the LLM never called is run afterwards, marked
  `crawler.guardrail` on its span. An LLM error or an exhausted budget never fails the crawl; the
  rules decide whatever is left.

**Hybrid classification.** The rules decide alone when they are confident: a clear AI signal (a
model call, a Bedrock agent, an ML image, a workflow model step, an agent framework env var) or
no AI signal and no model mentioned at all, together with an agent score of 0.2 or less, or 0.8
or more. Otherwise the LLM's verdict `{is_ai, is_agent, category, reasoning, evidence_quotes}`
is used if it passes the validator: every quote must appear in the collected code or config,
models and tools it names must appear there too, and the category must agree with `is_agent`.
A rejected verdict goes back to the LLM as a tool error so it can retry within the budget; if
none is accepted the rules decide. `ai_registry.decided_by` is `rules`, `llm` or
`rules_fallback`, and `llm_verdict` keeps the verdict and any rejection reasons. The system page
shows both.

**The crawler is an observed agent.** Every LLM call is a `chat` span under `aws-ai-crawler`
with model, tokens and cost, next to its collector and tool spans. The crawler appears in Agent
activity as "Our crawler", and four rules watch it: `collector_failed`, `llm_verdict_rejected`,
`slow_run` and `llm_budget_exceeded` (see
[docs/observability-attributes.md](docs/observability-attributes.md)).

In mock mode only Bedrock Runtime passes through moto to real AWS (Gemini calls never go through moto), so a Bedrock LLM needs real
credentials and a region in `AWS_REGIONS` where the profile exists. In `aws` mode the crawler's
role also needs `bedrock:InvokeModel` on the inference profile and its foundation model, on top
of the read-only policy.

**Schema change.** This adds `decided_by` and `llm_verdict` to `ai_registry` and four rules to
`agent_anomalies`. The schema file only creates missing tables, so an existing database needs a
reset once: `docker compose down -v` (then recreate `registry_test`), or drop the tables.

### Evaluation

`python -m app.cli evaluate` scored the rules, two models on their own and the hybrid with each
on 41 labelled resources: the 15 in the simulated estate and 26 hard variants. Every wrong case
is in [docs/evaluation.md](docs/evaluation.md).

| Method | is_ai F1 | is_agent F1 | LLM calls | Rejected verdicts | Cost |
|---|---|---|---|---|---|
| rules | 0.78 | 0.71 | 0 | - | $0 |
| gpt-oss-20b | 0.97 | 0.90 | 41 | 20 | $0.0085 |
| hybrid with gpt-oss-20b | 0.87 | 0.90 | 23 | 15 | $0.0050 |
| gpt-oss-120b | 0.98 | 0.95 | 41 | 17 | $0.0147 |
| hybrid with gpt-oss-120b | 0.91 | 0.95 | 23 | 11 | $0.0083 |

- **It ran on Groq, not Bedrock.** The AWS test account got "not available for this account" for
  Anthropic models and had every Nova quota at 0. The Gemini free tier allowed 20 Flash requests
  a day, where one model needs 41, and no Pro requests. Groq's Llama models were not available to
  the key, so gpt-oss-120b stands in for a 70B-class model. Only synthetic code and config were
  sent.
- **Model choice.** The hybrid with gpt-oss-120b beat gpt-oss-20b by 0.052 agent F1, just over
  the 0.05 bar, so `openai/gpt-oss-120b` is the Groq default. Bedrock with Claude Haiku 5.5 stays
  the default provider. The margin is one resource, `pydantic-ai-agent`. An earlier run, whose
  120b half failed on a dropped connection, scored gpt-oss-20b's hybrid at 0.95, so the gap is
  within run-to-run variation.
- **The validator costs recall.** It rejects 17 to 20 of each model's 41 verdicts, mostly over
  formatting rather than invented evidence:
  - quotes that include the context's labels, such as `iam actions:`;
  - quotes wrapped in extra quotation marks or JSON escapes;
  - service names such as Textract given as models or tools.

  So each model alone scores higher on is_ai than its hybrid does, though the hybrid makes no
  false positives. Matching quotes against the text the LLM was shown would recover most of
  these. The validator was not changed after the results came in.

## Running against a real AWS account

Set `DISCOVERY_MODE=aws` and `AWS_REGIONS`, give the process AWS credentials with the read-only
policy in [docs/crawler-iam-policy.json](docs/crawler-iam-policy.json), and run
`python -m app.cli discover`. Agents are expected to log OpenTelemetry-style span JSON under
`AGENT_LOG_PREFIX` (default `/ai/agents/`). From the API, `POST /discovery/run` only runs in mock
mode, because the API has no authentication.

boto3 needs `botocore[crt]` (in `requirements.txt`) to use credentials from `aws login`.

## Tested on a real AWS account

Region `ap-southeast-2`, in an otherwise empty account, using the scripts in `testbed/` (run from
the repo root with `AWS_REGIONS=ap-southeast-2`):

```bash
backend/.venv/bin/python testbed/deploy.py                              # idempotent
DISCOVERY_MODE=aws backend/.venv/bin/python testbed/crawl_readonly.py   # crawl as the read-only role
backend/.venv/bin/python testbed/teardown.py                            # delete everything tagged testbed=ai-registry-crawler
```

**Deployed**, all tagged `testbed=ai-registry-crawler`, nothing that bills while idle:

- Four Lambdas from the seed with its code, env vars and tags: `refund-agent`, `support-chatbot`,
  `ticket-classifier`, `image-thumbnailer`. Each has its own role with the seed's IAM actions.
  They are never invoked.
- `/ai/agents/refund-agent` with 6 runs (46 spans), one of them the $1,200 refund with no approval,
  and `/ai/agents/marketing-copy-agent` with 3 runs (12 spans) from an agent in no inventory.
- `ai-registry-crawler-readonly`, a role with exactly [docs/crawler-iam-policy.json](docs/crawler-iam-policy.json).

**Found** by `DISCOVERY_MODE=aws python -m app.cli discover` on a fresh database:

| Planted | Found |
|---|---|
| 3 AI systems, 1 of them an agent; `image-thumbnailer` is not AI | 4 resources scanned, 3 AI systems, 1 agent, thumbnailer skipped |
| Same records as the simulated estate | Every registry field identical to the mock run of the same Lambdas, including evidence read from the downloaded code |
| $1,200 refund without approval | `side_effect_without_approval`, critical, threshold read from the live `APPROVAL_THRESHOLD` |
| Shadow agent | `unregistered_agent`, high, 12 spans in 3 runs |
| Model fit | refund-agent keep, support-chatbot downgrade, ticket-classifier residency, as in mock mode |

**Broke, and how it was fixed:**

- The testbed first deleted and recreated each log group on rerun so spans would not pile up.
  CloudWatch kept the old events, so the second deploy left every span twice (92 events for 46
  spans) and the crawler reported the $1,200 refund twice. The database already ignored the
  repeated span ids, but the rules ran over the raw list. The observability agent now reads each
  `(trace_id, span_id)` once, which also covers exporters that retry, with a test that redelivers
  a log group's events. The testbed now writes its spans only when its log stream is new.

Code download from the presigned `GetFunction` URL, pagination, tags, IAM role reads and log
timestamps worked unchanged. Crawling as the read-only role gave a registry, findings and
recommendations identical to the full-access run, with no denied calls, so the policy needed no
changes.

## Limitations

- IAM Deny statements and conditions are ignored when listing a role's actions.
- Only the first region in `AWS_REGIONS` is crawled.
- The real-account test had no Bedrock agents, SageMaker endpoints, Step Functions or ECS task
  definitions, so those collectors and their IAM actions are only tested against moto.
- Errors on a single resource (code download, IAM reads) do not fail a run and are not stored with
  it; `testbed/crawl_readonly.py` prints them.
- CloudWatch events cannot be deleted one by one, so the testbed writes its spans once. After
  `LOG_LOOKBACK_HOURS` (30 days) they fall out of the window; tear down and deploy again.
- `task_type` and "decides about people" come from keyword rules tuned to this estate.
- Read-only agents (no side-effect tools, no approval step) count as assistive.
- Findings are recomputed every run and overwrite the previous set, so there is no findings history.
- The approval threshold is only read from Lambda agents (`APPROVAL_THRESHOLD`).
- moto has no Bedrock action groups or Lambda log-group links, so they are approximated with tags and the `/ai/agents/<name>` naming convention.
- Seeded traffic is low, so monthly cost estimates use the default volume (`DEFAULT_MONTHLY_REQUESTS`) with token profiles from the logs.
- The API has no authentication.
- In LLM mode the planner refers to resources by name, so two resources with the same name in different services would be confused.
- The crawler's LLM cost uses Anthropic's list price for Haiku 5.5; Bedrock geo inference profiles can cost slightly more.
- An unknown system id renders the not-found page with HTTP 200, because the page streams before the 404 is known.

## Tests

```bash
docker compose up -d db
docker compose exec db createdb -U registry registry_test     # once
cd backend
TEST_DATABASE_URL=postgresql://registry:registry@localhost:5433/registry_test pytest -q
```

221 tests. Tests set fake AWS credentials, so one that forgets the mock fails instead of reaching a real account. Without `TEST_DATABASE_URL` the database tests are skipped with a clear message;
they truncate every table, so never point them at the main database.

| File | Covers |
|---|---|
| `test_estate.py`, `test_collectors.py` | The simulated account: 15 resources, readable Lambda code, planted log cases, determinism, no giveaway tags; collector output and error handling |
| `test_masking.py` | Each secret name and value pattern, connection strings, code literals, nested config, values that must stay untouched |
| `test_llm.py`, `test_hybrid.py` | Chat spans with tokens and cost; Gemini and Groq message translation, rate-limit and dropped-connection retries; the confidence gate, every validator rejection, overrides and fallbacks |
| `test_crawler_llm.py`, `test_evaluation.py` | The planner loop with FakeLLM, guardrails, step budget, LLM failure, crawler findings; evaluation metrics and report |
| `test_classifier.py` | Every workload matches the ground truth; IAM-only is not AI; hand-built agent and GenAI cases |
| `test_registry.py` | Stable ids, risk scoring, decisions about people, impact assessments and controls |
| `test_crawler.py` | Crawls into Postgres, reruns, retirement, self-tracing, failed collectors, crashes |
| `test_observability.py`, `test_observe_pipeline.py` | Redaction, one test per behaviour rule, the $25k control, end-to-end findings matching the ground truth |
| `test_recommend.py`, `test_recommend_pipeline.py` | Eligibility and residency across all tasks, each outcome, volume rules, the estate's outcomes |
| `test_testbed.py` | Testbed deploy is idempotent and tags everything; teardown removes only tagged resources |
| `test_api.py`, `test_schema.py`, `test_health.py` | Every endpoint, filters, 404s, 409 on overlap, startup discovery; schema idempotency and constraints |

Frontend: `cd frontend && npm run lint && npm run build`.

## Project layout

```
backend/
  app/
    api.py, pipeline.py, store.py, cli.py, config.py, db.py, schema.sql
    crawler/          collectors, classifier, masking, hybrid LLM judge, registry builder, crawler agent
    llm.py            Bedrock, Gemini and Groq clients, and FakeLLM
    evaluation.py     rules vs LLM vs hybrid scoring
    observability/    span ingestion and behaviour rules
    recommend/        model catalog and recommendation engine
    seed/             simulated AWS estate, agent logs, ground truth, evaluation variants
  tests/
frontend/
  app/                Overview, inventory, agents, issues, model fit pages
  lib/                typed API client and formatting
testbed/              real-AWS deploy, read-only crawl and teardown
docs/                 data model, registry fields, observability, model fit, IAM policy, demo script
docker-compose.yml
```

## Troubleshooting

**Port already in use.** Postgres is on host port 5433 so it does not clash with a local
Postgres on 5432. If 5433, 8000 or 3000 is taken, stop whatever holds it (`lsof -i :3000`), or
change the left side of the port mapping in `docker-compose.yml`.

**API starting before the database.** Compose waits for the `db` healthcheck before starting the
API, and the API retries the database 10 times on startup. If it still cannot connect it starts
anyway and `/health` reports `degraded` instead of crashing.

**`localhost` inside Docker.** Inside a container, `localhost` is the container itself. The API
reaches Postgres at `db:5432`, and the dashboard's server reaches the API at `http://api:8000`
(`INTERNAL_API_URL`). Your browser runs on the host, so it calls the API at `http://localhost:8000`.
