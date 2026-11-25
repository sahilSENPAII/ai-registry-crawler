-- Idempotent: safe to run on every API startup.
-- The whole file runs in one transaction; this lock stops two concurrent runs racing on CREATE.
SELECT pg_advisory_xact_lock(4242001);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id                bigserial PRIMARY KEY,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    mode              text CHECK (mode IN ('mock', 'aws')),
    regions           text[],
    resources_scanned int NOT NULL DEFAULT 0,
    ai_systems_found  int NOT NULL DEFAULT 0,
    agents_found      int NOT NULL DEFAULT 0,
    findings_count    int NOT NULL DEFAULT 0,
    error             text
);

CREATE TABLE IF NOT EXISTS ai_registry (
    -- identity
    id                       text PRIMARY KEY,  -- stable hash of service|account|region|name
    resource_arn             text NOT NULL,     -- not unique: some ARNs change between runs
    name                     text,
    service                  text,
    account_id               text,
    region                   text,
    -- classification
    category                 text CHECK (category IN ('ai_agent', 'genai_application', 'ai_workflow', 'predictive_ml')),
    is_agent                 boolean,
    agent_score              numeric(4,3),
    evidence                 jsonb NOT NULL DEFAULT '[]',
    -- what it uses
    models                   jsonb,
    provider                 text,
    frameworks               text[],
    tools                    jsonb,
    data_sources             jsonb,
    data_classification      text CHECK (data_classification IN ('public', 'internal', 'confidential', 'pii')),
    task_type                text,
    purpose                  text,
    -- accountability
    owner                    text,
    environment              text CHECK (environment IN ('dev', 'staging', 'prod', 'unknown')),
    customer_facing          boolean,
    decisions_about_people   boolean,
    human_oversight          text,
    autonomy_level           text,
    -- risk
    risk_score               int,
    risk_level               text CHECK (risk_level IN ('low', 'medium', 'high')),
    risk_factors             jsonb,
    impact_assessment_status text CHECK (impact_assessment_status IN ('required', 'recommended', 'not_required')),
    controls                 jsonb,  -- ISO/IEC 42001 Annex A refs
    -- monitoring
    log_group                text,
    monitored                boolean NOT NULL DEFAULT false,
    unused_ai_permissions    jsonb NOT NULL DEFAULT '[]',
    -- lifecycle
    status                   text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    first_seen_at            timestamptz NOT NULL DEFAULT now(),
    last_seen_at             timestamptz NOT NULL DEFAULT now(),
    last_run_id              bigint REFERENCES discovery_runs(id),
    tags                     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ai_registry_category_idx   ON ai_registry (category);
CREATE INDEX IF NOT EXISTS ai_registry_risk_level_idx ON ai_registry (risk_level);
CREATE INDEX IF NOT EXISTS ai_registry_status_idx     ON ai_registry (status);

-- One row per span, following OpenTelemetry GenAI semantic conventions.
CREATE TABLE IF NOT EXISTS agent_traces (
    id             bigserial PRIMARY KEY,
    run_id         bigint REFERENCES discovery_runs(id),
    registry_id    text REFERENCES ai_registry(id),  -- NULL = shadow / unregistered agent
    agent_name     text NOT NULL,
    trace_id       text NOT NULL,
    span_id        text NOT NULL,
    parent_span_id text,
    operation      text CHECK (operation IN ('invoke_agent', 'chat', 'execute_tool', 'retrieval', 'human_approval')),
    span_name      text,
    model          text,
    tool_name      text,
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz,
    duration_ms    int,
    input_tokens   int,
    output_tokens  int,
    cost_usd       numeric(10,6),
    status         text CHECK (status IN ('ok', 'error')),
    error_type     text,
    attributes     jsonb NOT NULL DEFAULT '{}',
    UNIQUE (trace_id, span_id)  -- re-ingesting the same logs never duplicates
);
CREATE INDEX IF NOT EXISTS agent_traces_registry_started_idx ON agent_traces (registry_id, started_at);
CREATE INDEX IF NOT EXISTS agent_traces_trace_idx            ON agent_traces (trace_id);
CREATE INDEX IF NOT EXISTS agent_traces_agent_name_idx       ON agent_traces (agent_name);

CREATE TABLE IF NOT EXISTS agent_anomalies (
    id          bigserial PRIMARY KEY,
    run_id      bigint REFERENCES discovery_runs(id),
    registry_id text REFERENCES ai_registry(id),
    agent_name  text NOT NULL,
    rule        text CHECK (rule IN ('unregistered_agent', 'undeclared_tool', 'side_effect_without_approval',
                                     'tool_loop', 'token_spike', 'run_error', 'approval_denied')),
    severity    text CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    trace_id    text,
    span_id     text,
    description text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    detected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_anomalies_run_idx      ON agent_anomalies (run_id);
CREATE INDEX IF NOT EXISTS agent_anomalies_severity_idx ON agent_anomalies (severity);

CREATE TABLE IF NOT EXISTS model_recommendations (
    registry_id                  text PRIMARY KEY REFERENCES ai_registry(id) ON DELETE CASCADE,
    run_id                       bigint REFERENCES discovery_runs(id),
    task_type                    text,
    current_model                text,
    recommended_model            text,
    current_score                numeric(5,2),
    recommended_score            numeric(5,2),
    est_monthly_cost_current     numeric(12,2),
    est_monthly_cost_recommended numeric(12,2),
    rationale                    text,
    alternatives                 jsonb NOT NULL DEFAULT '[]',
    outcome                      text CHECK (outcome IN ('keep', 'downgrade', 'upgrade', 'residency', 'switch')),
    created_at                   timestamptz NOT NULL DEFAULT now()
);
