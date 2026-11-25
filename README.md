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
| Tell agents from non-agents | `backend/app/crawler/classifier.py`: weighted, explainable signals from code, config, env and tags; every decision carries its evidence |
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
- **Crawler agent** runs collect, classify and register, retires systems that disappeared, and traces its own steps.
- **Observability agent** reads span logs, links them to registry entries, stores them and runs seven behaviour rules.
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

- **Rules, not an LLM.** Classification, risk, behaviour checks and model fit are deterministic rules, so every verdict is reproducible, auditable against its evidence and free to run.
- **An IAM permission alone is not AI.** Permissions are recorded as unused AI permissions instead; `order-fulfilment` is the decoy that tests this.
- **Stable registry ids and retirement.** A system's id is a hash of service, account, region and name, not its ARN, so reruns update the same row and keep `first_seen_at`. Systems not seen in a run are marked retired, not deleted.
- **A failed collector never retires systems.** If one service fails (for example AccessDenied), the run still succeeds with the error recorded, and nothing is retired that run.
- **Internal agents trace themselves but are not flagged.** The crawler and observability agents write their own spans marked `internal: true`, so they are visible but never reported as shadow agents.
- **Residency is a hard rule.** pii and confidential systems are only ever recommended models hosted on Bedrock, whatever their score.
- **Risk-aware model weights.** High-risk systems weigh capability over cost when picking a model.
- **Prices are illustrative.** Model prices and capability scores in the catalog show how the engine reasons; they are not list prices or benchmarks.

## Running against a real AWS account

Set `DISCOVERY_MODE=aws` and `AWS_REGIONS`, give the process AWS credentials with the read-only
policy in [docs/crawler-iam-policy.json](docs/crawler-iam-policy.json), and run
`python -m app.cli discover`. Agents are expected to log OpenTelemetry-style span JSON under
`AGENT_LOG_PREFIX` (default `/ai/agents/`). From the API, `POST /discovery/run` only runs in mock
mode, because the API has no authentication.

This mode has not been tested against a real account.

## Limitations

- IAM Deny statements and conditions are ignored when listing a role's actions.
- Only the first region in `AWS_REGIONS` is crawled.
- `task_type` and "decides about people" come from keyword rules tuned to this estate.
- Read-only agents (no side-effect tools, no approval step) count as assistive.
- Findings are recomputed every run and overwrite the previous set, so there is no findings history.
- The approval threshold is only read from Lambda agents (`APPROVAL_THRESHOLD`).
- moto has no Bedrock action groups or Lambda log-group links, so they are approximated with tags and the `/ai/agents/<name>` naming convention.
- Seeded traffic is low, so monthly cost estimates use the default volume (`DEFAULT_MONTHLY_REQUESTS`) with token profiles from the logs.
- The API has no authentication.
- An unknown system id renders the not-found page with HTTP 200, because the page streams before the 404 is known.

## Tests

```bash
docker compose up -d db
docker compose exec db createdb -U registry registry_test     # once
cd backend
TEST_DATABASE_URL=postgresql://registry:registry@localhost:5433/registry_test pytest -q
```

155 tests. Without `TEST_DATABASE_URL` the database tests are skipped with a clear message;
they truncate every table, so never point them at the main database.

| File | Covers |
|---|---|
| `test_estate.py`, `test_collectors.py` | The simulated account: 15 resources, readable Lambda code, planted log cases, determinism, no giveaway tags; collector output and error handling |
| `test_classifier.py` | Every workload matches the ground truth; IAM-only is not AI; hand-built agent and GenAI cases |
| `test_registry.py` | Stable ids, risk scoring, decisions about people, impact assessments and controls |
| `test_crawler.py` | Crawls into Postgres, reruns, retirement, self-tracing, failed collectors, crashes |
| `test_observability.py`, `test_observe_pipeline.py` | Redaction, one test per behaviour rule, the $25k control, end-to-end findings matching the ground truth |
| `test_recommend.py`, `test_recommend_pipeline.py` | Eligibility and residency across all tasks, each outcome, volume rules, the estate's outcomes |
| `test_api.py`, `test_schema.py`, `test_health.py` | Every endpoint, filters, 404s, 409 on overlap, startup discovery; schema idempotency and constraints |

Frontend: `cd frontend && npm run lint && npm run build`.

## Project layout

```
backend/
  app/
    api.py, pipeline.py, store.py, cli.py, config.py, db.py, schema.sql
    crawler/          collectors, classifier, registry builder, crawler agent
    observability/    span ingestion and behaviour rules
    recommend/        model catalog and recommendation engine
    seed/             simulated AWS estate, agent logs, ground truth
  tests/
frontend/
  app/                Overview, inventory, agents, issues, model fit pages
  lib/                typed API client and formatting
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
