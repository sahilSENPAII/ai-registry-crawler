# AI registry fields

Each `ai_registry` row is one entry in the ISO/IEC 42001 AI system inventory. Built by
`backend/app/crawler/registry.py` from a collected resource and its classification.

| Field | Source | ISO/IEC 42001 purpose / Annex A control |
|---|---|---|
| `id` | sha1(`service\|account\|region\|name`)[:16] | Stable inventory identity across scans (clause 4.3 scope, A.6.2.7) |
| `resource_arn`, `name`, `service`, `account_id`, `region` | Collector | Locates the system (A.6.2.7 technical documentation) |
| `category`, `is_agent`, `agent_score`, `evidence` | Classifier | What the system is and why we think so (A.6.2.7) |
| `models`, `provider`, `frameworks` | Classifier | Components and third parties in use (A.10.3 suppliers, A.4.4 tooling) |
| `tools` | Classifier | Actions the system can take; side-effect tools raise risk (A.4.4, A.9.2) |
| `data_sources`, `data_classification` | Classifier / `data-classification` tag | Data used and its sensitivity (A.4.3 data resources, A.7.5 provenance) |
| `task_type`, `purpose` | Classifier / description or instruction | Intended use (A.9.4) |
| `owner` | `owner` tag | Accountable person (A.3.2 roles and responsibilities) |
| `environment` | `environment` tag, else `unknown` | Lifecycle stage (A.6.2.6) |
| `customer_facing` | `customer-facing` tag, else NULL | Exposure to people outside the company (A.9.2, A.9.4) |
| `decisions_about_people` | Description, instruction or workflow text (never a tag) | Impact on individuals (A.5.4) |
| `human_oversight` | `waitForTaskToken`, `APPROVAL_THRESHOLD` or a `human_approval` step | Human control over outputs (A.9.2) |
| `autonomy_level` | Derived, see below | How far it acts without a human (A.9.2) |
| `risk_score`, `risk_level`, `risk_factors` | Scoring rules below | Risk assessment (clause 6.1.2) |
| `impact_assessment_status` | From `risk_level` | Whether an impact assessment is due (A.5.2) |
| `controls` | Rules below | Applicable Annex A controls, each with the reason |
| `log_group`, `monitored` | Collector | Event logging and monitoring in operation (A.6.2.6, A.6.2.8) |
| `unused_ai_permissions` | Classifier | AI permissions granted but not used (least privilege, A.4.4) |
| `status`, `first_seen_at`, `last_seen_at`, `last_run_id` | Crawler | Lifecycle: `retired` once a scan no longer finds it (A.6.2.6) |
| `tags` | Resource tags | Raw metadata kept for audit |

Tag values are never guessed: a missing or unrecognised `environment` is `unknown`, and a
missing `data-classification` or `customer-facing` is NULL.

## Risk scoring

| Factor | Points |
|---|---|
| Agent | 2 |
| Side-effect tools (name starts with issue, send, create, update, delete, submit, export, write, refund, pay, approve) | 2 |
| No human oversight | 2 |
| Data classification `pii` / `confidential` | 2 / 1 |
| Makes decisions about people | 2 |
| Customer-facing | 1 |
| External model provider (direct API, outside AWS) | 1 |
| Runs in `prod` | 1 |
| Agent with no log group | 1 |

Level: **high** at 7 or more, **medium** at 4-6, **low** below 4.
Impact assessment: high = `required`, medium = `recommended`, low = `not_required`.

## Autonomy level

| Level | When |
|---|---|
| `automated_scoring` | Predictive ML model |
| `supervised` | Agent or AI workflow with an approval gate |
| `autonomous` | Agent with side-effect tools and no approval gate |
| `assistive` | Everything else (GenAI apps, read-only agents) |

## Annex A controls

| Control | Applies when |
|---|---|
| A.3.2 AI roles and responsibilities | Always |
| A.6.2.7 AI system technical documentation | Always |
| A.5.2 Impact assessment process, A.5.4 Impact on individuals | Impact assessment required or recommended, or decisions about people |
| A.6.2.6 Operation and monitoring, A.6.2.8 Event logs | Agents |
| A.7.5 Data provenance | `pii` or `confidential` data |
| A.4.3 Data resources | Has data sources |
| A.4.4 Tooling resources | Has tools |
| A.10.3 Suppliers | External model provider |
| A.9.2 Responsible use, A.9.4 Intended use | Customer-facing |
