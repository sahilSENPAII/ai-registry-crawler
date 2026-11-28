# Observability attributes and behaviour rules

The observability agent (`backend/app/observability/agent.py`) reads span JSON from the
CloudWatch log groups under `AGENT_LOG_PREFIX` (default `/ai/agents/`), stores each span in
`agent_traces`, and runs the behaviour rules below. Attribute names follow the
OpenTelemetry GenAI semantic conventions where one exists.

## Captured attributes

| Attribute | Convention | Stored as | Why it matters for governance |
|---|---|---|---|
| `trace_id`, `span_id`, `parent_span_id` | OTel trace context | same | Rebuilds each agent run step by step for audit (ISO/IEC 42001 A.6.2.8) |
| `name` | OTel span name (`{operation} {target}`) | `span_name` | Human-readable step in the run |
| `start_time`, `end_time` | OTel span timing | `started_at`, `ended_at`, `duration_ms` | When the agent acted; latency and ordering (e.g. approval before action) |
| `status` | OTel span status | `status` | Failed runs, for reliability review |
| `error.type` | OTel `error.type` | `error_type` | Which failures happen and how often |
| `gen_ai.operation.name` | GenAI: `invoke_agent`, `chat`, `execute_tool`, plus `retrieval` and `human_approval` | `operation` | What kind of step it was |
| `gen_ai.agent.name` | GenAI agent spans | `agent_name` | Links behaviour to a registry entry; unknown names reveal shadow agents |
| `gen_ai.request.model` | GenAI client spans | `model` | Which model made each decision (A.10.3 suppliers) |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | GenAI usage | `input_tokens`, `output_tokens`, `cost_usd` | Cost and runaway usage |
| `gen_ai.tool.name` | GenAI `execute_tool` spans | `tool_name` | Which actions the agent took, versus what it declared (A.4.4) |
| `gen_ai.tool.call.arguments` | GenAI tool call arguments (JSON string) | `attributes` | Amounts and targets of side effects, for approval checks |
| `gen_ai.data_source.id` | GenAI retrieval spans | `attributes` | Which knowledge base grounded the answer (A.7.5) |
| `approval.action`, `approval.amount`, `approval.decision` | Custom, no GenAI equivalent | `attributes` | Evidence of human oversight on risky actions (A.9.2) |
| `internal` | Custom | `attributes` | Marks the crawler's and this agent's own spans so they are never shadow agents |
| `crawler.llm.*`, `crawler.decided_by.*` | Custom, on the crawler's run span | `attributes` | The LLM's model, calls, tokens, cost, step budget and whether it ran out; how many resources each method decided |
| `crawler.guardrail` | Custom, on a crawler collector span | `attributes` | The planner never called this collector, so the guardrail ran it |
| `crawler.resource`, `crawler.verdict.rejected`, `crawler.verdict.rejected_items` | Custom, on `inspect_resource` / `record_verdict` spans | `attributes` | Which resource the LLM looked at, why the validator rejected its verdict, and the models or tools it named that were not in the collected data, which are dropped |

Every span keeps its full attribute map in `agent_traces.attributes`. Before storing, any
attribute whose key matches `email|phone|ssn|card|password|address` (case-insensitive),
including keys inside `gen_ai.tool.call.arguments`, is replaced with `[redacted]`.
`cost_usd` comes from the illustrative price table in `backend/app/pricing.py`.

## Behaviour rules

Findings are recomputed over the whole lookback window (`LOG_LOOKBACK_HOURS`) on every run, and
replace the previous set in `agent_anomalies`.

| Rule | Severity | Fires when | Threshold |
|---|---|---|---|
| `unregistered_agent` | high | Spans come from an agent with no active registry row (internal agents excluded) | - |
| `undeclared_tool` | high | An `execute_tool` span calls a tool not in the registry's `tools` | - |
| `side_effect_without_approval` | critical | A side-effect tool call's `amount` is above the agent's approval threshold (`APPROVAL_THRESHOLD`) with no approved `human_approval` for that action earlier in the trace | agent's own threshold |
| `tool_loop` | medium | The same tool is called several times in a row in one trace | 4 calls |
| `token_spike` | medium | A trace's total tokens exceed both a multiple of the agent's median trace and a floor | 5x median and > 20,000 |
| `run_error` | medium | A trace has a span with status `error` (one finding per trace) | - |
| `approval_denied` | low | A `human_approval` span returned `denied`; the agent stopped, so this is informational | - |

The crawler (`aws-ai-crawler`) writes its own spans straight to `agent_traces`; these rules run over them:

| Rule | Severity | Fires when | Threshold |
|---|---|---|---|
| `collector_failed` | medium | A collector span has status `error`, for example AccessDenied | - |
| `llm_verdict_rejected` | medium | The validator rejected a `record_verdict` call (quote or model not in what was collected, inconsistent category) | - |
| `slow_run` | low | A crawl took longer than a multiple of the median crawl | 3x median |
| `llm_budget_exceeded` | low | The planner was still calling tools when it hit `LLM_MAX_STEPS`; the rules covered the rest | `LLM_MAX_STEPS` |

Thresholds live in `THRESHOLDS` at the top of the agent module.
