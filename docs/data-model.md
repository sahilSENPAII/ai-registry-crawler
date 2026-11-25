# Data model

Schema lives in `backend/app/schema.sql`. It is idempotent and runs on every API
startup (`python -m app.cli init` runs it by hand). All times are `timestamptz` (UTC),
nested data is `jsonb`, and every enum is a `CHECK` constraint.

## discovery_runs
One row per scan of the AWS account (or of mock data). It records when the scan
started and finished, whether it succeeded, which mode and regions it covered, and
summary counts: resources scanned, AI systems and agents found, findings raised.
Every other table points back here through `run_id` / `last_run_id`.

## ai_registry
One row per AI system, as the ISO/IEC 42001 AI inventory sees it: a Bedrock agent, a
SageMaker endpoint, a Lambda that calls an LLM, and so on. The row says what the
system is (category, agent score and evidence), what it uses (models, tools, data and
its classification), who is accountable (owner, environment, oversight), how risky it
is (score, level, factors, Annex A controls), whether it is monitored, and its
lifecycle (first/last seen, active or retired).

## agent_traces
One row per span of agent activity, following the OpenTelemetry GenAI conventions: an
agent invocation, a model call, a tool call, a retrieval, or a human approval step.
Spans with the same `trace_id` form one agent run. `registry_id` is NULL when the
agent isn't in the registry, which is how shadow agents show up. `(trace_id, span_id)`
is unique, so re-ingesting the same logs never creates duplicates.

## agent_anomalies
One row per finding raised by a behaviour rule, such as an unregistered agent, a
tool the agent never declared, a side effect without human approval, a tool loop or
a token spike. It names the rule, its severity, and the trace/span that triggered it.

## model_recommendations
At most one row per AI system: the current recommendation for which model it should
use for its task type, with scores and estimated monthly cost for the current and
recommended models, the outcome (`keep`, `downgrade`, `upgrade`, `residency` or `switch`), a
rationale and the alternatives considered. A newer recommendation replaces the old row. It is deleted with its registry row.

## Why `ai_registry.id` is a stable hash, not the ARN
Each scan has to update the same row for the same system, so that `first_seen_at`,
owner and other history survive reruns. ARNs are a poor key for that: some change
when a resource is redeployed or versioned (for example, Bedrock agent aliases,
SageMaker endpoint configs, or Lambda version ARNs). So the id is a hash of
`service|account|region|name`, which stays the same as long as the system keeps its
identity. `resource_arn` is still stored (NOT NULL, not unique) for linking back to AWS.
